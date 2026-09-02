"""Bridge finalization for datasets whose second feature space carries no obs.

Finalization finds obs tables by their exact schema class name. Staging names an
obs table `{obs_class}_{feature_space}` whenever its dataset has more than one
feature space, `join_feature_space_obs.py` merges those suffixed tables back onto
the bare name, and `stamp_uid_on_feature_space_obs.py` then writes the per-feature-
space `uid` artifact that ingestion aligns DATA rows through. Both steps assume a
dataset with one obs table was staged bare, and both decline to act otherwise.

A dataset whose second feature space is `discrete_image` falls between the two.
The image is a DATA file with no obs of its own, so the dataset has two feature
spaces but only one obs table: staging suffixes it, the join declines to merge a
single table, and the stamp declines because it cannot name an artifact for a
dataset with two feature spaces. Left alone, the obs table is silently omitted
from finalization altogether.

Two phases, either side of `finalize_collection.py`:

- `--phase bare` (before) copies the lone suffixed table to the bare class name,
  in `obs_index` order so the copy is in DATA row order.
- `--phase artifact` (after) replaces the suffixed table with the `uid` artifact:
  the finalized obs `uid`, ordered by the suffixed table's own `obs_index`. The
  two are matched on `source_obs_id` rather than on position, because neither
  table's physical order survives finalization intact.

Run:
    python scripts/materialize_bare_obs.py <collection_root> \
        --obs-class SpatialObs --phase bare|artifact [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os

import lancedb
import pyarrow as pa

# The column carrying a row's position in DATA order. Current polycomb staging
# writes row_position and reuses obs_key for the source's leading column; older
# staging wrote obs_index. Try them in that order.
OBS_INDEX_CANDIDATES = ("row_position", "obs_index")
UID_COLUMN = "uid"
SOURCE_ID_COLUMN = "source_obs_id"


def datasets(collection_root: str) -> list[tuple[str, str]]:
    with open(os.path.join(collection_root, "collection.json")) as handle:
        manifest = json.load(handle)
    found = []
    for name in manifest.get("datasets", {}):
        path = os.path.join(collection_root, name, "lance_db")
        if os.path.isdir(path):
            found.append((name, path))
    return sorted(found)


def lone_suffixed(db: lancedb.DBConnection, obs_class: str) -> str | None:
    suffixed = sorted(t for t in db.list_tables().tables if t.startswith(f"{obs_class}_"))
    return suffixed[0] if len(suffixed) == 1 else None


def in_data_order(table: pa.Table, label: str) -> pa.Table:
    column = next((c for c in OBS_INDEX_CANDIDATES if c in table.column_names), None)
    if column is None:
        print(f"  {label}: warning — none of {OBS_INDEX_CANDIDATES}; trusting physical order")
        return table
    positions = table.column(column).to_pylist()
    if positions == sorted(positions):
        return table
    print(f"  {label}: restored DATA order from {column!r}")
    return table.sort_by(column)


def make_bare(name: str, lance_path: str, obs_class: str, *, dry_run: bool) -> bool:
    db = lancedb.connect(lance_path)
    if obs_class in db.list_tables().tables:
        print(f"  {name}: {obs_class} already present")
        return False
    source_name = lone_suffixed(db, obs_class)
    if source_name is None:
        print(f"  {name}: no single suffixed obs table; nothing to do")
        return False

    source = in_data_order(db.open_table(source_name).to_arrow(), f"{name}/{source_name}")
    if dry_run:
        print(f"  {name}: would write {obs_class} from {source_name} ({source.num_rows} rows)")
        return True
    db.create_table(obs_class, data=source, mode="overwrite")
    print(f"  {name}: wrote {obs_class} from {source_name} ({source.num_rows} rows)")
    return True


def make_artifact(name: str, lance_path: str, obs_class: str, *, dry_run: bool) -> bool:
    db = lancedb.connect(lance_path)
    artifact_name = lone_suffixed(db, obs_class)
    if artifact_name is None:
        print(f"  {name}: no single suffixed obs table; nothing to do")
        return False

    staged = db.open_table(artifact_name).to_arrow()
    if staged.column_names == [UID_COLUMN]:
        print(f"  {name}: {artifact_name} is already the uid artifact")
        return False

    obs = db.open_table(obs_class).to_arrow()
    for column in (UID_COLUMN, SOURCE_ID_COLUMN):
        if column not in obs.column_names:
            raise ValueError(f"{name}/{obs_class}: no {column!r} column; run finalization first")

    uid_by_source = dict(
        zip(
            obs.column(SOURCE_ID_COLUMN).to_pylist(),
            obs.column(UID_COLUMN).to_pylist(),
            strict=True,
        )
    )
    ordered = in_data_order(staged, f"{name}/{artifact_name}")
    sources = ordered.column(SOURCE_ID_COLUMN).to_pylist()
    missing = [s for s in sources if s not in uid_by_source]
    if missing:
        raise ValueError(
            f"{name}: {len(missing)} DATA row(s) have no finalized obs row "
            f"(first: {missing[0]!r}); the matrix would be misaligned"
        )
    if len(uid_by_source) != len(sources):
        raise ValueError(
            f"{name}: {obs_class} has {len(uid_by_source)} row(s) but the DATA file "
            f"has {len(sources)}; every obs row must have exactly one matrix row"
        )

    artifact = pa.table({UID_COLUMN: [uid_by_source[s] for s in sources]})
    if dry_run:
        print(f"  {name}: would write {artifact_name} = {artifact.num_rows} uid(s)")
        return True
    db.create_table(artifact_name, data=artifact, mode="overwrite")
    print(f"  {name}: wrote {artifact_name} = {artifact.num_rows} uid(s) in DATA order")
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection_root")
    parser.add_argument("--obs-class", required=True)
    parser.add_argument("--phase", choices=("bare", "artifact"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.collection_root)
    step = make_bare if args.phase == "bare" else make_artifact
    changed = sum(
        step(name, path, args.obs_class, dry_run=args.dry_run) for name, path in datasets(root)
    )
    print(f"\n{changed} dataset(s) {'to change' if args.dry_run else 'changed'}")


if __name__ == "__main__":
    main()
