"""Shared helpers for the reconstructed polycomb pipeline scripts.

These five scripts (``stage_lance_tables``, ``stage_library_table``,
``stage_dataset_table``, ``apply_resolution_pass``, ``finalize_collection``)
were Claude skills on the hackathon box and were never released with polycomb.
They are reconstructed here from the primitives polycomb 0.0.3 does expose, and
validated by rebuilding the published atlas and diffing against it — see
``docs/2026-08-25_atlas_rebuild_plan.md``.

Conventions they rely on, all of them observable in the committed harmonizers
rather than invented here:

``<root>/lance_db/<ClassName>``
    Collection-level registries (the FK targets). Reported by
    ``polycomb.util.discover_tables`` with ``dataset=None``.
``<root>/<dataset>/lance_db/<ClassName>``
    Per-dataset tables: the obs table, its feature registry, and the dataset
    table. Table names must match the schema class name **exactly** —
    ``_class_for_table`` does an exact match and silently ignores anything else.
``<ObsClass>_<feature_space>``
    How an obs table is named when its dataset has more than one feature space.
    ``scripts/materialize_bare_obs.py`` documents why and bridges the gap.
``<TargetClass>_join`` / ``<field>_<TargetClass>_join``
    The natural key recorded on the target and on each referrer, so
    finalization can resolve a ``RegistryKeyField`` to a uid without guessing.
    Written by the ``harmonize_*_registries.py`` scripts.
"""

from __future__ import annotations

import json
import os

import lancedb
import pandas as pd
import pyarrow as pa

LANCE_DB_DIR = "lance_db"
JOIN_SUFFIX = "_join"
UID_COLUMN = "uid"
DATASET_UID_COLUMN = "dataset_uid"


def read_manifest(collection_root: str) -> dict:
    with open(os.path.join(collection_root, "collection.json")) as handle:
        return json.load(handle)


def dataset_lance_db(collection_root: str, dataset: str) -> str:
    return os.path.join(collection_root, dataset, LANCE_DB_DIR)


def collection_lance_db(collection_root: str) -> str:
    return os.path.join(collection_root, LANCE_DB_DIR)


def obs_class(info) -> str:
    return next(name for name, kind in info.kinds.items() if kind == "obs")


def dataset_class(info) -> str:
    return next(name for name, kind in info.kinds.items() if kind == "dataset")


def feature_registry_by_space(info) -> dict[str, str]:
    """feature_space -> feature registry class name, read off the obs pointers.

    ``discrete_image`` deliberately has no entry: its pointers declare a feature
    space but no registry, because an image crop is addressed rather than
    catalogued.
    """
    cls = getattr(info.module, obs_class(info))
    mapping: dict[str, str] = {}
    for field in cls.model_fields.values():
        extra = getattr(field, "json_schema_extra", None) or {}
        if not extra.get("is_pointer"):
            continue
        registry = extra.get("feature_registry_schema")
        if registry:
            mapping[extra["feature_space"]] = registry
    return mapping


def write_table(lance_db_path: str, table_name: str, table: pa.Table) -> None:
    os.makedirs(lance_db_path, exist_ok=True)
    lancedb.connect(lance_db_path).create_table(table_name, data=table, mode="overwrite")


def read_csv_arrow(path: str) -> pa.Table:
    """CSV -> Arrow, with every all-null column typed as string.

    pandas infers an empty column as float64, which then collides with a schema
    field declared as a string. Staging has no business guessing a type for a
    column with no values in it, so it uses the widest one.
    """
    frame = pd.read_csv(path)
    for column in frame.columns:
        if frame[column].isna().all():
            frame[column] = frame[column].astype("object")
    return pa.Table.from_pandas(frame, preserve_index=False)


def add_column(table: pa.Table, name: str, value) -> pa.Table:
    if name in table.column_names:
        return table
    return table.append_column(name, pa.array([value] * table.num_rows))
