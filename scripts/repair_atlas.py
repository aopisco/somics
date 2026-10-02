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


def per_fragment_read_problems(uri: str, columns: list[str]) -> list[str]:
    """Read each struct column under a filter, fragment by fragment.

    A bad fragment fails every filtered scan that touches it, which is why a
    single one made all 1,049 sections fail the per-section check. Checking
    fragments directly finds the same defects in seconds instead of hours
    (112.8M rows: 11 s against ~5 h for the full per-section pass).
    """
    import lance

    problems = []
    for fragment in lance.dataset(uri).get_fragments():
        for column in columns:
            try:
                fragment.to_table(columns=["uid", column], filter="uid IS NOT NULL")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"fragment {fragment.fragment_id}.{column}: {str(exc)[:120]}")
    return problems


def sampled_section_read_problems(db, table, columns: list[str], n: int = 60) -> list[str]:
    """The atlas's real query shape (a section_uid filter) on a fixed sample of sections."""
    import random

    sections = db.open_table("TissueSectionSchema").to_arrow().column("uid").to_pylist()
    random.Random(0).shuffle(sections)
    problems = []
    for uid in sections[:n]:
        for column in columns:
            try:
                table.search().where(f"section_uid = '{uid}'").limit(50_000_000).select(["uid", column]).to_arrow()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{uid}.{column}: {str(exc)[:120]}")
    return problems


def read_problems(db, table, uri: str, columns: list[str], full: bool) -> list[str]:
    problems = per_fragment_read_problems(uri, columns)
    problems += (per_section_read_problems if full else sampled_section_read_problems)(db, table, columns)
    return problems


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


def _contiguous(batch: pa.RecordBatch) -> pa.RecordBatch:
    """The same batch with every buffer rebuilt from offset 0.

    A scan hands back struct arrays that are slices of larger buffers. Written
    as-is, the struct's validity and its children's can land misaligned, which
    is the "Incorrect number of nulls for StructArray" a filtered read then
    trips over. An IPC round trip copies each column into fresh buffers.
    """
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, batch.schema) as writer:
        writer.write_batch(batch)
    return pa.ipc.open_stream(sink.getvalue()).read_next_batch()


def rewrite_streaming(uri: str, batch_rows: int = 131_072, rows_per_file: int = 1_048_576) -> None:
    """Rewrite a Lance table from an unfiltered scan, one contiguous batch at a time.

    The old rewrite read the whole table (``to_arrow()``) and wrote it back in
    one call. That repaired 23M rows, but at 101M+ rows the rewritten table
    failed the same filtered reads (Stereo-seq follow-up, literature Xenium),
    and it needs the table in memory. Unfiltered scans of the broken table read
    fine, so stream them, rebuild each batch, and write a new version; the old
    version's files stay until cleanup, so the scan is not disturbed.
    """
    import lance

    source = lance.dataset(uri)
    schema = source.schema

    def batches():
        done = 0
        for batch in source.to_batches(batch_size=batch_rows):
            yield _contiguous(batch)
            done += batch.num_rows
            if done % (batch_rows * 80) < batch_rows:
                print(f"    rewrote {done} rows", flush=True)

    lance.write_dataset(
        pa.RecordBatchReader.from_batches(schema, batches()),
        uri,
        schema=schema,
        mode="overwrite",
        max_rows_per_file=rows_per_file,
        max_rows_per_group=1024,
        data_storage_version=source.data_storage_version,
    )


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
    ap.add_argument("--full-section-check", action="store_true",
                    help="read every section (hours at 100M+ rows) instead of every fragment plus a section sample")
    args = ap.parse_args()

    db = lancedb.connect(os.path.join(args.atlas, "lance_db"))
    table = db.open_table(args.table)
    uri = os.path.join(args.atlas, "lance_db", f"{args.table}.lance")
    columns = struct_columns(table.schema)
    print(f"{args.table}: {table.count_rows()} rows; reading {columns} per fragment and per sampled section")
    problems = read_problems(db, table, uri, columns, args.full_section_check)
    if not problems:
        print("  every fragment and sampled section reads under a filter; nothing to repair")
        return 0
    for p in problems:
        print(f"  filtered read fails: {p[:160]}")
    if args.check_only:
        return 1

    n, schema = table.count_rows(), table.schema
    print(f"  {n} rows; rewriting batch by batch")
    rewrite_streaming(os.path.join(args.atlas, "lance_db", f"{args.table}.lance"))
    rewritten = db.open_table(args.table)
    if rewritten.count_rows() != n or rewritten.schema != schema:
        raise RuntimeError("rewritten table differs in rows or schema")
    after = read_problems(db, rewritten, uri, columns, args.full_section_check)
    if after:
        for p in after:
            print(f"  STILL FAILING: {p[:200]}", file=sys.stderr)
        return 1
    version = snapshot(args.atlas, args.schema)
    print(
        f"  rewrote {n} rows; every fragment and sampled section reads under a filter; atlas snapshot v{version}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
