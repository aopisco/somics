"""Assemble the monkman2024 NSCLC CODEX release into a polycomb data package.

The Zenodo deposit is three tables and 42 whole-core OME-TIFFs. Turning that into
a package needs four derivations of the *files*, all done here rather than during
harmonization because none of them is a resolution of a value:

1. **The cells are restricted to the published subset.** ``4301_cells.csv`` holds
   225,319 QuPath objects across all 42 cores; the authors' annotated AnnData
   holds 210,945 across 36, having dropped six cores and a scatter of cells in
   QC. Only the annotated cells are packaged, so every row carries a published
   cell type. The two sources join exactly on ``(Image, Object ID)`` and their
   centroids agree to the digit — asserted below, since the cell types would
   otherwise be attached to the wrong cells.

2. **A pixel frame is derived.** The tables give centroids in microns only. Every
   core image is 3024x2688 and the largest centroid in a core sits just inside
   that extent at 0.3775 um/px, the standard 20x CODEX sampling — so
   ``x_px = x_um / 0.3775`` indexes the core image directly, with no flip: QuPath
   and TIFF both put the origin at the top left.

3. **A viewable composite is rendered.** The source image is a 60-channel uint16
   stack, which is neither what ``discrete_image`` crops are for nor something a
   viewer can display. Three channels are taken — CD45, PanCK, DAPI as R, G, B —
   and each is stretched from its own 1st-99.5th percentile into uint8. This is a
   display rendering and is lossy by construction; the raw stack stays at Zenodo.

4. **Intensities are rounded.** QuPath reports the mean pixel value inside each
   cell, which is fractional for 16% of values. The ``protein_abundance`` spec
   admits only a uint32 ``counts`` layer, so the means are rounded to nearest.
   The unrounded values survive in the per-core copy of the source table.

Column names are normalised to SQL-safe identifiers here (``Cell: Area um^2`` ->
``cell_area_um2``) because the curation applicator cannot address the originals.
The values are untouched: the source already reports areas in um^2, so nothing is
converted and no arithmetic enters the package outside the pixel frame above.

Run:
    python scripts/build_monkman_codex_package.py [--regions reg001 ...] [--skip-images]
"""

from __future__ import annotations

import argparse
import json
import os

import anndata as ad
import numpy as np
import pandas as pd
import tifffile

SOURCE_ROOT = "/home/ubuntu/datasets/monkman_nsclc_codex/raw"
# Built files land in staging: the collection's coalesce() moves them into the
# package root, and it refuses to move a file onto itself.
STAGING_ROOT = "/home/ubuntu/datasets/monkman_nsclc_codex/staging"

CELLS_FILE = "4301_cells.csv"
CHANNELS_FILE = "4301_channelnames.csv"
H5AD_FILE = "anndata_4301_v4_annotated.h5ad"

STUDY = "Monkman_NSCLC_CODEX"
PANEL_NAME = "Monkman NSCLC 36-plex CODEX antibody panel"

# 20x CODEX sampling. Checked against the data: the widest core spans 1013.3 um
# over an image 2688 px wide, i.e. 0.3770 um/px, and no centroid in any core
# falls outside the image extent at this scale.
UM_PER_PX = 0.3775
IMAGE_H, IMAGE_W = 3024, 2688

# Composite channels, as (channel name, RGB position). Indices into the stack
# come from channelnames.csv, which lists the 60 planes in acquisition order.
COMPOSITE = [("CD45", 0), ("PanCK", 1), ("DAPI", 2)]
DISPLAY_PERCENTILES = (1.0, 99.5)


# Channels that measure no antigen: the nuclear counterstain re-imaged once per
# cycle, the blank cycles, and the unused channel slots.
def is_control_channel(name: str) -> bool:
    return name.startswith(("DAPI", "Blank", "Empty"))


def region_of(image: str) -> str:
    """'s293_c001_v001_r001_reg007.ome.tiff' -> 'reg007'."""
    return image.split("_")[-1].split(".")[0]


def load_sources() -> tuple[pd.DataFrame, list[str]]:
    """The annotated cells, in source order, with their published labels."""
    channels = pd.read_csv(os.path.join(SOURCE_ROOT, CHANNELS_FILE), header=None)[0].tolist()
    if len(channels) != 60:
        raise ValueError(f"expected 60 channels in {CHANNELS_FILE}, found {len(channels)}")

    cells = pd.read_csv(os.path.join(SOURCE_ROOT, CELLS_FILE))
    markers = list(cells.columns[9:])

    adata = ad.read_h5ad(os.path.join(SOURCE_ROOT, H5AD_FILE))
    labels = (
        adata.obs[
            ["Image", "Object ID", "cell_types", "pheno_leiden", "leiden_group", "cores", "Region"]
        ]
        .astype(str)
        .reset_index(drop=True)
    )
    labels["annot_x"] = np.asarray(adata.obs["x"], dtype=float)
    labels["annot_y"] = np.asarray(adata.obs["y"], dtype=float)

    merged = cells.merge(labels, on=["Image", "Object ID"], how="inner", validate="one_to_one")
    if len(merged) != len(labels):
        raise ValueError(
            f"{len(labels)} annotated cells but {len(merged)} joined onto the cell table; "
            f"the published labels would be attached to the wrong cells"
        )
    # The two files must describe the same cells, not merely share keys.
    if not np.allclose(merged["Centroid X µm"], merged["annot_x"], atol=1e-6) or not np.allclose(
        merged["Centroid Y µm"], merged["annot_y"], atol=1e-6
    ):
        raise ValueError("centroids disagree between the cell table and the annotated object")

    # One all-NaN QuPath object exists in reg017; it did not survive the authors'
    # QC either, so the annotated subset should be free of nulls.
    if merged[markers].isna().any().any():
        raise ValueError("annotated cells carry null intensities")

    merged = merged.drop(columns=["annot_x", "annot_y"])
    return merged, markers


def build_var(markers: list[str], channels: list[str]) -> pd.DataFrame:
    """One row per measured column, in matrix column order.

    The nuclear counterstain is re-imaged every cycle, so DAPI..DAPI15 are
    fifteen separate measurements of one dye rather than fifteen targets. They
    keep their own names because ``target_name`` is what ``protein_key`` falls
    back to, and collapsing them would give fifteen matrix columns one identity.
    """
    index = {name: i for i, name in enumerate(channels)}
    return pd.DataFrame(
        {
            "var_index": markers,
            "target_name": markers,
            "is_control": [is_control_channel(m) for m in markers],
            "channel_index": [index.get(m) for m in markers],
        }
    )


def render_composite(region: str, channels: list[str], out_path: str) -> None:
    """Three channels of the 60-plane stack as a display-stretched RGB image."""
    src = os.path.join(SOURCE_ROOT, f"s293_c001_v001_r001_{region}.ome.tiff")
    canvas = np.zeros((IMAGE_H, IMAGE_W, 3), dtype=np.uint8)
    with tifffile.TiffFile(src) as tif:
        series = tif.series[0]
        if series.shape != (60, IMAGE_H, IMAGE_W):
            raise ValueError(
                f"{region}: image is {series.shape}, expected {(60, IMAGE_H, IMAGE_W)}"
            )
        for name, position in COMPOSITE:
            plane = series.asarray(key=channels.index(name)).astype(np.float32)
            lo, hi = np.percentile(plane, DISPLAY_PERCENTILES)
            if hi <= lo:
                hi = lo + 1.0
            scaled = np.clip((plane - lo) / (hi - lo), 0.0, 1.0) * 255.0
            canvas[:, :, position] = scaled.astype(np.uint8)

    tifffile.imwrite(
        out_path,
        canvas,
        photometric="rgb",
        tile=(512, 512),
        compression="zlib",
        compressionargs={"level": 1},
    )
    print(
        f"  wrote {os.path.basename(out_path)}: {canvas.shape} "
        f"({os.path.getsize(out_path) / 1e6:.1f} MB)"
    )


def build_region(
    region: str,
    frame: pd.DataFrame,
    markers: list[str],
    channels: list[str],
    out_dir: str,
    *,
    skip_images: bool,
) -> dict:
    print(f"{region}:")
    os.makedirs(out_dir, exist_ok=True)
    frame = frame.reset_index(drop=True)
    core = frame["cores"].iat[0]

    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(frame), dtype=np.int64),
            # QuPath object ids are uuids, unique across the whole run.
            "source_obs_id": frame["Object ID"].to_numpy(),
            "image_file": frame["Image"].to_numpy(),
            "region": region,
            "tma_core": core,
            "x_um": frame["Centroid X µm"].to_numpy(),
            "y_um": frame["Centroid Y µm"].to_numpy(),
            "x_px": frame["Centroid X µm"].to_numpy() / UM_PER_PX,
            "y_px": frame["Centroid Y µm"].to_numpy() / UM_PER_PX,
            "um_per_px": UM_PER_PX,
            "cell_area_um2": frame["Cell: Area µm^2"].to_numpy(),
            "nucleus_area_um2": frame["Nucleus: Area µm^2"].to_numpy(),
            "cell_circularity": frame["Cell: Circularity"].to_numpy(),
            "nucleus_circularity": frame["Nucleus: Circularity"].to_numpy(),
            "cell_types": frame["cell_types"].to_numpy(),
            "pheno_leiden": frame["pheno_leiden"].to_numpy(),
            "leiden_group": frame["leiden_group"].to_numpy(),
            "section_id": f"{STUDY}_{core}",
            "panel_name": PANEL_NAME,
        }
    )
    if (obs["x_px"] > IMAGE_W).any() or (obs["y_px"] > IMAGE_H).any():
        raise ValueError(f"{region}: centroids fall outside the {IMAGE_W}x{IMAGE_H} image")

    extras = [
        "cell_circularity",
        "nucleus_circularity",
        "pheno_leiden",
        "leiden_group",
        "tma_core",
        "image_file",
    ]
    obs["source_extras_json"] = [
        json.dumps(record) for record in obs[extras].to_dict(orient="records")
    ]
    obs.to_csv(os.path.join(out_dir, f"{region}_obs.csv"), index=False)
    print(f"  wrote {region}_obs.csv: {len(obs)} cells")

    build_var(markers, channels).to_csv(os.path.join(out_dir, f"{region}_var.csv"), index=False)

    # The matrix, in obs row order. Rounded to satisfy the uint32 counts layer;
    # the fractional means stay in the source-table copy below.
    intensity = frame[markers].round().astype(np.uint32)
    intensity.to_csv(os.path.join(out_dir, f"{region}_protein_intensity.csv"), index=False)

    # The source rows this dataset was derived from, unmodified.
    frame.drop(columns=["cores", "Region"]).to_csv(
        os.path.join(out_dir, f"{region}_cells.csv"), index=False
    )

    image_path = os.path.join(out_dir, f"{region}_composite.tif")
    if not skip_images and not os.path.exists(image_path):
        render_composite(region, channels, image_path)

    return {
        "region": region,
        "tma_core": core,
        "n_cells": len(obs),
        "height_px": IMAGE_H,
        "width_px": IMAGE_W,
        "image_path": image_path,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", nargs="*")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--out", default=STAGING_ROOT)
    args = parser.parse_args(argv)

    channels = pd.read_csv(os.path.join(SOURCE_ROOT, CHANNELS_FILE), header=None)[0].tolist()
    cells, markers = load_sources()
    cells["region"] = [region_of(image) for image in cells["Image"]]
    regions = args.regions or sorted(cells["region"].unique())

    os.makedirs(args.out, exist_ok=True)
    summary = [
        build_region(
            region,
            cells[cells["region"] == region].drop(columns=["region"]),
            markers,
            channels,
            os.path.join(args.out, region),
            skip_images=args.skip_images,
        )
        for region in regions
    ]
    # The geometry file describes the whole package, so a --regions run merges
    # into it rather than replacing it with its own subset. Later steps read this
    # file to enumerate the datasets.
    geometry_path = os.path.join(args.out, "region_geometry.json")
    merged: dict[str, dict] = {}
    if os.path.exists(geometry_path):
        with open(geometry_path) as handle:
            merged = {entry["region"]: entry for entry in json.load(handle)}
    merged.update({entry["region"]: entry for entry in summary})
    with open(geometry_path, "w") as handle:
        json.dump([merged[region] for region in sorted(merged)], handle, indent=2)

    total = sum(entry["n_cells"] for entry in summary)
    print(
        f"\n{len(summary)} region(s), {total} cells, {len(markers)} channels "
        f"({len(merged)} region(s) in {os.path.basename(geometry_path)})"
    )


if __name__ == "__main__":
    main()
