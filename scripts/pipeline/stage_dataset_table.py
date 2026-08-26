#!/usr/bin/env python3
"""Create each dataset's dataset table: one row per feature space.

Reconstruction of the hackathon skill script of the same name; see
``scripts/pipeline/_common.py``.

Only the two columns that are structural are written here — ``dataset_uid``,
which the manifest owns, and ``feature_space``, which the dataset's own tagged
files determine. Everything else is somebody else's to fill and is deliberately
left absent: provenance by ``harmonize_*_datasets.py`` (which ``AddColumn``s
onto this table and would error on a column that already existed), the summary
fields by finalization, and ``zarr_group`` / ``layout_uid`` / ``created_at`` by
ingestion once the arrays are actually written.

``discrete_image`` gets a row like any other feature space. It has no feature
registry and no obs of its own, but it is still a space the dataset carries and
ingestion looks it up here.

Run:
    python scripts/pipeline/stage_dataset_table.py <collection_root> --schema <schema.yaml>
"""

from __future__ import annotations

import argparse
import os
import sys

import pyarrow as pa
from polycomb.util import load_schema_info

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (  # noqa: E402
    dataset_class,
    dataset_lance_db,
    read_manifest,
    write_table,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("collection_root")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--datasets", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    info = load_schema_info(args.schema)
    table_name = dataset_class(info)
    manifest = read_manifest(args.collection_root)
    wanted = set(args.datasets) if args.datasets else None

    for name, payload in sorted(manifest.get("datasets", {}).items()):
        if wanted and name not in wanted:
            continue
        spaces = sorted({f["feature_space"] for f in payload["files"] if f["feature_space"]})
        if not spaces:
            print(f"  {name}: no feature spaces, skipped")
            continue
        table = pa.table(
            {
                "dataset_uid": pa.array([payload["dataset_uid"]] * len(spaces), pa.string()),
                "feature_space": pa.array(spaces, pa.string()),
            }
        )
        print(f"  {name}/{table_name}: {len(spaces)} rows ({', '.join(spaces)})")
        if not args.dry_run:
            write_table(dataset_lance_db(args.collection_root, name), table_name, table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
