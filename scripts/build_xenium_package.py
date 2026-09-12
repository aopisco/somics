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
    "cells.zarr.zip",
    "cell_feature_matrix.zarr.zip",
    "experiment.xenium",
    "metrics_summary.csv",
    "gene_panel.json",
    "morphology_focus.ome.tif",
    "morphology_mip.ome.tif",
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


# Per-sample ``files`` keys -> the names the builder reads them under.
FILE_DESTS = {
    "cells": "cells.parquet",
    "matrix": "cell_feature_matrix.h5",
    "cells_zarr": "cells.zarr.zip",
    "matrix_zarr": "cell_feature_matrix.zarr.zip",
    "experiment": "experiment.xenium",
    "gene_panel": "gene_panel.json",
    "metrics": "metrics_summary.csv",
    "zstack": "morphology.ome.tiff",
    "focus": "morphology_focus.ome.tif",
}


def sources_for(spec: dict, sample: str) -> list[tuple[str, str]]:
    """``(url, relative destination)`` pairs for the sample.

    A 10x bundle is one outs zip (the runner extracts the members). A HuBMAP
    submission lists the bundle's files individually under ``files`` -- S3 URIs
    the runner copies straight to the names in ``FILE_DESTS``.
    """
    files = spec["samples"][sample].get("files")
    if files:
        return [(uri, FILE_DESTS[k]) for k, uri in files.items() if k in FILE_DESTS]
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


# 10x's "Explorer" bundles (the _xe_outs.zip on some catalogue pages) and Atera's
# preproduction bundle ship cells and the count matrix only as zarr archives.
# They are rewritten into the cells.parquet / cell_feature_matrix.h5 pair the
# rest of this builder reads, so nothing downstream learns a second layout.
ZARR_FEATURE_TYPES = {
    "gene": "Gene Expression",
    "negative_control_probe": "Negative Control Probe",
    "negative_control_codeword": "Negative Control Codeword",
    "unassigned_codeword": "Unassigned Codeword",
    "deprecated_codeword": "Deprecated Codeword",
    "genomic_control": "Genomic Control",
    "protein": "Protein Expression",  # In Situ Gene and Protein Expression bundles
}
PROTEIN_FEATURE_TYPE = "Protein Expression"
# 10x's cell_id string is the uint32 id in shifted hex (a=0 .. p=15) plus the
# dataset suffix: (27196, 1) -> "aaaagkdm-1". Checked against cells.parquet.
_SHIFTED_HEX = "abcdefghijklmnop"


def encode_cell_id(number: int, suffix: int) -> str:
    return "".join(_SHIFTED_HEX[int(ch, 16)] for ch in f"{number:08x}") + f"-{suffix}"


def materialize_zarr_bundle(src: str) -> None:
    """Write cells.parquet and cell_feature_matrix.h5 from the zarr archives if absent."""
    import scipy.sparse as sp
    import zarr

    parquet = os.path.join(src, "cells.parquet")
    h5 = os.path.join(src, "cell_feature_matrix.h5")
    cells_zip = os.path.join(src, "cells.zarr.zip")
    matrix_zip = os.path.join(src, "cell_feature_matrix.zarr.zip")
    if (os.path.exists(parquet) and os.path.exists(h5)) or not (
        os.path.exists(cells_zip) and os.path.exists(matrix_zip)
    ):
        return
    print("  materializing cells.parquet and cell_feature_matrix.h5 from the zarr archives")
    cells = zarr.open(zarr.storage.ZipStore(cells_zip, mode="r"), mode="r")
    matrix = zarr.open(zarr.storage.ZipStore(matrix_zip, mode="r"), mode="r")
    feats = matrix["cell_features"]
    ids = np.asarray(cells["cell_id"][:])
    if not np.array_equal(ids, np.asarray(feats["cell_id"][:])):
        raise ValueError(
            f"{src}: cells.zarr and cell_feature_matrix.zarr list cells in different orders"
        )
    barcodes = np.array([encode_cell_id(int(n), int(x)) for n, x in ids])

    types = np.array(feats.attrs["feature_types"])
    keep = np.array([t in ZARR_FEATURE_TYPES for t in types])  # drops 'aggregate_gene'
    n_feat, n_cell = len(types), len(ids)
    # cell_features is feature-major (indptr over features); 10x's h5 is CSC
    # over (features x cells) with indptr over cells.
    by_feature = sp.csr_matrix(
        (
            np.asarray(feats["data"][:]),
            np.asarray(feats["indices"][:]),
            np.asarray(feats["indptr"][:]),
        ),
        shape=(n_feat, n_cell),
    )[keep]
    csc = by_feature.tocsc()
    csc.sort_indices()
    feature_ids = np.array(feats.attrs["feature_ids"])[keep]
    feature_keys = np.array(feats.attrs["feature_keys"])[keep]
    feature_types = np.array([ZARR_FEATURE_TYPES[t] for t in types[keep]])

    with h5py.File(h5 + ".part", "w") as f:
        g = f.create_group("matrix")
        g.create_dataset("data", data=csc.data.astype(np.int32), compression="gzip")
        g.create_dataset("indices", data=csc.indices.astype(np.int64), compression="gzip")
        g.create_dataset("indptr", data=csc.indptr.astype(np.int64), compression="gzip")
        g.create_dataset("shape", data=np.array(csc.shape, dtype=np.int32))
        g.create_dataset("barcodes", data=barcodes.astype("S"), compression="gzip")
        fg = g.create_group("features")
        fg.create_dataset("id", data=feature_ids.astype("S"))
        fg.create_dataset("name", data=feature_keys.astype("S"))
        fg.create_dataset("feature_type", data=feature_types.astype("S"))
        fg.create_dataset("genome", data=np.array([""] * len(feature_ids)).astype("S"))
        fg.create_dataset("_all_tag_keys", data=np.array(["genome"]).astype("S"))
    os.replace(h5 + ".part", h5)

    summary = np.asarray(cells["cell_summary"][:])
    columns = list(cells["cell_summary"].attrs["column_names"])
    summary = pd.DataFrame(summary, columns=columns)
    per_type = {
        t: np.asarray(csc[feature_types == label].sum(axis=0)).ravel()
        for t, label in ZARR_FEATURE_TYPES.items()
    }
    table = pd.DataFrame(
        {
            "cell_id": barcodes,
            "x_centroid": summary["cell_centroid_x"].to_numpy(),
            "y_centroid": summary["cell_centroid_y"].to_numpy(),
            "transcript_counts": per_type["gene"],
            "control_probe_counts": per_type["negative_control_probe"],
            "genomic_control_counts": per_type["genomic_control"],
            "control_codeword_counts": per_type["negative_control_codeword"],
            "unassigned_codeword_counts": per_type["unassigned_codeword"],
            "deprecated_codeword_counts": per_type["deprecated_codeword"],
            "total_counts": np.asarray(csc.sum(axis=0)).ravel(),
            "cell_area": summary["cell_area"].to_numpy(),
            "nucleus_area": summary["nucleus_area"].to_numpy()
            if "nucleus_area" in summary
            else np.nan,
        }
    )
    for extra in ("nucleus_count", "z_level"):
        if extra in summary:
            table[extra] = summary[extra].to_numpy()
    table.to_parquet(parquet + ".part")
    os.replace(parquet + ".part", parquet)


CONTROL_TARGET_RE = re.compile(r"isotype|control|blank|unassigned", re.IGNORECASE)


def split_protein_features(sample: str, h5_path: str, var: pd.DataFrame, out_dir: str):
    """On a co-detection bundle, carve the protein rows out into a second feature space.

    The feature axis of an "In Situ Gene and Protein Expression" bundle mixes
    the gene panel (and its controls) with ~27 antibody targets typed "Protein
    Expression". The atlas keeps proteins in ``protein_abundance``, so the
    package gets a gene-only ``cell_feature_matrix.h5`` plus, as CosMx does, a
    dense per-cell protein table, a minimal protein obs carrying the join key,
    and a protein var. Returns None when the bundle has no protein features.
    """
    import scipy.sparse as sp

    is_protein = (var.feature_type == PROTEIN_FEATURE_TYPE).to_numpy()
    if not is_protein.any():
        return None
    with h5py.File(h5_path, "r") as f:
        g = f["matrix"]
        m = sp.csc_matrix(
            (g["data"][:], g["indices"][:], g["indptr"][:]), shape=tuple(g["shape"][:])
        )
        barcodes = g["barcodes"][:].astype(str)
        feats = {k: g["features"][k][:] for k in g["features"] if k != "_all_tag_keys"}
    print(f"  {int(is_protein.sum())} protein targets split into protein_abundance")
    dense = np.asarray(m[is_protein].todense()).T  # cells x proteins
    targets = var.gene_name[is_protein].tolist()
    pd.DataFrame(dense, columns=targets).to_csv(
        os.path.join(out_dir, f"{sample}_protein_intensity.csv"), index=False
    )
    pd.DataFrame(
        {"barcode": barcodes, "obs_index": np.arange(len(barcodes)), "source_obs_id": barcodes}
    ).to_csv(os.path.join(out_dir, f"{sample}_protein_obs.csv"), index=False)
    pd.DataFrame(
        {
            "var_index": targets,
            "target_name": targets,
            "feature_id": var.gene_id[is_protein].tolist(),
            "is_stain": False,
            # ProteinSchema requires is_control (non-nullable; run 4 skipped both
            # co-detection bundles without it). Antibody targets are measurements;
            # isotype / blank entries, if a panel carries them, are controls.
            "is_control": [bool(CONTROL_TARGET_RE.search(t)) for t in targets],
        }
    ).to_csv(os.path.join(out_dir, f"{sample}_protein_var.csv"), index=False)
    n_ctrl = sum(bool(CONTROL_TARGET_RE.search(t)) for t in targets)
    print(f"  protein var: {len(targets)} targets, {n_ctrl} flagged is_control")

    keep = ~is_protein
    gene_only = m[keep]
    gene_only.sort_indices()
    out = os.path.join(out_dir, "cell_feature_matrix.h5")
    with h5py.File(out + ".part", "w") as f:
        g = f.create_group("matrix")
        g.create_dataset("data", data=gene_only.data, compression="gzip")
        g.create_dataset("indices", data=gene_only.indices.astype(np.int64), compression="gzip")
        g.create_dataset("indptr", data=gene_only.indptr.astype(np.int64), compression="gzip")
        g.create_dataset("shape", data=np.array(gene_only.shape, dtype=np.int32))
        g.create_dataset("barcodes", data=barcodes.astype("S"), compression="gzip")
        fg = g.create_group("features")
        for k, v in feats.items():
            fg.create_dataset(k, data=v[keep] if len(v) == len(keep) else v)
        fg.create_dataset("_all_tag_keys", data=np.array(["genome"]).astype("S"))
    os.replace(out + ".part", out)
    return {"targets": targets}


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
        print(
            f"  max-projecting {n_pages} z planes ({height}, {width}) {dtype} "
            "-> morphology_focus.ome.tif"
        )
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
    # 1.x Explorer bundles ship the DAPI max-intensity projection under this
    # name and no focus image; it is the same kind of single-channel projection.
    mip = os.path.join(src, "morphology_mip.ome.tif")
    if os.path.exists(mip):
        return mip, None
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


# Informational members a submission may leave out without the package suffering.
OPTIONAL_PASSTHROUGH = {"metrics_summary.csv"}

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

    materialize_zarr_bundle(src)
    with open(os.path.join(src, "experiment.xenium")) as handle:
        experiment = json.load(handle)
    pixel_size = float(experiment["pixel_size"])

    cells = pq.read_table(os.path.join(src, "cells.parquet")).to_pandas()
    cells["cell_id"] = cells["cell_id"].astype(str)
    # Onboard Analysis 1.0 numbered cells 1..N; a bare "10" round-trips through
    # the CSV staging as an int and fails the obs schema's string field, so
    # numeric ids are namespaced with the sample (as SPRM and MIBI ids are).
    numeric_ids = cells["cell_id"].str.fullmatch(r"\d+").all()
    if numeric_ids:
        cells["cell_id"] = sample + ":" + cells["cell_id"]

    h5_path = os.path.join(src, "cell_feature_matrix.h5")
    var, barcodes = read_h5_features(h5_path)
    if numeric_ids:
        barcodes = np.array([f"{sample}:{b}" for b in barcodes])
    if not np.array_equal(barcodes, cells["cell_id"].to_numpy()):
        raise ValueError(
            f"{sample}: cell_feature_matrix.h5 barcode order does not match cells.parquet row "
            f"for row; ingestion hands the matrix to the writer positionally, so the obs table "
            f"and the matrix would be misaligned"
        )
    protein_files = split_protein_features(sample, h5_path, var, out_dir)
    if protein_files:
        h5_path = os.path.join(out_dir, "cell_feature_matrix.h5")  # gene-only copy
        var, barcodes = read_h5_features(h5_path)
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
    # The control columns vary by Onboard Analysis version (1.0 has no
    # unassigned/deprecated codewords, 3.x adds genomic controls); a column a
    # version does not write counts as zero, and the columns present are kept
    # verbatim in additional_metadata.
    def col(name: str) -> np.ndarray:
        if name in cells.columns:
            return cells[name].to_numpy()
        return np.zeros(len(cells), dtype=np.int64)

    negative_control = (
        col("control_probe_counts") + col("control_codeword_counts") + col("genomic_control_counts")
    )
    unassigned = col("unassigned_codeword_counts") + col("deprecated_codeword_counts")

    obs = pd.DataFrame(
        {
            # On a co-detection bundle both feature spaces' obs tables must share
            # a first-column key of the same type; CosMx uses the barcode string.
            **({"barcode": cells.cell_id.to_numpy()} if protein_files else {}),
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
        if name == "cell_feature_matrix.h5":
            source_path = h5_path  # the gene-only copy on a co-detection bundle
        if not os.path.exists(source_path):
            if name in OPTIONAL_PASSTHROUGH:
                continue  # HuBMAP submissions omit metrics_summary.csv
            raise FileNotFoundError(source_path)
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
        "protein_targets": protein_files["targets"] if protein_files else None,
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
