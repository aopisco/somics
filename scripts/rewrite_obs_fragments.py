"""Rewrite the atlas obs table to repair a bad compacted fragment.

``atlas.optimize()`` compacts obs into ~1M-row fragments. After the CosMx
ingest, the compacted fragment came out with a struct null buffer that Lance
itself rejects on a **filtered** read of ``morphology_crop``:

    Invalid argument error: Incorrect number of nulls for StructArray,
    expected 1024 got 1092

An unfiltered scan of the same column reads all 1.4M rows fine, and every other
pointer column reads fine filtered, so the rows are intact — it is the
compaction's encoding of that one column that is wrong. Writing the same rows
back out produces fragments that read correctly, which is what this does.

The obs table is read whole (~1.4M rows), so this needs a few GB of memory. It
writes a new Lance version; snapshot the atlas afterwards so the atlas version
record pins the repaired table, and do not re-run ``optimize()`` on it.

Run:
    python scripts/rewrite_obs_fragments.py [--atlas PATH] [--table SpatialObs] [--check-only]
"""

from __future__ import annotations

import argparse
import os
import sys

import lancedb
import pyarrow as pa

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

DEFAULT_ATLAS = f"{DATA_HOME}/polycomb_atlases/somics_spatial_atlas"
POINTER_SUFFIXES = ("_crop", "_expression", "_abundance", "_features")


def struct_columns(schema: pa.Schema) -> list[str]:
    return [field.name for field in schema if pa.types.is_struct(field.type)]


def filtered_read_problems(table, columns: list[str]) -> list[str]:
    """Read each struct column under a filter, the shape of read that fails."""
    problems = []
    for column in columns:
        try:
            table.search().where("uid IS NOT NULL").limit(2_000_000).select(
                ["uid", column]
            ).to_arrow()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{column}: {exc}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", default=DEFAULT_ATLAS)
    parser.add_argument("--table", default="SpatialObs")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    db = lancedb.connect(os.path.join(args.atlas, "lance_db"))
    table = db.open_table(args.table)
    columns = struct_columns(table.schema)
    print(f"{args.table}: {table.count_rows()} rows, struct columns {columns}")

    before = filtered_read_problems(table, columns)
    for problem in before:
        print(f"  filtered read fails: {problem[:140]}")
    if not before:
        print("  every struct column reads under a filter; nothing to repair")
        return
    if args.check_only:
        sys.exit(1)

    arrow = table.to_arrow()
    schema = arrow.schema
    print(f"  read {arrow.num_rows} rows")
    db.create_table(args.table, data=arrow, mode="overwrite")

    rewritten = db.open_table(args.table)
    if rewritten.count_rows() != arrow.num_rows:
        raise RuntimeError(f"rewrote {rewritten.count_rows()} rows, expected {arrow.num_rows}")
    if rewritten.schema != schema:
        raise RuntimeError("the rewritten table's schema differs from the original")

    after = filtered_read_problems(rewritten, columns)
    if after:
        print("\nstill failing after the rewrite:", file=sys.stderr)
        for problem in after:
            print(f"- {problem[:200]}", file=sys.stderr)
        sys.exit(1)
    print(f"  rewrote {rewritten.count_rows()} rows; every struct column now reads under a filter")
    print("  snapshot the atlas so its version record pins this table version")


if __name__ == "__main__":
    main()
