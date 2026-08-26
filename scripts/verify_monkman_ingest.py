"""Read ingested monkman cells back out of an atlas and check them against the source.

Ingestion is where a row can quietly end up attached to the wrong cell — the
matrix is aligned to obs positionally, the feature axis is aligned by name, and
the crops are boxes computed from obs geometry. None of those errors change a
value's plausibility, so they are checked against the deposit's own files here:

- the protein vector against the marker columns of ``<region>_cells.csv``,
  rounded the way the package rounds them, target by target;
- the crop pixels against the rendered section composite at the cell's centroid;
- the cell type against the label the authors published for that object.

Run:
    python scripts/verify_monkman_ingest.py [--atlas PATH] [--region reg019] [--cells 8]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import homeobox as hox
import numpy as np
import pandas as pd
import tifffile

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/monkman_nsclc_codex"
DEFAULT_ATLAS = f"{DATA_HOME}/polycomb_atlases/somics_spatial_atlas"
CROP_PX = 128


def dataset_uid(region: str) -> str:
    with open(os.path.join(PACKAGE_ROOT, "collection.json")) as handle:
        return json.load(handle)["datasets"][region]["dataset_uid"]


def check(atlas_path: str, region: str, n_cells: int) -> list[str]:
    atlas = hox.RaggedAtlas.checkout_latest(atlas_path)
    problems: list[str] = []

    cells = pd.read_csv(os.path.join(PACKAGE_ROOT, region, f"{region}_cells.csv"))
    cells = cells.set_index("Object ID")

    uid = dataset_uid(region)
    n_atlas = atlas.query().where(f"dataset_uid = '{uid}'").count()
    if n_atlas != len(cells):
        problems.append(f"{region}: atlas has {n_atlas} row(s), the source has {len(cells)}")
    print(f"{region}: {n_atlas} obs row(s) in the atlas")

    rng = np.random.default_rng(0)
    picks = rng.choice(len(cells), size=min(n_cells, len(cells)), replace=False)
    wanted = [str(cells.index[i]) for i in sorted(picks.tolist())]
    predicate = "source_obs_id IN (" + ", ".join(f"'{s}'" for s in wanted) + ")"

    # -- protein ----------------------------------------------------------
    registry = atlas.feature_registry("protein_abundance")
    target_by_uid = dict(
        zip(registry["uid"].to_list(), registry["target_name"].to_list(), strict=True)
    )
    pdata = atlas.query().where(predicate).feature_spaces("protein_abundance").to_anndata()
    targets = [target_by_uid[uid] for uid in pdata.var.index]
    values = np.asarray(pdata.X)
    for i, source_id in enumerate(pdata.obs["source_obs_id"]):
        expected = cells.loc[source_id, targets].to_numpy().round().astype(np.uint32)
        if not np.array_equal(values[i], expected):
            bad = int(np.argmax(values[i] != expected))
            problems.append(
                f"{source_id}: protein differs at {targets[bad]!r} — "
                f"atlas {values[i][bad]}, source {expected[bad]}"
            )
    print(f"  protein: {len(pdata)} cell(s) x {pdata.n_vars} targets checked")

    # -- cell types -------------------------------------------------------
    labels = (
        atlas.query()
        .where(predicate)
        .select(["source_obs_id", "cell_type", "cell_type_original", "x_px", "y_px"])
        .to_polars()
    )
    for row in labels.iter_rows(named=True):
        published = cells.loc[row["source_obs_id"], "cell_types"]
        if row["cell_type_original"] != published:
            problems.append(
                f"{row['source_obs_id']}: cell_type_original is "
                f"{row['cell_type_original']!r}, the source says {published!r}"
            )
    print(f"  cell types: {len(labels)} label(s) checked")

    # -- crops ------------------------------------------------------------
    crops = (
        atlas.query()
        .where(predicate)
        .select(["uid", "source_obs_id", "x_px", "y_px"])
        .select_fields("morphology_crop")
        .to_multimodal()
    )
    image_path = os.path.join(PACKAGE_ROOT, region, f"{region}_composite.tif")
    with tifffile.TiffFile(image_path) as tif:
        height, width = tif.series[0].levels[0].shape[:2]

    crop_obs = crops.obs
    tiles = crops["morphology_crop"].layers["raw"]
    for i, source_id in enumerate(crop_obs["source_obs_id"]):
        row = labels.filter(labels["source_obs_id"] == source_id)
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
    parser.add_argument("--region", default="reg019")
    parser.add_argument("--cells", type=int, default=8)
    args = parser.parse_args()

    problems = check(args.atlas, args.region, args.cells)
    if problems:
        print("\nMISMATCHES:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        sys.exit(1)
    print("\natlas matches the source files")


if __name__ == "__main__":
    main()
