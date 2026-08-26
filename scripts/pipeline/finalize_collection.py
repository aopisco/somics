#!/usr/bin/env python3
"""Compute uids, resolve every foreign key, then drop the join scaffolding.

Reconstruction of the hackathon skill script of the same name; see
``scripts/pipeline/_common.py``.

Walks the schema classes in ``polycomb.util.finalization_order`` — a topological
sort of the registry-key declarations, so every FK target is finalized before
anything that points at it — and for each class:

1. **Assigns ``uid``.** A class with ``StableUIDField`` markers gets
   ``make_stable_uid`` over those fields, which is what makes a section's uid
   reproducible across rebuilds. A class without them (the obs table) gets a
   random uid, because it has no natural key to hash.
2. **Resolves incoming foreign keys.** Each referrer carries
   ``<field>_<Target>_join`` and each target carries ``<Target>_join``, both
   holding the same natural key; the target's uid is looked up through them and
   written into ``<field>``. An unresolved key is an error, not a null — a
   dangling FK would surface much later as missing data.
3. **Materializes missing nullable schema columns**, via
   ``ensure_schema_columns_for_table``.

Then, once every referrer has been resolved, the ``*_join`` columns are dropped.
**Order matters here**: cleanup is what
``scripts/materialize_bare_obs.py --phase bare`` has to precede, because an obs
table that appears after cleanup has nothing left to join against.

Finally the dataset table's summary fields (``n_rows``, ``n_sections``, and the
list-valued ``organism``/``tissue``/``disease``/``assay``) are computed from the
finalized obs.

Run:
    python scripts/pipeline/finalize_collection.py <collection_root> \
        --schema <schema.yaml> [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import pyarrow as pa
from homeobox.schema import make_stable_uid, make_uid
from polycomb.finalize_columns import ensure_schema_columns_for_table
from polycomb.util import (
    discover_tables,
    finalization_order,
    join_key,
    load_schema_info,
    overwrite_table,
    read_arrow,
    tables_for_class,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import JOIN_SUFFIX, UID_COLUMN, dataset_class, obs_class  # noqa: E402

STRING = pa.string()


def stable_uid_fields(cls) -> list[str]:
    return [
        name
        for name, field in cls.model_fields.items()
        if (getattr(field, "json_schema_extra", None) or {}).get("stable_uid")
    ]


def set_column(table: pa.Table, name: str, values: list, kind=None) -> pa.Table:
    array = pa.array(values, kind or STRING)
    if name in table.column_names:
        return table.set_column(table.column_names.index(name), name, array)
    return table.append_column(name, array)


def assign_uids(table: pa.Table, cls) -> tuple[pa.Table, int]:
    """uid from the stable-uid fields, or random when the class declares none."""
    keys = stable_uid_fields(cls)
    if not keys:
        return set_column(table, UID_COLUMN, [make_uid() for _ in range(table.num_rows)]), 0
    missing = [k for k in keys if k not in table.column_names]
    if missing:
        raise ValueError(f"stable-uid field(s) {missing} absent; cannot derive a reproducible uid")
    columns = [table.column(k).to_pylist() for k in keys]
    uids = [make_stable_uid(*[str(c[i]) for c in columns]) for i in range(table.num_rows)]
    return set_column(table, UID_COLUMN, uids), len(keys)


def uid_index(refs, class_name: str) -> dict[str, str]:
    """natural key -> uid, pooled across every table holding this class."""
    index: dict[str, str] = {}
    join_column = f"{class_name}{JOIN_SUFFIX}"
    for ref in tables_for_class(refs, class_name):
        table = read_arrow(ref)
        if join_column not in table.column_names or UID_COLUMN not in table.column_names:
            continue
        for key, uid in zip(
            table.column(join_column).to_pylist(),
            table.column(UID_COLUMN).to_pylist(),
            strict=True,
        ):
            canonical = join_key(key)
            if canonical is not None:
                index[canonical] = uid
    return index


def resolve_fks(table: pa.Table, class_name: str, info, indexes) -> tuple[pa.Table, list[str]]:
    resolved = []
    for fk in info.scalar_fks.get(class_name, []):
        column = f"{fk.field_name}_{fk.target_schema}{JOIN_SUFFIX}"
        if column not in table.column_names:
            continue
        index = indexes.get(fk.target_schema, {})
        values, misses = [], set()
        for raw in table.column(column).to_pylist():
            canonical = join_key(raw)
            if canonical is None:
                values.append(None)
                continue
            uid = index.get(canonical)
            if uid is None:
                misses.add(canonical)
            values.append(uid)
        if misses:
            raise ValueError(
                f"{class_name}.{fk.field_name}: {len(misses)} key(s) not in "
                f"{fk.target_schema}: {sorted(misses)[:5]}"
            )
        table = set_column(table, fk.field_name, values)
        resolved.append(fk.field_name)
    return table, resolved


def drop_join_columns(table: pa.Table) -> pa.Table:
    keep = [c for c in table.column_names if not c.endswith(JOIN_SUFFIX)]
    return table.select(keep) if len(keep) != table.num_columns else table


def summarize(refs, info, collection_root: str, *, dry_run: bool) -> None:
    """n_rows, n_sections and the list-valued summary columns, from finalized obs."""
    fields = info.summary_fields.get(dataset_class(info), {})
    if not fields:
        return
    obs_by_dataset = defaultdict(list)
    for ref in refs:
        if ref.class_name == obs_class(info) and ref.dataset:
            obs_by_dataset[ref.dataset].append(ref)

    for ref in tables_for_class(refs, dataset_class(info)):
        obs_refs = obs_by_dataset.get(ref.dataset, [])
        if not obs_refs:
            continue
        table = read_arrow(ref)
        obs = read_arrow(obs_refs[0])
        n_rows = obs.num_rows
        sections = (
            len(set(obs.column("section_uid").to_pylist()))
            if "section_uid" in obs.column_names
            else 0
        )
        table = set_column(table, "n_rows", [n_rows] * table.num_rows, pa.int64())
        table = set_column(table, "n_sections", [sections] * table.num_rows, pa.int64())
        for name in fields:
            if name in ("n_rows", "n_sections") or name not in obs.column_names:
                continue
            distinct = sorted({v for v in obs.column(name).to_pylist() if v is not None})
            table = set_column(table, name, [distinct] * table.num_rows, pa.list_(pa.string()))
        print(f"  {ref.dataset}/{ref.table_name}: n_rows={n_rows} n_sections={sections}")
        if not dry_run:
            overwrite_table(ref, table)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("collection_root")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    info = load_schema_info(args.schema)
    refs = discover_tables(args.collection_root, info)
    print(f"discovered {len(refs)} table(s)")

    indexes: dict[str, dict[str, str]] = {}
    for class_name in finalization_order(info):
        cls = getattr(info.module, class_name)
        for ref in tables_for_class(refs, class_name):
            table = read_arrow(ref)
            if UID_COLUMN in cls.model_fields:
                table, n_keys = assign_uids(table, cls)
                how = f"stable({n_keys} field(s))" if n_keys else "random"
            else:
                how = "no uid column"
            table, resolved = resolve_fks(table, class_name, info, indexes)
            label = f"{ref.dataset + '/' if ref.dataset else ''}{ref.table_name}"
            print(f"  {label}: uid={how}" + (f" fks={resolved}" if resolved else ""))
            if not args.dry_run:
                overwrite_table(ref, table)
                ensure_schema_columns_for_table(ref, info)
        indexes[class_name] = uid_index(refs, class_name)

    # Cleanup only after every referrer above has been resolved.
    for ref in refs:
        table = read_arrow(ref)
        pruned = drop_join_columns(table)
        if pruned.num_columns != table.num_columns:
            dropped = table.num_columns - pruned.num_columns
            print(f"  {ref.table_name}: dropped {dropped} join column(s)")
            if not args.dry_run:
                overwrite_table(ref, pruned)

    summarize(refs, info, args.collection_root, dry_run=args.dry_run)
    print("finalized" + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
