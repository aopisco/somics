"""Read ingested CosMx cells back out of an atlas and check them against the source.

Ingestion is where a row can quietly end up attached to the wrong cell — the
matrix is aligned to obs positionally, the feature axis is aligned by name, and
the crops are boxes computed from obs geometry. None of those errors change a
value's plausibility, so they are checked against the vendor's own files here:

- the expression vector against the row of ``exprMat_file.csv`` for that cell,
  feature by feature;
- the protein vector against the ``Mean.*`` columns of ``metadata_file.csv``;
- the crop pixels against the stitched section image at the cell's centroid.

Run:
    python scripts/verify_cosmx_ingest.py [--atlas PATH] [--sample Lung9_Rep1] [--cells 8]
"""

from __future__ import annotations

import argparse
import os
import sys

import homeobox as hox
import lancedb
import numpy as np
import pandas as pd
import tifffile

PACKAGE_ROOT = "/home/ubuntu/polycomb_data_packages/cosmx_nsclc_ffpe"
DEFAULT_ATLAS = "/home/ubuntu/polycomb_atlases/somics_spatial_atlas"
CROP_PX = 128
MARKERS = ["MembraneStain", "PanCK", "CD45", "CD3", "DAPI"]


def check(atlas_path: str, sample: str, n_cells: int) -> list[str]:
    atlas = hox.RaggedAtlas.checkout_latest(atlas_path)
    problems: list[str] = []

    obs = (
        atlas.query()
        .where(f"source_obs_id LIKE '{sample}_%'")
        .select(["uid", "source_obs_id", "x_px", "y_px", "n_counts", "n_genes"])
        .to_polars()
    )
    meta = pd.read_csv(os.path.join(PACKAGE_ROOT, sample, f"{sample}_metadata_file.csv"))
    if len(obs) != len(meta):
        problems.append(f"{sample}: atlas has {len(obs)} row(s), the source has {len(meta)}")
    print(f"{sample}: {len(obs)} obs row(s) in the atlas")

    rng = np.random.default_rng(0)
    picks = sorted(rng.choice(len(obs), size=min(n_cells, len(obs)), replace=False).tolist())
    wanted = obs[picks]

    # -- expression -------------------------------------------------------
    adata = (
        atlas.query()
        .where("source_obs_id IN (" + ", ".join(f"'{s}'" for s in wanted["source_obs_id"]) + ")")
        .feature_spaces("gene_expression")
        .to_anndata()
    )
    # Matrix column names come from the package's own feature table, not from
    # the atlas registry: a gene the corpus already carried keeps the symbol the
    # first dataset registered it under, which for a handful of the CosMx panel
    # is a newer symbol than the panel's own (HIST1H4C registered as H2AC6).
    package_features = (
        lancedb.connect(os.path.join(PACKAGE_ROOT, sample, "lance_db"))
        .open_table("GenomicFeatureSchema")
        .to_arrow()
        .to_pandas()
    )
    name_by_uid = {
        uid: (gene if isinstance(gene, str) else feature)
        for uid, gene, feature in zip(
            package_features["uid"],
            package_features["gene_name"],
            package_features["feature_id"],
            strict=True,
        )
    }
    columns = [name_by_uid[uid] for uid in adata.var.index]

    expr_path = os.path.join(PACKAGE_ROOT, sample, f"{sample}_exprMat_file.csv")
    header = pd.read_csv(expr_path, nrows=0).columns.tolist()
    source_expr = pd.read_csv(expr_path, dtype=dict.fromkeys(header, np.int32))
    source_expr = source_expr[source_expr["cell_ID"] != 0]
    source_expr.index = [
        f"{sample}_F{f:03d}_{c}"
        for f, c in zip(source_expr["fov"], source_expr["cell_ID"], strict=True)
    ]

    dense = np.asarray(adata.X.todense())
    for i, source_id in enumerate(adata.obs["source_obs_id"]):
        expected = source_expr.loc[source_id, columns].to_numpy()
        if not np.array_equal(dense[i], expected):
            bad = int(np.argmax(dense[i] != expected))
            problems.append(
                f"{source_id}: expression differs at {columns[bad]!r} — "
                f"atlas {dense[i][bad]}, source {expected[bad]}"
            )
    print(f"  expression: {len(adata)} cell(s) x {adata.n_vars} features checked")

    # -- protein ----------------------------------------------------------
    protein_registry = atlas.feature_registry("protein_abundance")
    pdata = (
        atlas.query()
        .where("source_obs_id IN (" + ", ".join(f"'{s}'" for s in wanted["source_obs_id"]) + ")")
        .feature_spaces("protein_abundance")
        .to_anndata()
    )
    target_by_uid = dict(
        zip(
            protein_registry["uid"].to_list(),
            protein_registry["target_name"].to_list(),
            strict=True,
        )
    )
    targets = [target_by_uid[uid] for uid in pdata.var.index]
    meta.index = [
        f"{sample}_F{f:03d}_{c}" for f, c in zip(meta["fov"], meta["cell_ID"], strict=True)
    ]
    values = np.asarray(pdata.X)
    for i, source_id in enumerate(pdata.obs["source_obs_id"]):
        expected = meta.loc[source_id, [f"Mean.{t}" for t in targets]].to_numpy()
        if not np.array_equal(values[i], expected):
            problems.append(f"{source_id}: protein differs — atlas {values[i]}, source {expected}")
    print(f"  protein: {len(pdata)} cell(s) x {pdata.n_vars} targets checked")

    # -- crops ------------------------------------------------------------
    crops = (
        atlas.query()
        .where("source_obs_id IN (" + ", ".join(f"'{s}'" for s in wanted["source_obs_id"]) + ")")
        .select(["uid", "source_obs_id", "x_px", "y_px"])
        .select_fields("morphology_crop")
        .to_multimodal()
    )
    image_path = os.path.join(PACKAGE_ROOT, sample, f"{sample}_composite.tif")
    with tifffile.TiffFile(image_path) as tif:
        height, width = tif.series[0].levels[0].shape[:2]

    # Crops come back keyed by the pointer field, one array per row in query
    # order, alongside the shared obs frame.
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
            problems.append(f"{source_id}: crop pixels differ from the section image at ({y}, {x})")
    print(f"  crops: {len(crop_obs)} crop(s) of {CROP_PX}x{CROP_PX} px checked")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", default=DEFAULT_ATLAS)
    parser.add_argument("--sample", default="Lung9_Rep1")
    parser.add_argument("--cells", type=int, default=8)
    args = parser.parse_args()

    problems = check(args.atlas, args.sample, args.cells)
    if problems:
        print("\nMISMATCHES:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        sys.exit(1)
    print("\natlas matches the source files")


if __name__ == "__main__":
    main()
