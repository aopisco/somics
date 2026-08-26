"""Read ingested Xenium lung cells back out of an atlas and check them against the source.

Ingestion is where a row can quietly end up attached to the wrong cell — the
matrix is aligned to obs through the per-feature-space uid artifact, the feature
axis is aligned by name, and the crops are boxes computed from obs geometry.
None of those errors change a value's plausibility, so they are checked against
10x's own files here:

- the expression vector against the cell's row of ``cell_feature_matrix.h5``,
  feature by feature;
- the crop pixels against ``morphology_focus.ome.tif`` at the cell's centroid;
- the per-section row count against ``cells.parquet``.

Rows are selected by ``dataset_uid`` rather than by ``source_obs_id``: a Xenium
cell id is only unique within its own run, and this release has two runs whose
id spaces overlap.

Run:
    python scripts/verify_xenium_lung_ingest.py [--atlas PATH] [--cells 8]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import h5py
import homeobox as hox
import lancedb
import numpy as np
import pyarrow.parquet as pq
import scipy.sparse as sp
import tifffile

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/xenium_lung_preview"
# cells.parquet is the one vendor file the package does not carry: the builder
# derives the obs CSV from it. Checking the atlas against the extracted original
# rather than against the derived table is the stronger comparison anyway, since
# it re-tests the derivation as well as the ingest.
EXTRACTED_ROOT = f"{DATA_HOME}/datasets/xenium_lung_preview/extracted"
DEFAULT_ATLAS = f"{DATA_HOME}/polycomb_atlases/somics_spatial_atlas"
CROP_PX = 128


def dataset_uids() -> dict[str, str]:
    with open(os.path.join(PACKAGE_ROOT, "collection.json")) as handle:
        manifest = json.load(handle)
    return {name: entry["dataset_uid"] for name, entry in manifest["datasets"].items()}


def source_matrix(sample: str) -> tuple[sp.csr_matrix, list[str], list[str]]:
    """The published matrix as (cells x features) CSR, with its axes' names."""
    path = os.path.join(PACKAGE_ROOT, sample, "cell_feature_matrix.h5")
    with h5py.File(path, "r") as handle:
        group = handle["matrix"]
        n_features, n_cells = (int(v) for v in group["shape"][:])
        matrix = sp.csr_matrix(
            (group["data"][:], group["indices"][:], group["indptr"][:]),
            shape=(n_cells, n_features),
        )
        feature_ids = np.asarray(group["features"]["id"]).astype(str).tolist()
        barcodes = np.asarray(group["barcodes"]).astype(str).tolist()
    return matrix, feature_ids, barcodes


def check(atlas_path: str, sample: str, uid: str, n_cells: int) -> list[str]:
    atlas = hox.RaggedAtlas.checkout_latest(atlas_path)
    problems: list[str] = []

    obs = (
        atlas.query()
        .where(f"dataset_uid = '{uid}'")
        .select(["uid", "source_obs_id", "x_px", "y_px", "n_counts", "n_genes"])
        .to_polars()
    )
    cells = pq.read_table(os.path.join(EXTRACTED_ROOT, sample, "cells.parquet")).to_pandas()
    cells["cell_id"] = cells["cell_id"].astype(str)
    if len(obs) != len(cells):
        problems.append(f"{sample}: atlas has {len(obs)} row(s), the source has {len(cells)}")
    print(f"{sample}: {len(obs)} obs row(s) in the atlas")

    # The derived count columns, re-checked against the vendor's own.
    source_counts = cells.set_index("cell_id")
    atlas_counts = obs.to_pandas().set_index("source_obs_id")
    joined = atlas_counts.join(source_counts, how="inner")
    if len(joined) != len(cells):
        problems.append(f"{sample}: only {len(joined)} of {len(cells)} cell id(s) joined")
    bad_counts = joined[joined["n_counts"] != joined["transcript_counts"]]
    if len(bad_counts):
        problems.append(
            f"{sample}: n_counts differs from transcript_counts on {len(bad_counts)} row(s)"
        )
    print(f"  n_counts: {len(joined)} row(s) checked against transcript_counts")

    matrix, feature_ids, barcodes = source_matrix(sample)
    row_of = {barcode: i for i, barcode in enumerate(barcodes)}

    rng = np.random.default_rng(0)
    picks = sorted(rng.choice(len(obs), size=min(n_cells, len(obs)), replace=False).tolist())
    wanted = obs[picks]
    selection = ", ".join(f"'{s}'" for s in wanted["source_obs_id"])

    # -- expression -------------------------------------------------------
    adata = (
        atlas.query()
        .where(f"dataset_uid = '{uid}' AND source_obs_id IN ({selection})")
        .feature_spaces("gene_expression")
        .to_anndata()
    )
    # Matrix column names come from the package's own feature table, not from
    # the atlas registry: a gene the corpus already carried keeps whatever
    # identity the first dataset registered it under.
    package_features = (
        lancedb.connect(os.path.join(PACKAGE_ROOT, sample, "lance_db"))
        .open_table("GenomicFeatureSchema")
        .to_arrow()
        .to_pandas()
    )
    feature_of_uid = dict(zip(package_features["uid"], package_features["feature_id"], strict=True))
    column_of_feature = {feature: i for i, feature in enumerate(feature_ids)}
    columns = [column_of_feature[feature_of_uid[u]] for u in adata.var.index]

    dense = np.asarray(adata.X.todense())
    for i, source_id in enumerate(adata.obs["source_obs_id"]):
        expected = np.asarray(matrix[row_of[source_id]].todense()).ravel()[columns]
        if not np.array_equal(dense[i], expected):
            bad = int(np.argmax(dense[i] != expected))
            problems.append(
                f"{sample}/{source_id}: expression differs at "
                f"{feature_ids[columns[bad]]!r} — atlas {dense[i][bad]}, source {expected[bad]}"
            )
    print(f"  expression: {len(adata)} cell(s) x {adata.n_vars} features checked")

    # -- crops ------------------------------------------------------------
    crops = (
        atlas.query()
        .where(f"dataset_uid = '{uid}' AND source_obs_id IN ({selection})")
        .select(["uid", "source_obs_id", "x_px", "y_px"])
        .select_fields("morphology_crop")
        .to_multimodal()
    )
    image_path = os.path.join(PACKAGE_ROOT, sample, "morphology_focus.ome.tif")
    with tifffile.TiffFile(image_path) as tif:
        height, width = tif.series[0].levels[0].shape[:2]

    crop_obs = crops.obs
    tiles = crops["morphology_crop"].layers["raw"]
    for i, source_id in enumerate(crop_obs["source_obs_id"]):
        row = obs.filter(obs["source_obs_id"] == source_id)
        y = int(np.clip(round(row["y_px"][0]) - CROP_PX // 2, 0, height - CROP_PX))
        x = int(np.clip(round(row["x_px"][0]) - CROP_PX // 2, 0, width - CROP_PX))
        expected = tifffile.imread(
            image_path, selection=(slice(y, y + CROP_PX), slice(x, x + CROP_PX))
        )
        got = np.asarray(tiles[i])
        if not np.array_equal(got, expected):
            problems.append(
                f"{sample}/{source_id}: crop pixels differ from the section image at ({y}, {x})"
            )
    print(f"  crops: {len(crop_obs)} crop(s) of {CROP_PX}x{CROP_PX} px checked")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", default=DEFAULT_ATLAS)
    parser.add_argument("--samples", nargs="*")
    parser.add_argument("--cells", type=int, default=8)
    args = parser.parse_args()

    uids = dataset_uids()
    samples = args.samples or sorted(uids)
    problems: list[str] = []
    for sample in samples:
        problems += check(args.atlas, sample, uids[sample], args.cells)

    if problems:
        print("\nMISMATCHES:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        sys.exit(1)
    print("\natlas matches the source files")


if __name__ == "__main__":
    main()
