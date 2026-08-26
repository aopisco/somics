#!/usr/bin/env python3
"""Stage each dataset's OBS and VAR files into Lance tables.

Reconstruction of the hackathon skill script of the same name; see
``scripts/pipeline/_common.py`` for why and for the conventions.

For every dataset in ``collection.json`` this reads the files the collection
tagged OBS and VAR and writes them into ``<dataset>/lance_db/`` under the schema
class they belong to:

- an OBS file becomes the obs class, suffixed with its feature space when the
  dataset has more than one — the suffix is what lets two feature spaces keep
  separate obs while the schema has only one obs class;
- a VAR file becomes the feature registry its feature space declares on the obs
  pointer (``gene_expression`` -> ``GenomicFeatureSchema``). A feature space
  with no registry, which is what ``discrete_image`` is, contributes no table.

``dataset_uid`` is stamped on obs here rather than by the harmonizers, because
it comes from the manifest and nothing downstream can recover it once the file
has been read.

Run:
    python scripts/pipeline/stage_lance_tables.py <collection_root> --schema <schema.yaml>
"""

from __future__ import annotations

import argparse
import os
import sys

from polycomb.collection import FileTypeTag
from polycomb.util import load_schema_info

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (  # noqa: E402
    DATASET_UID_COLUMN,
    add_column,
    dataset_lance_db,
    feature_registry_by_space,
    obs_class,
    read_csv_arrow,
    read_manifest,
    write_table,
)


def stage_dataset(
    collection_root: str,
    name: str,
    payload: dict,
    info,
    *,
    dry_run: bool,
) -> list[str]:
    files = payload["files"]
    spaces = sorted({f["feature_space"] for f in files if f["feature_space"]})
    registries = feature_registry_by_space(info)
    lance_path = dataset_lance_db(collection_root, name)
    written = []

    for entry in files:
        tag, space, path = entry["tag"], entry["feature_space"], entry["path"]
        if tag == FileTypeTag.OBS:
            table_name = obs_class(info) if len(spaces) == 1 else f"{obs_class(info)}_{space}"
        elif tag == FileTypeTag.VAR:
            table_name = registries.get(space)
            if table_name is None:
                continue
        else:
            continue
        if not os.path.exists(path):
            raise FileNotFoundError(f"{name}: {tag} file missing: {path}")

        table = read_csv_arrow(path)
        if tag == FileTypeTag.OBS:
            table = add_column(table, DATASET_UID_COLUMN, payload["dataset_uid"])
        print(f"  {name}/{table_name}: {table.num_rows} rows x {table.num_columns} cols")
        if not dry_run:
            write_table(lance_path, table_name, table)
        written.append(table_name)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("collection_root")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--datasets", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    info = load_schema_info(args.schema)
    manifest = read_manifest(args.collection_root)
    wanted = set(args.datasets) if args.datasets else None

    total = 0
    for name, payload in sorted(manifest.get("datasets", {}).items()):
        if wanted and name not in wanted:
            continue
        total += len(stage_dataset(args.collection_root, name, payload, info, dry_run=args.dry_run))
    print(f"staged {total} tables{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
