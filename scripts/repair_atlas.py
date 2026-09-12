#!/usr/bin/env python3
"""Check the atlas obs table's pointer columns under a filter and repair if needed.

``atlas.optimize()`` -- which every ingest runs, because it assigns
``global_index`` -- compacts obs into ~1M-row fragments, and a compacted
fragment has twice come out with a struct null buffer Lance rejects on a
**filtered** read (``Incorrect number of nulls for StructArray``). Unfiltered
scans are fine, so the rows are intact; it is one column's encoding in one
fragment. After the 10x Visium block landed (22.9M rows) the verifier hit it
on the first section it read.

This is the end-of-run step both EC2 ingest scripts call, and what
``repair_atlas_ec2.sh`` runs on a large box against a finished prefix:

1. read every struct column of the obs table under a per-section filter, for
   every section (the shape every query uses); exit 0 if all read;
2. otherwise rewrite the table from a whole read -- the remedy
   ``rewrite_obs_fragments.py`` established -- which needs memory for the
   whole table (tens of GB at 22.9M rows; use a 128 GB box);
3. snapshot the atlas through the same schema resolution the ingest uses, so
   the version record pins the repaired table.

Do not run ``optimize()`` again afterwards without re-checking; the next
ingest will, and will be followed by this step again.

Run:
    PYTHONPATH=src python scripts/repair_atlas.py --atlas PATH \\
        --schema schema/spatial_omics_atlas_schema.yaml [--check-only]
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import lancedb
import pyarrow as pa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rewrite_obs_fragments import struct_columns  # noqa: E402


def per_section_read_problems(db, table, columns: list[str]) -> list[str]:
    """Read each struct column under a per-section filter, for every section.

    ``rewrite_obs_fragments.py`` checked ``uid IS NOT NULL`` with a 2M-row limit,
    which reaches only the first fragments; the Visium-block defect sat in a
    later one and the check passed while the verifier's first
    ``section_uid == ...`` read failed. Reading every section the way the atlas
    is actually queried is the check that means something.
    """
    sections = db.open_table("TissueSectionSchema").to_arrow().column("uid").to_pylist()
    problems = []
    for uid in sections:
        for column in columns:
            try:
                table.search().where(f"section_uid = '{uid}'").limit(50_000_000).select(
                    ["uid", column]
                ).to_arrow()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{uid}.{column}: {str(exc)[:120]}")
    return problems


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(REPO_ROOT, "schema", "spatial_omics_atlas_schema.yaml")


def snapshot(atlas_path: str, schema_path: str) -> int:
    from homeobox.atlas import create_or_open_atlas
    from polycomb.ingestion import _resolve_schema

    schema = _resolve_schema(os.path.abspath(schema_path))
    registries = dict(schema.feature_space_registry())
    # The schema declares feature spaces no package has carried yet (chromatin
    # accessibility), and create_or_open_atlas refuses an atlas with no registry
    # table for a declared space. Open with the registries the atlas has.
    for _ in range(len(registries) + 1):
        try:
            atlas = create_or_open_atlas(
                atlas_path,
                obs_schemas={schema.obs_class: schema.obs_cls},
                dataset_table_name=schema.dataset_class,
                dataset_schema=schema.dataset_cls,
                registry_schemas=registries,
            )
            break
        except ValueError as exc:
            m = re.search(r"no registry table for feature space '([^']+)'", str(exc))
            if not m or m.group(1) not in registries:
                raise
            print(f"  opening without the '{m.group(1)}' registry (no table in this atlas)")
            registries.pop(m.group(1))
    return atlas.snapshot()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--table", default="SpatialObs")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    db = lancedb.connect(os.path.join(args.atlas, "lance_db"))
    table = db.open_table(args.table)
    columns = struct_columns(table.schema)
    print(f"{args.table}: {table.count_rows()} rows; reading {columns} per section")
    problems = per_section_read_problems(db, table, columns)
    if not problems:
        print("  every struct column reads under a filter; nothing to repair")
        return 0
    for p in problems:
        print(f"  filtered read fails: {p[:160]}")
    if args.check_only:
        return 1

    arrow: pa.Table = table.to_arrow()
    n, schema = arrow.num_rows, arrow.schema
    print(f"  read {n} rows; rewriting")
    db.create_table(args.table, data=arrow, mode="overwrite")
    del arrow
    rewritten = db.open_table(args.table)
    if rewritten.count_rows() != n or rewritten.schema != schema:
        raise RuntimeError("rewritten table differs in rows or schema")
    after = per_section_read_problems(db, rewritten, columns)
    if after:
        for p in after:
            print(f"  STILL FAILING: {p[:200]}", file=sys.stderr)
        return 1
    version = snapshot(args.atlas, args.schema)
    print(
        f"  rewrote {n} rows; every struct column reads under a filter; atlas snapshot v{version}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
