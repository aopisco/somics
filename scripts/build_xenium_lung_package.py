"""Assemble the 10x Xenium Human Lung Preview outs bundles into a data package.

The release is two FFPE sections profiled in one preview run — one non-diseased
lung and one with invasive adenocarcinoma — on the same panel: the 292-gene
pre-designed Xenium human lung panel (design PD_339, ``hLung_292g``) plus a
100-gene lung add-on (PD_346, ``hLung_100g``). Because the panel is shared, the
two sections are directly comparable in feature space, which is the whole point
of ingesting them together.

A Xenium ``outs`` bundle is already close to a data package — ``cells.parquet``
is an obs table and ``cell_feature_matrix.h5`` is the matrix — so only three
derivations happen here, all of them properties of the *files* rather than
resolutions of values:

1. **``n_genes`` is computed.** It is a row statistic of the expression matrix
   over the 392 real gene features only, excluding the 149 control and blank
   codewords that share the same feature axis. No later step reads the matrix.

2. **Pixel coordinates are derived.** Xenium reports centroids in microns in the
   same frame the morphology image is written in, so ``x_px = x_um /
   pixel_size`` with no offset — verified here against the image bounds rather
   than assumed.

3. **Row order is pinned.** The ``.h5`` barcode order must equal
   ``cells.parquet`` row order, because ingestion hands the matrix to the writer
   positionally. Asserted, not trusted.

The vendor's own columns travel alongside the derived ones so that harmonization
has the raw values to work from.

Run:
    python scripts/build_xenium_lung_package.py [--samples ...] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil

import h5py
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import tifffile

SOURCE_ROOT = "/home/ubuntu/datasets/xenium_lung_preview/extracted"
# Built files land in staging, not in the package root: the collection's own
# coalesce() is what moves them into place, and it refuses to move a file onto
# itself.
STAGING_ROOT = "/home/ubuntu/datasets/xenium_lung_preview/staging"

STUDY = "Xenium_Lung_Preview"
PANEL_NAME = "Xenium Human Lung Panel v1 + hLung_100g Add-On"

# The 10x folder name is the stable source key; everything else is keyed off it.
SAMPLES = {
    "Xenium_Preview_Human_Non_diseased_Lung_With_Add_on_FFPE": {
        "section": "non_diseased",
        "donor": "non_diseased",
        "disease_state": "healthy",
        "disease": None,
    },
    "Xenium_Preview_Human_Lung_Cancer_With_Add_on_2_FFPE": {
        "section": "lung_cancer",
        "donor": "lung_cancer",
        "disease_state": "diseased",
        "disease": "lung adenocarcinoma",
    },
}

# Files copied into the package unchanged.
PASSTHROUGH = (
    "cell_feature_matrix.h5",
    "morphology_focus.ome.tif",
    "experiment.xenium",
    "metrics_summary.csv",
)

# The feature axis is one panel plus its controls; only these are real genes.
GENE_FEATURE_TYPE = "Gene Expression"


def sample_dir(sample: str) -> str:
    return os.path.join(SOURCE_ROOT, sample)


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
    without materializing the matrix, which is 14.5M nonzeros for the smaller
    section alone.
    """
    with h5py.File(path, "r") as handle:
        group = handle["matrix"]
        indices = np.asarray(group["indices"])
        indptr = np.asarray(group["indptr"])
    if len(indptr) != n_cells + 1:
        raise ValueError(f"{path}: indptr implies {len(indptr) - 1} cells, obs has {n_cells}")
    # np.add.reduceat over the per-row slices of the gene mask, guarding the
    # empty rows reduceat would otherwise read past.
    flags = is_gene[indices].astype(np.int64)
    totals = np.add.reduceat(np.concatenate([flags, [0]]), indptr[:-1])
    totals[np.diff(indptr) == 0] = 0
    return totals.astype(np.int32)


def build_sample(sample: str, out_dir: str) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)
    src = sample_dir(sample)
    spec = SAMPLES[sample]

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

    with tifffile.TiffFile(os.path.join(src, "morphology_focus.ome.tif")) as tif:
        level = tif.series[0].levels[0]
        height, width = (int(d) for d in level.shape[:2])

    x_px = cells.x_centroid.to_numpy() / pixel_size
    y_px = cells.y_centroid.to_numpy() / pixel_size
    if x_px.max() >= width or y_px.max() >= height or x_px.min() < 0 or y_px.min() < 0:
        raise ValueError(
            f"{sample}: centroids fall outside the morphology image "
            f"({y_px.min():.0f}..{y_px.max():.0f} of {height} rows, "
            f"{x_px.min():.0f}..{x_px.max():.0f} of {width} cols); the micron and pixel frames "
            f"are not related by pixel_size alone"
        )

    # Xenium reports control counts in three buckets. Both control buckets are
    # negative controls of the assay — probes that target nothing, and codewords
    # that decode to nothing — while the blank/unassigned bucket is codeword
    # space that was never assigned a target. The atlas keeps that split, which
    # is the same mapping the colon Xenium dataset already in the corpus uses.
    negative_control = (
        cells.control_probe_counts.to_numpy() + cells.control_codeword_counts.to_numpy()
    )
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
            "nucleus_area_um2": cells.nucleus_area.to_numpy(),
            "total_counts": cells.total_counts.to_numpy(),
            "section_id": f"{STUDY}_{spec['section']}",
            "donor_id": f"{STUDY}_{spec['donor']}",
            "panel_name": PANEL_NAME,
        }
    )

    # Source columns with no schema field of their own, kept verbatim so nothing
    # the vendor measured is lost when the leftovers are dropped.
    extras = [
        "control_probe_counts",
        "control_codeword_counts",
        "unassigned_codeword_counts",
        "total_counts",
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
        if not os.path.exists(dest):
            shutil.copy2(os.path.join(src, name), dest)

    return {
        "sample": sample,
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
    parser.add_argument("--samples", nargs="*", default=list(SAMPLES))
    parser.add_argument("--out", default=STAGING_ROOT)
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    summary = [build_sample(sample, os.path.join(args.out, sample)) for sample in args.samples]
    with open(os.path.join(args.out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    total = sum(entry["n_cells"] for entry in summary)
    print(f"\n{len(summary)} sample(s), {total} cells")


if __name__ == "__main__":
    main()
