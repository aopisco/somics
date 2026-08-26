#!/usr/bin/env python3
"""Stage one collection-level registry CSV into a Lance table.

Reconstruction of the hackathon skill script of the same name; see
``scripts/pipeline/_common.py``.

Registries are the FK targets — donors, sections, panels, section images — so
they live in the collection's own ``lance_db``, not under a dataset. The table
name is given explicitly rather than derived, because a registry CSV's filename
(``donor_registry.csv``) does not name its schema class (``DonorSchema``).

Run:
    python scripts/pipeline/stage_library_table.py <collection_root> \
        --library <path.csv> --table <ClassName>
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import collection_lance_db, read_csv_arrow, write_table  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("collection_root")
    ap.add_argument("--library", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.library):
        raise FileNotFoundError(args.library)
    table = read_csv_arrow(args.library)
    print(f"  {args.table}: {table.num_rows} rows x {table.num_columns} cols")
    if not args.dry_run:
        write_table(collection_lance_db(args.collection_root), args.table, table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
