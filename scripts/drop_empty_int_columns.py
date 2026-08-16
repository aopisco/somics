"""Drop integer obs columns that finalization null-initialized and nothing filled.

Ingestion builds its Arrow table from a pandas frame, and casts each column back
to the atlas's own type with ``pa.array(obs_df[col].values, type=...)``. An
int64 Lance column whose rows are all null reads back into pandas as float64
NaN, and NaN does not cast to int64:

    ArrowInvalid: Float value nan was truncated converting to int64

A column that is simply *absent* from the frame takes a different branch and is
null-filled from the schema type, which is the intended result. So the way to
get a null integer column into the atlas is to not carry an empty one into
ingestion.

This affects integer columns only — an all-null double arrives as NaN and casts
back fine, and an all-null enum fails earlier, when Lance refuses to write an
empty dictionary. It bites a spatial proteomics dataset because ``n_genes``
counts something an antibody panel does not measure.

The drop goes through the audited applicator: the column is schema-owned, so its
removal belongs in the curation record even though its content is nothing.

Run:
    python scripts/drop_empty_int_columns.py <collection_root> --table SpatialObs [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os

import lancedb
import pyarrow as pa
from polycomb import CurationApplicator, CurationTransaction, DropColumn, default_audit_db_path

REASON = (
    "the column is entirely null and integer-typed, which ingestion cannot cast back from "
    "pandas; an absent column is null-filled from the schema instead, giving the same result"
)


def datasets(collection_root: str) -> list[tuple[str, str]]:
    with open(os.path.join(collection_root, "collection.json")) as handle:
        manifest = json.load(handle)
    found = [
        (name, os.path.join(collection_root, name, "lance_db"))
        for name in manifest.get("datasets", {})
    ]
    return sorted((n, p) for n, p in found if os.path.isdir(p))


def empty_int_columns(table: pa.Table) -> list[str]:
    return [
        field.name
        for field in table.schema
        if pa.types.is_integer(field.type) and table.column(field.name).null_count == table.num_rows
    ]


def process(name: str, lance_path: str, table_name: str, *, dry_run: bool) -> int:
    db = lancedb.connect(lance_path)
    if table_name not in db.list_tables().tables:
        return 0
    arrow = db.open_table(table_name).to_arrow()
    columns = empty_int_columns(arrow)
    if not columns:
        print(f"  {name}: nothing to drop")
        return 0

    print(f"  {name}: dropping {columns}")
    if dry_run:
        return len(columns)

    applicator = CurationApplicator(lance_path, audit_db_path=default_audit_db_path(lance_path))
    try:
        result = applicator.apply(
            CurationTransaction(
                table_name=table_name,
                changes=[
                    DropColumn(column=column, tool="schema_align", reason=REASON)
                    for column in columns
                ],
            ),
            allowed_columns=set(columns),
        )
        if result.error:
            raise RuntimeError(f"{name}/{table_name}: {result.error}")
    finally:
        applicator.close()
    return len(columns)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection_root")
    parser.add_argument("--table", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.collection_root)
    dropped = sum(
        process(name, path, args.table, dry_run=args.dry_run) for name, path in datasets(root)
    )
    print(f"\n{dropped} column(s) {'to drop' if args.dry_run else 'dropped'}")


if __name__ == "__main__":
    main()
