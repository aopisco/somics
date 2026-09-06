#!/usr/bin/env python3
"""Assemble any 10x Xenium ``outs`` bundle into a data package.

``build_xenium_lung_package.py`` reads a Xenium bundle perfectly well but is
welded to two specific samples: their folder names, donors, disease states and
panel live in module constants. A Xenium ``outs`` bundle has a fixed layout, so
the reading half generalises to every Xenium dataset we hold; only the curation
does not, because no bundle states which donor a section came from or whether
the tissue was diseased.

This script is that split. The reader is the same code; everything a bundle
cannot tell you comes from a spec file:

    {
      "study": "Xenium_Lung_Preview",
      "panel_name": "Xenium Human Lung Panel v1 + hLung_100g Add-On",
      "samples": {
        "<the 10x folder name>": {
          "section": "non_diseased",
          "donor": "non_diseased",
          "disease_state": "healthy",
          "disease": null
        }
      }
    }

``section`` and ``donor`` are namespaced with ``study`` to build ``section_id``
and ``donor_id``, which is what makes their uids stable and collision-free
across datasets. ``disease`` is null for healthy tissue rather than absent —
the schema keeps "healthy" distinct from "unannotated", and a builder that
silently omitted it would collapse the two.

Checks that stay, because they catch misalignment that would otherwise surface
as quietly wrong data: the h5 barcode order must match ``cells.parquet`` row for
row, since ingestion hands the matrix to the writer positionally; and centroids
must fall inside the morphology image, since the micron and pixel frames are
only related by ``pixel_size`` if they do.

Run:
    python scripts/build_xenium_package.py --spec specs/<dataset>.json \\
        [--samples ...] [--source DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil

import h5py
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import tifffile

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

# Files copied into the package unchanged.
PASSTHROUGH = (
    "cell_feature_matrix.h5",
    "morphology_focus.ome.tif",
    "experiment.xenium",
    "metrics_summary.csv",
)

# The members a builder needs out of an outs bundle; everything else (transcripts,
# boundaries, the zarr copies, the full morphology z-stack) is unused.
BUNDLE_MEMBERS = (
    "cells.parquet",
    "cell_feature_matrix.h5",
    "experiment.xenium",
    "metrics_summary.csv",
    "gene_panel.json",
    "morphology_focus.ome.tif",
    "morphology_focus/*",
)

# Xenium Onboard Analysis 2.0+ writes the focus image as one file per channel.
# Indices 0-3 are fixed by the multimodal segmentation kit; 4.0 protein bundles
# name the channel in the file (ch0008_cd4.ome.tif) and those names are used.
FOCUS_CHANNEL_NAMES = {
    0: "DAPI",
    1: "ATP1A1/CD45/E-Cadherin",
    2: "18S",
    3: "alphaSMA/Vimentin",
}


def sources_for(spec: dict, sample: str) -> list[tuple[str, str]]:
    """``(url, relative destination)`` for the sample's outs bundle."""
    url = spec["download_url"].format(sample=sample)
    return [(url, f"{sample}_outs.zip")]


def find_json_key(obj, key: str):
    """First value of ``key`` anywhere in a nested JSON document, or None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = find_json_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_json_key(v, key)
            if found is not None:
                return found
    return None


STACK_TILE = 1024


def plane_view(path: str):
    """The file's own full-resolution plane as a lazily sliceable zarr array.

    2.0+ channel files are OME companions: each file's OME-XML references its
    siblings, so ``TiffFile.series[0]`` presents the whole (C, Y, X) stack and
    reading through it pulls every file. Page 0 of each file is that channel's
    plane (tiled, JPEG 2000; the pyramid lives in SubIFDs), so that is what is
    read, one tile row at a time.
    """
    import zarr

    return zarr.open(tifffile.imread(path, key=0, aszarr=True), mode="r")


def stream_stack(paths: list[str], out: str, height: int, width: int, dtype) -> None:
    """Write channel files as one tiled (Y, X, C) BigTIFF without holding them in memory.

    A 3.0 bundle's four full-resolution channels are 3.3 GB each decoded;
    stacking them with ``np.stack`` needs two copies and killed the first build
    silently (OOM). Reading one tile-row slab per channel at a time keeps the
    peak at a slab: 1024 rows x width x channels x 2 bytes (~440 MB here).
    """
    n = len(paths)
    shape = (height, width, n) if n > 1 else (height, width)
    views = [plane_view(p) for p in paths]

    def tiles():
        for y0 in range(0, height, STACK_TILE):
            y1 = min(y0 + STACK_TILE, height)
            planes = [np.asarray(v[y0:y1, :]) for v in views]
            slab = np.stack(planes, axis=-1) if n > 1 else planes[0]
            for x0 in range(0, width, STACK_TILE):
                yield np.ascontiguousarray(slab[:, x0 : x0 + STACK_TILE])

    with tifffile.TiffWriter(out + ".part", bigtiff=True) as writer:
        writer.write(
            tiles(),
            shape=shape,
            dtype=dtype,
            tile=(STACK_TILE, STACK_TILE),
            compression="zlib",
            photometric="minisblack",
            planarconfig="contig" if n > 1 else None,
            metadata={"axes": "YXC" if n > 1 else "YX"},
        )
    os.replace(out + ".part", out)


def stream_max_projection(path: str, out: str) -> None:
    """Max-project a (Z, Y, X) multi-page TIFF into a tiled (Y, X) BigTIFF, slab by slab."""
    import zarr

    with tifffile.TiffFile(path) as tif:
        n_pages = len(tif.pages)
        height, width = (int(d) for d in tif.pages[0].shape[:2])
        dtype = tif.pages[0].dtype
    views = [zarr.open(tifffile.imread(path, key=z, aszarr=True), mode="r") for z in range(n_pages)]
    print(
        f"  max-projecting {n_pages} z planes ({height}, {width}) {dtype} -> morphology_focus.ome.tif"
    )

    def tiles():
        for y0 in range(0, height, STACK_TILE):
            y1 = min(y0 + STACK_TILE, height)
            slab = np.asarray(views[0][y0:y1, :])
            for v in views[1:]:
                np.maximum(slab, np.asarray(v[y0:y1, :]), out=slab)
            for x0 in range(0, width, STACK_TILE):
                yield np.ascontiguousarray(slab[:, x0 : x0 + STACK_TILE])

    with tifffile.TiffWriter(out + ".part", bigtiff=True) as writer:
        writer.write(
            tiles(),
            shape=(height, width),
            dtype=dtype,
            tile=(STACK_TILE, STACK_TILE),
            compression="zlib",
            photometric="minisblack",
            metadata={"axes": "YX"},
        )
    os.replace(out + ".part", out)


def focus_image(src: str, out_dir: str) -> tuple[str, list[str] | None]:
    """The section image as one (Y, X[, C]) TIFF, plus channel names if stacked.

    Before 2.0 the bundle carries ``morphology_focus.ome.tif`` and that is used
    as-is (single DAPI channel). From 2.0 the focus image is a directory of one
    OME-TIFF per channel; those are read at full resolution and written once as
    a tiled (Y, X, C) BigTIFF, which is the layout the atlas's slab loader boxes.
    """
    single = os.path.join(src, "morphology_focus.ome.tif")
    if os.path.exists(single):
        return single, None
    folder = os.path.join(src, "morphology_focus")
    zstack = os.path.join(src, "morphology.ome.tiff")
    if not os.path.isdir(folder) and not os.path.exists(zstack):
        zstack = os.path.join(src, "morphology.ome.tif")
    if not os.path.isdir(folder) and os.path.exists(zstack):
        # HuBMAP's Xenium submissions ship the DAPI z-stack (Z, Y, X) and no
        # focus projection; a max projection over Z is the closest equivalent,
        # streamed slab by slab (14 planes of 51k x 54k would be 77 GB decoded).
        out = os.path.join(out_dir, "morphology_focus.ome.tif")
        if not os.path.exists(out):
            stream_max_projection(zstack, out)
        return out, None
    files = sorted(f for f in os.listdir(folder) if f.endswith((".ome.tif", ".ome.tiff", ".tif")))
    if not files:
        raise FileNotFoundError(
            f"{src}: neither morphology_focus.ome.tif nor a morphology_focus/ directory"
        )
    names = []
    for f in files:
        m = re.match(r"(?:morphology_focus_|ch)(\d{4})(?:_(.+?))?\.ome\.tiff?$", f)
        if m and m.group(2):
            names.append(m.group(2).replace("_", "/"))
        elif m:
            names.append(FOCUS_CHANNEL_NAMES.get(int(m.group(1)), f"channel_{int(m.group(1))}"))
        else:
            names.append(os.path.splitext(f)[0])
    stacked = os.path.join(out_dir, "morphology_focus.ome.tif")
    if not os.path.exists(stacked):
        paths = [os.path.join(folder, f) for f in files]
        with tifffile.TiffFile(paths[0]) as tif:
            page = tif.pages[0]
            height, width = (int(d) for d in page.shape[:2])
            dtype = page.dtype
        for pth in paths[1:]:
            with tifffile.TiffFile(pth) as tif:
                if tuple(tif.pages[0].shape[:2]) != (height, width):
                    raise ValueError(f"{pth}: channel shape differs from {paths[0]}")
        print(
            f"  stacking {len(paths)} focus channel(s) {names} -> "
            f"({height}, {width}, {len(paths)}) {dtype}"
        )
        stream_stack(paths, stacked, height, width, dtype)
    return stacked, names


# The feature axis is one panel plus its controls; only these are real genes.
GENE_FEATURE_TYPE = "Gene Expression"


def read_h5_features(path: str) -> tuple[pd.DataFrame, np.ndarray]:
    """The feature table in matrix-column order, and the barcode order."""
    with h5py.File(path, "r") as handle:
        group = handle["matrix"]
        features = group["features"]
        var = pd.DataFrame(
            {
                "gene_id": np.asarray(features["id"]).astype(str),
                "gene_name": np.asarray(features["name"]).astype(str),
                "feature_type": np.asarray(features["feature_type"]).astype(str),
                "genome": np.asarray(features["genome"]).astype(str),
            }
        )
        barcodes = np.asarray(group["barcodes"]).astype(str)
    return var, barcodes


def genes_per_cell(path: str, is_gene: np.ndarray, n_cells: int) -> np.ndarray:
    """Distinct real genes detected per cell.

    The file is CSC over (features x cells), which is CSR over (cells x
    features) with the same three arrays — so a row's feature indices are one
    contiguous slice of ``indices`` and the count is a masked sum over it. Read
    without materializing the matrix, which is 14.5M nonzeros for a single
    modest section.
    """
    with h5py.File(path, "r") as handle:
        group = handle["matrix"]
        indices = np.asarray(group["indices"])
        indptr = np.asarray(group["indptr"])
    if len(indptr) != n_cells + 1:
        raise ValueError(f"{path}: indptr implies {len(indptr) - 1} cells, obs has {n_cells}")
    flags = is_gene[indices].astype(np.int64)
    totals = np.add.reduceat(np.concatenate([flags, [0]]), indptr[:-1])
    totals[np.diff(indptr) == 0] = 0
    return totals.astype(np.int32)


def build_sample(sample: str, spec: dict, study: str, panel: str, src: str, out_dir: str) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(src, "experiment.xenium")) as handle:
        experiment = json.load(handle)
    pixel_size = float(experiment["pixel_size"])

    cells = pq.read_table(os.path.join(src, "cells.parquet")).to_pandas()
    cells["cell_id"] = cells["cell_id"].astype(str)

    h5_path = os.path.join(src, "cell_feature_matrix.h5")
    var, barcodes = read_h5_features(h5_path)
    if not np.array_equal(barcodes, cells["cell_id"].to_numpy()):
        raise ValueError(
            f"{sample}: cell_feature_matrix.h5 barcode order does not match cells.parquet row "
            f"for row; ingestion hands the matrix to the writer positionally, so the obs table "
            f"and the matrix would be misaligned"
        )
    is_gene = (var.feature_type == GENE_FEATURE_TYPE).to_numpy()
    n_genes = genes_per_cell(h5_path, is_gene, len(cells))

    image, channel_names = focus_image(src, out_dir)
    with tifffile.TiffFile(image) as tif:
        level = tif.series[0].levels[0]
        height, width = (int(d) for d in level.shape[:2])

    # gene_panel.json describes one panel design: payload.panel.identity.name,
    # with type.descriptor 'predesigned' or 'add_on'. On an add-on run it is the
    # add-on that is described (100 targets of a 480-gene axis), so it names the
    # bundle's panel only partially and the catalogue name stays primary.
    gene_panel_path = os.path.join(src, "gene_panel.json")
    bundle_panel_name = None
    if os.path.exists(gene_panel_path):
        with open(gene_panel_path) as handle:
            gp = json.load(handle)
        identity = find_json_key(gp, "identity") or {}
        descriptor = (
            (find_json_key(gp, "type") or {}).get("descriptor")
            if isinstance(find_json_key(gp, "type"), dict)
            else None
        )
        name = identity.get("name") if isinstance(identity, dict) else None
        if name:
            bundle_panel_name = f"{name} ({descriptor})" if descriptor else name
    panel = panel or bundle_panel_name

    x_px = cells.x_centroid.to_numpy() / pixel_size
    y_px = cells.y_centroid.to_numpy() / pixel_size
    if x_px.max() >= width or y_px.max() >= height or x_px.min() < 0 or y_px.min() < 0:
        raise ValueError(
            f"{sample}: centroids fall outside the morphology image "
            f"({y_px.min():.0f}..{y_px.max():.0f} of {height} rows, "
            f"{x_px.min():.0f}..{x_px.max():.0f} of {width} cols); the micron and pixel frames "
            f"are not related by pixel_size alone"
        )

    # 10x splits non-gene signal three ways. Negative controls report probe and
    # codeword failure; unassigned codewords are transcripts decoded into panel
    # space that were never assigned a target. The atlas keeps that split.
    negative_control = (
        cells.control_probe_counts.to_numpy() + cells.control_codeword_counts.to_numpy()
    )
    if "genomic_control_counts" in cells.columns:  # Onboard Analysis 3.x+ / 5K panels
        negative_control = negative_control + cells.genomic_control_counts.to_numpy()
    unassigned = cells.unassigned_codeword_counts.to_numpy()
    if "deprecated_codeword_counts" in cells.columns:
        unassigned = unassigned + cells.deprecated_codeword_counts.to_numpy()

    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(cells), dtype=np.int64),
            "source_obs_id": cells.cell_id.to_numpy(),
            "x_um": cells.x_centroid.to_numpy(),
            "y_um": cells.y_centroid.to_numpy(),
            "x_px": x_px,
            "y_px": y_px,
            "pixel_size_um": pixel_size,
            "n_counts": cells.transcript_counts.to_numpy(),
            "n_genes": n_genes,
            "negative_control_counts": negative_control,
            "unassigned_counts": unassigned,
            "cell_area_um2": cells.cell_area.to_numpy(),
            "nucleus_area_um2": (
                cells.nucleus_area.to_numpy() if "nucleus_area" in cells.columns else np.nan
            ),
            "total_counts": cells.total_counts.to_numpy(),
            # Verbatim when the spec states them. A published section's uid is
            # a content hash of exactly this string, so a rebuild only reproduces
            # it if the string matches character for character -- and not every
            # study follows the study-prefixed convention.
            "section_id": spec.get("section_id") or f"{study}_{spec['section']}",
            "donor_id": spec.get("donor_id") or f"{study}_{spec['donor']}",
            "panel_name": panel,
        }
    )

    # Source columns with no schema field of their own, kept verbatim so nothing
    # the vendor measured is lost when the leftovers are dropped.
    extras = [
        "control_probe_counts",
        "control_codeword_counts",
        "unassigned_codeword_counts",
        "total_counts",
        # 2.0+ records how each cell was segmented (nucleus expansion vs a
        # boundary or interior stain); kept verbatim beside the study-level
        # segmentation_method the spec states.
        "segmentation_method",
        "nucleus_count",
        "z_level",
        "genomic_control_counts",
    ]
    extras = [c for c in extras if c in cells.columns]
    obs["source_extras_json"] = [
        json.dumps(record) for record in cells[extras].to_dict(orient="records")
    ]

    obs_path = os.path.join(out_dir, f"{sample}_obs.csv")
    obs.to_csv(obs_path, index=False)
    print(f"  wrote {os.path.basename(obs_path)}: {len(obs)} cells, {int(is_gene.sum())} genes")

    var_path = os.path.join(out_dir, "cell_feature_matrix_var.csv")
    var.to_csv(var_path, index=False)
    print(f"  wrote {os.path.basename(var_path)}: {len(var)} features")
    print("   ", var.feature_type.value_counts().to_dict())

    for name in PASSTHROUGH:
        dest = os.path.join(out_dir, name)
        if os.path.exists(dest):
            continue
        source_path = image if name == "morphology_focus.ome.tif" else os.path.join(src, name)
        if os.path.abspath(source_path) == os.path.abspath(dest):
            continue
        try:
            os.link(source_path, dest)
        except OSError:
            shutil.copy2(source_path, dest)

    return {
        "sample": sample,
        "image_file": "morphology_focus.ome.tif",
        "channel_names": channel_names,
        "n_channels": len(channel_names) if channel_names else 1,
        "panel_name": panel,
        "panel_name_from_bundle": bundle_panel_name,
        "n_cells": int(len(cells)),
        "n_genes_panel": int(is_gene.sum()),
        "n_features": int(len(var)),
        "pixel_size_um": pixel_size,
        "height_px": height,
        "width_px": width,
        "median_transcripts_per_cell": int(np.median(cells.transcript_counts)),
        "run_name": experiment["run_name"],
        "run_start_time": experiment["run_start_time"],
        "analysis_sw_version": experiment["analysis_sw_version"],
        "panel_num_targets_predesigned": experiment["panel_num_targets_predesigned"],
        "panel_num_targets_custom": experiment["panel_num_targets_custom"],
        "disease_state": spec["disease_state"],
        "disease": spec["disease"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="dataset spec JSON")
    parser.add_argument("--samples", nargs="*", help="subset of the spec's samples")
    parser.add_argument("--source", help="override the extracted-bundles directory")
    parser.add_argument("--out", help="override the staging directory")
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="print '<url>\\t<destination>' for each sample's outs bundle and exit",
    )
    args = parser.parse_args(argv)

    with open(args.spec) as handle:
        spec = json.load(handle)
    key = spec.get("dataset_key") or os.path.splitext(os.path.basename(args.spec))[0]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")

    samples = args.samples or list(spec["samples"])
    unknown = [s for s in samples if s not in spec["samples"]]
    if unknown:
        raise SystemExit(f"not in {args.spec}: {unknown}")
    if args.list_sources:
        for sample in samples:
            for url, rel in sources_for(spec, sample):
                print(f"{url}\t{os.path.join(source, sample, rel)}")
        return

    os.makedirs(out, exist_ok=True)
    summary = [
        build_sample(
            sample,
            spec["samples"][sample],
            spec["study"],
            # The panel is a bare name in the older specs and an object in the
            # ones the assembler also reads, which needs its vendor and target
            # count. Accept either rather than forcing every spec to be rewritten.
            spec.get("panel_name") or spec["panel"]["panel_name"],
            os.path.join(source, sample),
            os.path.join(out, sample),
        )
        for sample in samples
    ]
    with open(os.path.join(out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    total = sum(entry["n_cells"] for entry in summary)
    print(f"\n{len(summary)} sample(s), {total} cells")


if __name__ == "__main__":
    main()
