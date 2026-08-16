"""Assemble the CosMx SMI NSCLC FFPE flat files into a polycomb data package.

The vendor ships, per sample, a cell x gene CSV, a per-cell metadata CSV, an FOV
position table, and per-FOV images. Three things have to happen before that is a
data package, and all three are done here rather than during harmonization
because they are derivations of the *files*, not resolutions of values:

1. **The section image is stitched.** CosMx writes one 5472x3648 RGB composite
   per FOV; the atlas stores one image per section that every obs row boxes
   into. FOVs are laid out by ``fov_positions_file.csv`` in a global pixel frame
   whose y axis points *up* — ``y_global = fov_y + y_local`` and ``y_local`` is
   measured from the bottom edge of the FOV image (verified against
   ``CellLabels``: a cell's mask centroid sits at row ``H - 1 - y_local``). The
   mosaic is written in the usual top-left-origin image frame, so the y axis is
   flipped once, here, and the obs pixel coordinates are flipped with it.

2. **Per-cell expression summaries are computed.** ``n_counts``, ``n_genes``,
   and ``negative_control_counts`` are row statistics of the expression matrix,
   which no later step reads.

3. **Row order is pinned.** ``exprMat_file.csv`` carries one extra row per FOV
   for transcripts assigned to no cell (``cell_ID == 0``). Dropping those, its
   rows match ``metadata_file.csv`` exactly, in order — asserted here, because
   every downstream alignment of matrix rows to obs rows depends on it.

The obs table keeps the vendor's own columns alongside the derived ones so that
harmonization has the raw values to work from and its audit trail shows the
arithmetic.

Run:
    python scripts/build_cosmx_nsclc_package.py [--samples Lung6 ...] [--skip-images]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil

import numpy as np
import pandas as pd
import tifffile
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

SOURCE_ROOT = "/home/ubuntu/datasets/cosmx_nsclc_ffpe/extracted"
# Built files land in staging, not in the package root: the collection's own
# coalesce() is what moves them into place, and it refuses to move a file onto
# itself.
STAGING_ROOT = "/home/ubuntu/datasets/cosmx_nsclc_ffpe/staging"

# 180 nm per pixel, from the vendor ReadMe; every FOV is the same size.
UM_PER_PX = 0.18
FOV_H, FOV_W = 3648, 5472

SAMPLES = [
    "Lung5_Rep1",
    "Lung5_Rep2",
    "Lung5_Rep3",
    "Lung6",
    "Lung9_Rep1",
    "Lung9_Rep2",
    "Lung12",
    "Lung13",
]

# Sample -> (donor, block, index within the block). Lung5 and Lung9 were each
# sectioned more than once; the vendor calls those replicates, and they are
# serial sections of one block rather than repeats of one section.
DONOR_OF = {
    "Lung5_Rep1": ("Lung5", 0),
    "Lung5_Rep2": ("Lung5", 1),
    "Lung5_Rep3": ("Lung5", 2),
    "Lung6": ("Lung6", 0),
    "Lung9_Rep1": ("Lung9", 0),
    "Lung9_Rep2": ("Lung9", 1),
    "Lung12": ("Lung12", 0),
    "Lung13": ("Lung13", 0),
}

STUDY = "CosMx_NSCLC"
PANEL_NAME = "CosMx Human Universal Cell Characterization RNA Panel (960-plex prototype)"

# The four antibody/stain channels the morphology imaging used, in the order the
# metadata file lists them. The composite JPEG renders four of them as RGB.
MARKERS = ["MembraneStain", "PanCK", "CD45", "CD3", "DAPI"]


def sample_dir(sample: str) -> str:
    return os.path.join(SOURCE_ROOT, sample, f"{sample}-Flat_files_and_images")


def read_fov_positions(sample: str) -> pd.DataFrame:
    path = os.path.join(sample_dir(sample), f"{sample}_fov_positions_file.csv")
    return pd.read_csv(path).set_index("fov")


def composite_paths(sample: str) -> dict[int, str]:
    out = {}
    for path in glob.glob(os.path.join(sample_dir(sample), "CellComposite", "*.jpg")):
        match = re.search(r"F(\d+)", os.path.basename(path))
        if match:
            out[int(match.group(1))] = path
    return out


def mosaic_frame(sample: str, fovs: list[int]) -> dict:
    """Canvas geometry for the FOVs that actually hold cells.

    ``x_origin`` and ``y_top`` convert the vendor's global pixel frame into the
    mosaic's: ``col = x_global - x_origin`` and ``row = y_top - y_global``.
    """
    pos = read_fov_positions(sample).loc[fovs]
    x_origin = float(pos.x_global_px.min())
    y_top = float(pos.y_global_px.max()) + FOV_H
    width = int(round(float(pos.x_global_px.max()) - x_origin)) + FOV_W
    height = int(round(y_top - float(pos.y_global_px.min())))
    return {"x_origin": x_origin, "y_top": y_top, "height": height, "width": width}


def stitch_composite(sample: str, fovs: list[int], frame: dict, out_path: str) -> None:
    """Paste every FOV composite into one mosaic TIFF.

    The FOV image needs no flip of its own: its top row is the largest
    ``y_local``, which is the smallest mosaic row, so the whole tile lands with a
    straight copy once its origin is computed through the flipped y axis.
    """
    paths = composite_paths(sample)
    missing = [f for f in fovs if f not in paths]
    if missing:
        raise ValueError(f"{sample}: no CellComposite image for FOV(s) {missing}")

    pos = read_fov_positions(sample)
    canvas = np.zeros((frame["height"], frame["width"], 3), dtype=np.uint8)
    for fov in fovs:
        tile = np.asarray(Image.open(paths[fov]))
        if tile.shape != (FOV_H, FOV_W, 3):
            raise ValueError(
                f"{sample} FOV {fov}: composite is {tile.shape}, expected {(FOV_H, FOV_W, 3)}"
            )
        col = int(round(float(pos.x_global_px[fov]) - frame["x_origin"]))
        row = int(round(frame["y_top"] - float(pos.y_global_px[fov]) - FOV_H))
        canvas[row : row + FOV_H, col : col + FOV_W] = tile

    tifffile.imwrite(
        out_path,
        canvas,
        bigtiff=True,
        photometric="rgb",
        tile=(512, 512),
        compression="zlib",
        compressionargs={"level": 1},
    )
    size_gb = os.path.getsize(out_path) / 1e9
    print(f"  wrote {os.path.basename(out_path)}: {canvas.shape} ({size_gb:.2f} GB)")


def expression_summaries(sample: str, meta: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Per-cell count summaries, and the panel's column order.

    Reads the expression CSV once. The ``cell_ID == 0`` rows (one per FOV, for
    transcripts assigned to no cell) are dropped, and what remains must match the
    metadata file row for row — that identity is what lets the matrix be handed
    to ingestion in file order.
    """
    path = os.path.join(sample_dir(sample), f"{sample}_exprMat_file.csv")
    columns = pd.read_csv(path, nrows=0).columns.tolist()
    genes = columns[2:]
    dtypes = {c: np.int32 for c in columns}
    expr = pd.read_csv(path, dtype=dtypes)
    expr = expr[expr.cell_ID != 0].reset_index(drop=True)

    keys = expr[["fov", "cell_ID"]].to_numpy()
    if not np.array_equal(keys, meta[["fov", "cell_ID"]].to_numpy()):
        raise ValueError(
            f"{sample}: expression rows do not match metadata rows one-for-one in order; "
            f"the obs table and the matrix would be misaligned"
        )

    is_negative = np.array([g.startswith("NegPrb") for g in genes])
    values = expr[genes].to_numpy()
    real = values[:, ~is_negative]
    summaries = pd.DataFrame(
        {
            "n_counts": real.sum(axis=1).astype(np.int64),
            "n_genes": (real > 0).sum(axis=1).astype(np.int32),
            "negative_control_counts": values[:, is_negative].sum(axis=1).astype(np.int64),
        }
    )
    return summaries, genes


def build_sample(sample: str, out_dir: str, *, skip_images: bool) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)
    src = sample_dir(sample)

    meta = pd.read_csv(os.path.join(src, f"{sample}_metadata_file.csv"))
    fovs = sorted(meta.fov.unique().tolist())
    frame = mosaic_frame(sample, fovs)
    summaries, genes = expression_summaries(sample, meta)

    donor, section_index = DONOR_OF[sample]
    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(meta), dtype=np.int64),
            "source_obs_id": [
                f"{sample}_F{f:03d}_{c}" for f, c in zip(meta.fov, meta.cell_ID, strict=True)
            ],
            "fov": meta.fov.to_numpy(),
            "cell_ID": meta.cell_ID.to_numpy(),
            "CenterX_global_px": meta.CenterX_global_px.to_numpy(),
            "CenterY_global_px": meta.CenterY_global_px.to_numpy(),
            # The mosaic frame: x shifted to the canvas origin, y flipped onto it.
            "x_px": meta.CenterX_global_px.to_numpy() - frame["x_origin"],
            "y_px": frame["y_top"] - meta.CenterY_global_px.to_numpy(),
            "um_per_px": UM_PER_PX,
            "Area_px": meta.Area.to_numpy(),
            "AspectRatio": meta.AspectRatio.to_numpy(),
            "Width_px": meta.Width.to_numpy(),
            "Height_px": meta.Height.to_numpy(),
            "n_counts": summaries.n_counts.to_numpy(),
            "n_genes": summaries.n_genes.to_numpy(),
            "negative_control_counts": summaries.negative_control_counts.to_numpy(),
            "section_id": f"{STUDY}_{sample}",
            "donor_id": f"{STUDY}_{donor}",
            "panel_name": PANEL_NAME,
        }
    )
    for marker in MARKERS:
        obs[f"Mean_{marker}"] = meta[f"Mean.{marker}"].to_numpy()
        obs[f"Max_{marker}"] = meta[f"Max.{marker}"].to_numpy()

    # Source columns with no schema field of their own, kept verbatim so nothing
    # the vendor measured is lost when the leftovers are dropped.
    extras = ["fov", "cell_ID", "AspectRatio", "Width_px", "Height_px"] + [
        f"Max_{m}" for m in MARKERS
    ]
    obs["source_extras_json"] = [
        json.dumps(record) for record in obs[extras].to_dict(orient="records")
    ]

    obs_path = os.path.join(out_dir, f"{sample}_obs.csv")
    obs.to_csv(obs_path, index=False)
    print(f"  wrote {os.path.basename(obs_path)}: {len(obs)} cells")

    # var: the panel in matrix column order. Negative probes are features of the
    # same axis, flagged rather than dropped.
    var = pd.DataFrame({"var_index": genes})
    var["gene_name"] = [None if g.startswith("NegPrb") else g for g in genes]
    var["is_negative_probe"] = [g.startswith("NegPrb") for g in genes]
    var_path = os.path.join(out_dir, f"{sample}_var.csv")
    var.to_csv(var_path, index=False)

    # protein_abundance: the mean fluorescence of each morphology channel within
    # the segmented cell, one column per target, obs row order.
    protein = obs[[f"Mean_{m}" for m in MARKERS]].copy()
    protein.columns = MARKERS
    protein_path = os.path.join(out_dir, f"{sample}_protein_intensity.csv")
    protein.to_csv(protein_path, index=False)

    # Both feature spaces measure the same cells in the same order, but staging
    # wants one OBS per feature space and joins them on a shared barcode, so the
    # protein side gets its own minimal obs carrying just the join keys. Its
    # first column is the positional index, which is what the barcode
    # reconciliation matches on.
    obs[["obs_index", "source_obs_id"]].to_csv(
        os.path.join(out_dir, f"{sample}_protein_obs.csv"), index=False
    )

    protein_var = pd.DataFrame(
        {
            "var_index": MARKERS,
            "target_name": MARKERS,
            "is_stain": [m in ("MembraneStain", "DAPI") for m in MARKERS],
        }
    )
    protein_var_path = os.path.join(out_dir, f"{sample}_protein_var.csv")
    protein_var.to_csv(protein_var_path, index=False)

    image_path = os.path.join(out_dir, f"{sample}_composite.tif")
    if not skip_images and not os.path.exists(image_path):
        stitch_composite(sample, fovs, frame, image_path)

    # The vendor tables the derived obs was built from travel with the package.
    for name in ("metadata_file.csv", "fov_positions_file.csv"):
        dest = os.path.join(out_dir, f"{sample}_{name}")
        if not os.path.exists(dest):
            shutil.copy2(os.path.join(src, f"{sample}_{name}"), dest)

    # The expression matrix itself is the DATA file, copied unchanged.
    expr_dest = os.path.join(out_dir, f"{sample}_exprMat_file.csv")
    if not os.path.exists(expr_dest):
        shutil.copy2(os.path.join(src, f"{sample}_exprMat_file.csv"), expr_dest)

    return {
        "sample": sample,
        "n_cells": len(obs),
        "n_fovs": len(fovs),
        "height_px": frame["height"],
        "width_px": frame["width"],
        "image_path": image_path,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", nargs="*", default=SAMPLES)
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--out", default=STAGING_ROOT)
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    summary = [
        build_sample(sample, os.path.join(args.out, sample), skip_images=args.skip_images)
        for sample in args.samples
    ]
    with open(os.path.join(args.out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    total = sum(entry["n_cells"] for entry in summary)
    print(f"\n{len(summary)} sample(s), {total} cells")


if __name__ == "__main__":
    main()
