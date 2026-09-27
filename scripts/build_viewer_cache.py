#!/usr/bin/env python3
"""Precompute the viewer's per-section index so the API never scans the obs table.

The viewer API was written for a 587k-cell atlas: `samples()` scanned 13 obs
columns and `point_cloud()` scanned six and partitioned the whole atlas in
memory. At 73M rows that is minutes and gigabytes per request. This script does
those two scans once, on a box next to the bucket, and writes:

    <out>/samples.json                 the /api/samples payload (AtlasSource.samples())
    <out>/coords/<section_uid>.parquet uid, x_um, y_um, n_counts, n_genes, cell_area_um2

The API reads them when `SOMICS_VIEWER_INDEX` points at the directory or the
s3:// prefix they were synced to. Crops and gene painting still read the atlas
live (filtered reads; those were always the cheap path).

Run (on EC2, against the current atlas prefix):
    SOMICS_ATLAS_DIR=s3://somics-dev/ingest/<family>/atlas/<stamp> SOMICS_ATLAS_STORE=aws \\
      uv run python scripts/build_viewer_cache.py --out /mnt/work/viewer_cache
"""

from __future__ import annotations

import argparse
import json
import os
import time

import polars as pl
import pyarrow.parquet as pq

from somics.viewer.atlas_source import AtlasConfig, AtlasSource, _COORD_COLUMNS


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(os.path.join(args.out, "coords"), exist_ok=True)

    config = AtlasConfig.from_env()
    config.index_dir = None  # this script produces the index; it must not read one
    source = AtlasSource(config)
    t0 = time.time()
    samples = source.samples()
    print(f"samples: {len(samples)} sections in {time.time() - t0:.0f} s")
    with open(os.path.join(args.out, "samples.json"), "w") as handle:
        json.dump(samples, handle)

    t0 = time.time()
    frame = source.atlas.query().select([*_COORD_COLUMNS, "uid"]).to_polars()
    print(f"coords: {frame.height} rows in {time.time() - t0:.0f} s")
    n = 0
    for key, part in frame.partition_by("section_uid", as_dict=True).items():
        section_uid = str(key[0])
        part = part.drop("section_uid").select(["uid", "x_um", "y_um", "n_counts", "n_genes", "cell_area_um2"])
        pq.write_table(part.to_arrow(), os.path.join(args.out, "coords", f"{section_uid}.parquet"), compression="zstd")
        n += 1
    print(f"wrote {n} per-section parquet files under {args.out}/coords")
    missing = [s["section_uid"] for s in samples if not os.path.exists(os.path.join(args.out, "coords", f"{s['section_uid']}.parquet"))]
    if missing:
        raise SystemExit(f"{len(missing)} sections in samples.json have no coords file, e.g. {missing[:3]}")


if __name__ == "__main__":
    main()
