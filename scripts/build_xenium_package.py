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

    # 10x splits non-gene signal three ways. Negative controls report probe and
    # codeword failure; unassigned codewords are transcripts decoded into panel
    # space that were never assigned a target. The atlas keeps that split.
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
    parser.add_argument("--spec", required=True, help="dataset spec JSON")
    parser.add_argument("--samples", nargs="*", help="subset of the spec's samples")
    parser.add_argument("--source", help="override the extracted-bundles directory")
    parser.add_argument("--out", help="override the staging directory")
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

    os.makedirs(out, exist_ok=True)
    summary = [
        build_sample(
            sample,
            spec["samples"][sample],
            spec["study"],
            spec["panel_name"],
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
