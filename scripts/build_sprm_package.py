#!/usr/bin/env python3
"""Assemble a HuBMAP Cytokit + SPRM dataset (CODEX or PhenoCycler) into a data
package, driven by a spec file.

HuBMAP runs every CODEX and PhenoCycler submission through one pipeline --
Cytokit stitches, drift-corrects and segments; SPRM measures each cell -- so
the staged datasets under ``s3://somics-dev/hubmap/<HBM-ID>/`` share a layout
and one builder serves all of them. What it reads:

- ``sprm_outputs/*-cell_channel_total.csv``  the summed pixel intensity of each
  antigen channel inside each segmented cell; the protein readout.
- ``sprm_outputs/*-cell_centers.csv``        cell centroids, or the ``xy`` in
  ``anndata-zarr/*.zarr.zip`` where that file exists (see below).
- ``ometiff-pyramids/.../*_expressions.ome.tif`` (CODEX) or ``.../expr/*.ome.tiff``
  (PhenoCycler): the stitched multiplex image, channels first, whose OME-XML
  names the channels and states the pixel size.
- ``experiment.yaml`` where present: Cytokit's acquisition record, used only to
  cross-check the OME pixel size.

Four decisions are made here rather than left to a spec, because they follow
from what SPRM publishes:

1. **The protein matrix is ``cell_x_antigen_total``, rounded.** The atlas's
   ``protein_abundance`` space admits one ``counts`` layer of uint32. SPRM's
   *mean* intensities have a dynamic range of about 0-13 (a mean of 0.49 is
   typical), so rounding them would erase the signal; the *totals* are sums of
   pixel values with a range in the hundreds to tens of thousands, so rounding
   loses at most 0.5 on a value that large. On uint16 images the totals are
   integers already; on uint8 PhenoCycler images SPRM reports fractional totals
   and the rounding is recorded in the geometry file. Means are total / area.

2. **Coordinates come from the AnnData ``xy`` when it exists.** That is SPRM's
   own fractional centroid over exactly the cells it measured. Where the
   AnnData is absent, ``cell_centers.csv`` is used -- and its columns are
   *(row, col)* despite being named ``x, y``: against the AnnData the two agree
   to a few pixels only after the swap, and only the swap keeps every cell
   inside the image. When both are present they are checked against each
   other, and the sample is refused if they disagree by more than a cell.

3. **The full expression stack is kept, rewritten channels-last.** The
   expression image is Cytokit's *extract* -- the antigen channels the
   submitter chose, 11 to ~55 planes -- not the raw per-cycle stack, so there
   is no reason to render a three-channel composite as the Monkman package did
   for its 60-plane source. The atlas's loader boxes the leading spatial axes
   and reads channels in full, so ``(C, Y, X)`` is streamed once into a tiled
   ``(Y, X, C)`` TIFF; ``channel_names`` on the section image names each plane.

4. **Segmentation is the spec's call, from HuBMAP's ``dataset_type``.** Cytokit
   detects nuclei with a U-Net and grows cell boundaries by marker-controlled
   watershed on the membrane channel (``memb_*`` in ``experiment.yaml``), so
   those datasets are ``watershed``; the PhenoCycler submissions were segmented
   with DeepCell's Mesmer instead (``[DeepCell + SPRM]``), which the enum has no
   member for and which is recorded as ``other`` with the method in the audit
   trail. Both pipelines hand SPRM the same mask and SPRM writes the same files.

Nothing about the donor is decided here; that is the spec's, from HuBMAP's own
record. ``cell_area_um2`` stays null: SPRM's ``cell_shape.csv`` is a shape
descriptor, not an area, and the mask is not read.

Run:
    python scripts/build_sprm_package.py --spec specs/sprm/<dataset>.json \\
        [--samples ...] [--skip-image] [--list-sources]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import zipfile

import numpy as np
import pandas as pd
import tifffile

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
TILE = 1024
SLAB_ROWS = 1024
# Channels that measure no antigen: the nuclear counterstain (re-imaged every
# cycle on CODEX, once on PhenoCycler), blank cycles and unused channel slots.
CONTROL_PREFIXES = ("DAPI", "Blank", "Empty", "HOECHST", "Hoechst")
# How far (px) SPRM's integer cell_centers may sit from the AnnData centroid
# before the two are treated as describing different cells.
CENTER_TOLERANCE_PX = 8
# Above the tolerance the gap is recorded rather than refused, up to a cell
# diameter: two HuBMAP small-intestine regions disagree by 12-13 px (~5 um at
# 0.38 um/px) between SPRM's cell_centers.csv and its own AnnData centroids,
# and the AnnData is what the package uses either way.
CENTER_REFUSE_PX = 40.0
DEST_NAMES = {
    "cell_channel_total": "cell_channel_total.csv",
    "cell_centers": "cell_centers.csv",
    "anndata": "anndata.zarr.zip",
    "experiment_yaml": "experiment.yaml",
    "expression_image": "expression.ome.tif",
}


def fetch(uri: str, dest: str) -> str:
    """Copy one S3 object down once. Re-runs are expected while a package is
    being brought up, and the images are gigabytes."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    subprocess.run(["aws", "s3", "cp", uri, tmp, "--only-show-errors"], check=True)
    os.replace(tmp, dest)
    return dest


def sources_for(spec: dict, sample: str) -> list[tuple[str, str]]:
    """``(s3 uri, relative destination)`` pairs a sample needs on disk."""
    files = spec["samples"][sample]["files"]
    prefix = spec["s3_prefix"].rstrip("/")
    return [(f"{prefix}/{files[k]}", DEST_NAMES[k]) for k in DEST_NAMES if k in files]


def read_ome(path: str) -> dict:
    """Channel names, pixel size and geometry from the OME-XML of the stitched image."""
    with tifffile.TiffFile(path) as tif:
        xml = tif.ome_metadata or ""
        series = tif.series[0]
        shape, axes, dtype = series.shape, series.axes, str(series.dtype)
    channels = re.findall(r"<Channel[^>]*?Name=\"([^\"]+)\"", xml)
    pixels = re.search(r"<Pixels[^>]*>", xml)
    if not channels or pixels is None:
        raise ValueError(f"{path}: OME-XML has no channel names or Pixels element")
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', pixels.group(0)))
    size = float(attrs["PhysicalSizeX"])
    unit = attrs.get("PhysicalSizeXUnit", "µm")
    pixel_size_um = size / 1000.0 if unit == "nm" else size
    size_c = int(attrs["SizeC"])
    if len(channels) < size_c:
        # One HuBMAP large-intestine region names 43 channels for 44 planes; the
        # extra plane is not in SPRM's tables either. Name it so the image keeps
        # every plane and the antigen axis comes from SPRM's columns below.
        channels = channels + [f"unnamed_{i}" for i in range(len(channels), size_c)]
    elif len(channels) > size_c:
        raise ValueError(f"{path}: {len(channels)} channel names for SizeC={size_c}")
    if axes not in ("CYX", "IYX", "ZYX", "QYX") or shape[0] != len(channels):
        raise NotImplementedError(f"{path}: expected a channels-first stack, got {axes} {shape}")
    return {
        "channel_names": channels,
        "pixel_size_um": pixel_size_um,
        "width_px": int(attrs["SizeX"]),
        "height_px": int(attrs["SizeY"]),
        "n_channels": len(channels),
        "dtype": dtype,
        "axes": axes,
    }


def read_anndata_xy(zip_path: str, extract_to: str) -> pd.DataFrame | None:
    """SPRM's per-cell centroid ``(x, y)`` indexed by cell id, or None if absent."""
    if not os.path.exists(zip_path):
        return None
    import anndata as ad

    if not os.path.isdir(extract_to):
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_to)
    inner = os.listdir(extract_to)
    root = extract_to if "obs" in inner else os.path.join(extract_to, inner[0])
    adata = ad.read_zarr(root)
    xy = np.asarray(adata.obsm["xy"], dtype=float)
    return pd.DataFrame({"x": xy[:, 0], "y": xy[:, 1]}, index=adata.obs.index.astype(int))


def read_centers(path: str) -> pd.DataFrame:
    """``cell_centers.csv`` as ``(x, y)`` in image pixels -- its ``x`` is the row."""
    centers = pd.read_csv(path).set_index("ID")
    return pd.DataFrame({"x": centers["y"].astype(float), "y": centers["x"].astype(float)})


def rewrite_channels_last(src: str, dest: str, info: dict) -> None:
    """Stream a ``(C, Y, X)`` pyramid's base level into a tiled ``(Y, X, C)`` TIFF.

    Each slab is a ``selection`` read, which decodes only the tiles it covers
    from every channel page; a 22 GB PhenoCycler stack never has to be in
    memory at once. Border tiles are padded, as TIFF requires.
    """
    height, width, n_ch = info["height_px"], info["width_px"], info["n_channels"]
    dtype = np.dtype(info["dtype"])
    print(f"  rewriting {os.path.basename(src)} {info['axes']}{(n_ch, height, width)} as (Y, X, C)")

    def tiles():
        for y0 in range(0, height, SLAB_ROWS):
            y1 = min(y0 + SLAB_ROWS, height)
            block = tifffile.imread(src, selection=(slice(None), slice(y0, y1), slice(None)))
            slab = np.ascontiguousarray(np.moveaxis(block, 0, -1))
            for ty in range(0, y1 - y0, TILE):
                for tx in range(0, width, TILE):
                    tile = slab[ty : ty + TILE, tx : tx + TILE]
                    if tile.shape[:2] != (TILE, TILE):
                        padded = np.zeros((TILE, TILE, n_ch), dtype=dtype)
                        padded[: tile.shape[0], : tile.shape[1]] = tile
                        tile = padded
                    yield tile

    with tifffile.TiffWriter(dest + ".part", bigtiff=True) as writer:
        writer.write(
            tiles(),
            shape=(height, width, n_ch),
            dtype=dtype,
            tile=(TILE, TILE),
            compression="zlib",
            photometric="minisblack",
            planarconfig="contig",
            metadata={"axes": "YXC"},
        )
    os.replace(dest + ".part", dest)
    print(f"  wrote {os.path.basename(dest)} ({os.path.getsize(dest) / 1e9:.2f} GB)")


def build_sample(sample: str, spec: dict, source: str, out_dir: str, *, skip_image: bool) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)
    entry = spec["samples"][sample]
    sample_dir = os.path.join(source, sample)
    for uri, rel in sources_for(spec, sample):
        fetch(uri, os.path.join(sample_dir, rel))

    image = os.path.join(sample_dir, DEST_NAMES["expression_image"])
    info = read_ome(image)
    yaml_path = os.path.join(sample_dir, DEST_NAMES["experiment_yaml"])
    if os.path.exists(yaml_path):
        m = re.search(r"lateral_resolution:\s*([\d.]+)", open(yaml_path).read())
        if m and not np.isclose(float(m.group(1)) / 1000.0, info["pixel_size_um"], rtol=1e-3):
            raise ValueError(
                f"{sample}: experiment.yaml lateral_resolution {m.group(1)} nm disagrees with "
                f"the OME pixel size {info['pixel_size_um'] * 1000:.2f} nm"
            )

    totals = pd.read_csv(os.path.join(sample_dir, DEST_NAMES["cell_channel_total"])).set_index("ID")
    # A plane the OME header leaves unnamed is one SPRM still measured, under its
    # own placeholder ("Channel:0:43"); give the image plane that name so the
    # two axes agree, then the antigen axis below is exactly SPRM's columns.
    unnamed = [c for c in info["channel_names"] if c.startswith("unnamed_")]
    extra = sorted(set(totals.columns) - set(info["channel_names"]))
    if unnamed and len(unnamed) == len(extra):
        renames = dict(zip(unnamed, extra, strict=True))
        info["channel_names"] = [renames.get(c, c) for c in info["channel_names"]]
        print(f"  unnamed image plane(s) named from SPRM's tables: {renames}")
    if not set(totals.columns) <= set(info["channel_names"]):
        raise ValueError(
            f"{sample}: SPRM channels {sorted(set(totals.columns) - set(info['channel_names']))} "
            f"are not in the image's {sorted(info['channel_names'])}"
        )
    # Matrix columns in image channel order, so var.channel_index is the plane.
    # The antigen axis is what SPRM measured; an image plane SPRM did not
    # tabulate stays in the image (and its channel_names) only.
    measured = [c for c in info["channel_names"] if c in set(totals.columns)]
    totals = totals[measured]
    if totals.isna().any().any():
        raise ValueError(f"{sample}: null intensities in cell_channel_total")
    ids = totals.index.to_numpy()

    xy = read_anndata_xy(
        os.path.join(sample_dir, DEST_NAMES["anndata"]), os.path.join(sample_dir, "anndata.zarr")
    )
    centers_path = os.path.join(sample_dir, DEST_NAMES["cell_centers"])
    centers = read_centers(centers_path) if os.path.exists(centers_path) else None
    if xy is not None and set(xy.index) != set(ids):
        raise ValueError(f"{sample}: AnnData cells differ from cell_channel_total rows")
    if xy is not None and centers is not None:
        both = centers.reindex(ids)
        gap = float(np.nanmax(np.abs(both.to_numpy() - xy.reindex(ids).to_numpy())))
        if gap > CENTER_REFUSE_PX:
            raise ValueError(
                f"{sample}: cell_centers and the AnnData centroids disagree by {gap:.1f} px; "
                f"the row/col convention assumed for cell_centers does not hold here"
            )
        if gap > CENTER_TOLERANCE_PX:
            print(
                f"  note: cell_centers and AnnData centroids disagree by {gap:.1f} px "
                "(AnnData used)"
            )
    else:
        gap = None
    if xy is not None:
        coords, coordinate_source = xy.reindex(ids), "anndata obsm['xy']"
    elif centers is not None:
        coords, coordinate_source = centers.reindex(ids), "cell_centers.csv (row, col swapped)"
        if coords.isna().any().any():
            raise ValueError(f"{sample}: cells in cell_channel_total absent from cell_centers")
    else:
        raise FileNotFoundError(f"{sample}: neither AnnData nor cell_centers.csv present")

    x_px = coords["x"].to_numpy(dtype=float)
    y_px = coords["y"].to_numpy(dtype=float)
    if x_px.max() >= info["width_px"] or y_px.max() >= info["height_px"] or x_px.min() < 0:
        raise ValueError(
            f"{sample}: centroids extend to ({x_px.max():.0f}, {y_px.max():.0f}) px but the "
            f"image is {info['width_px']}x{info['height_px']}; not the frame they were written in"
        )

    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(ids), dtype=np.int64),
            # SPRM cell ids are integers unique within the region; the atlas
            # selects by dataset_uid, so they need not be unique beyond it.
            # SPRM cell ids are integers; a bare "200" round-trips through the CSV
            # staging as an int and fails the obs schema's string field. Namespace
            # them with the region name, as the MIBI builder does with mask labels.
            "source_obs_id": [f"{sample}:{i}" for i in ids],
            "x_um": x_px * info["pixel_size_um"],
            "y_um": y_px * info["pixel_size_um"],
            "x_px": x_px,
            "y_px": y_px,
            "pixel_size_um": info["pixel_size_um"],
            "section_id": entry["section_id"],
            "donor_id": entry["donor_id"],
        }
    )
    obs["source_extras_json"] = json.dumps(
        {"region": sample, "coordinate_source": coordinate_source}
    )
    obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
    print(f"  wrote {sample}_obs.csv: {len(obs)} cells ({coordinate_source})")

    pd.DataFrame(
        {
            "var_index": measured,
            "target_name": measured,
            "is_control": [c.startswith(CONTROL_PREFIXES) for c in measured],
            "channel_index": [info["channel_names"].index(c) for c in measured],
        }
    ).to_csv(os.path.join(out_dir, f"{sample}_var.csv"), index=False)

    values = totals.to_numpy(dtype=float)
    rounded = np.rint(values)
    fraction_fractional = float(np.mean(values != rounded))
    max_rounding_loss = float(np.abs(values - rounded).max())
    pd.DataFrame(rounded.astype(np.uint32), columns=measured).to_csv(
        os.path.join(out_dir, f"{sample}_protein_intensity.csv"), index=False
    )
    print(
        f"  wrote {sample}_protein_intensity.csv: {len(ids)} x {len(measured)} "
        f"(fraction of values rounded: {fraction_fractional:.3f})"
    )

    image_file = f"{sample}_{spec.get('image_modality', 'immunofluorescence')}_image.tif"
    dest = os.path.join(out_dir, image_file)
    if not skip_image and not os.path.exists(dest):
        rewrite_channels_last(image, dest, info)

    uuid = spec["uuid"]
    rel = spec["samples"][sample]["files"]["expression_image"]
    return {
        "sample": sample,
        "image_file": image_file,
        "image_source": f"https://assets.hubmapconsortium.org/{uuid}/{rel}",
        "image_built": os.path.exists(dest),
        "n_channels": info["n_channels"],
        "channel_names": info["channel_names"],
        "n_targets": int(sum(not c.startswith(CONTROL_PREFIXES) for c in measured)),
        "image_dtype": info["dtype"],
        "pixel_size_um": info["pixel_size_um"],
        "height_px": info["height_px"],
        "width_px": info["width_px"],
        "n_cells": int(len(ids)),
        "coordinate_source": coordinate_source,
        "centroid_gap_px": gap,
        "fraction_values_rounded": fraction_fractional,
        "max_rounding_loss": max_rounding_loss,
        "section_id": entry["section_id"],
        "donor_id": entry["donor_id"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--samples", nargs="*")
    parser.add_argument("--source")
    parser.add_argument("--out")
    parser.add_argument("--skip-image", action="store_true", help="derive tables only")
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="print '<s3 uri>\\t<destination path>' per source file and exit",
    )
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec.get("dataset_key") or os.path.splitext(os.path.basename(args.spec))[0]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")

    samples = args.samples or list(spec["samples"])
    unknown = [s for s in samples if s not in spec["samples"]]
    if unknown:
        raise SystemExit(f"not in {args.spec}: {unknown}")
    if args.list_sources:
        for sample in samples:
            for uri, rel in sources_for(spec, sample):
                print(f"{uri}\t{os.path.join(source, sample, rel)}")
        return

    os.makedirs(out, exist_ok=True)
    summary = [
        build_sample(s, spec, source, os.path.join(out, s), skip_image=args.skip_image)
        for s in samples
    ]
    with open(os.path.join(out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    print(f"\n{len(summary)} sample(s), {sum(e['n_cells'] for e in summary)} cells")
    if shutil.which("aws") is None:
        print("note: the aws CLI was not on PATH; sources must already be on disk")


if __name__ == "__main__":
    main()
