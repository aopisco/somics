"""Harmonize the collection-level registry tables of the monkman CODEX package.

The ontology columns these tables carry (organism, tissue, disease) are resolved
by ``apply_resolution_pass.py --from-schema``; what is left, and what this script
does, is the work that pass cannot:

- **Join keys.** Every ``RegistryKeyField`` needs the natural key that links it to
  its target recorded under the ``*_join`` convention, on both sides, so that
  finalization can resolve it to a uid. Sections are keyed by the study-namespaced
  core id, the panel by its name. ``TissueSectionSchema.donor_uid`` gets no join
  column: the deposit has no patients to point at.
- **Channel names.** The section image's trailing axis is the composite this
  package rendered, which no source column states.

There is no DonorSchema table in this package at all — see the assembler.

Run:
    python scripts/harmonize_monkman_registries.py [--dry-run]
"""

from __future__ import annotations

import argparse
import os

import lancedb
from polycomb import (
    AddColumn,
    CurationApplicator,
    CurationTransaction,
    default_audit_db_path,
)

LANCE_DB = "/home/ubuntu/polycomb_data_packages/monkman_nsclc_codex/lance_db"

# Red, green, blue of the rendered composite, in stored order.
COMPOSITE_CHANNELS = ["CD45", "PanCK", "DAPI"]


def apply(txn: CurationTransaction, allowed: set[str], *, dry_run: bool) -> None:
    # Re-running is expected while a package is being brought up, and an
    # AddColumn onto a column that already exists is an error rather than a
    # no-op, so already-satisfied ops are dropped instead of failing the batch.
    existing = set(lancedb.connect(LANCE_DB).open_table(txn.table_name).to_arrow().column_names)
    kept = [op for op in txn.changes if not (isinstance(op, AddColumn) and op.column in existing)]
    if len(kept) != len(txn.changes):
        skipped = [op.column for op in txn.changes if op not in kept]
        print(f"  {txn.table_name}: column(s) already present, skipping {skipped}")
    if not kept:
        return
    txn = CurationTransaction(table_name=txn.table_name, changes=kept)

    applicator = CurationApplicator(LANCE_DB, audit_db_path=default_audit_db_path(LANCE_DB))
    try:
        result = applicator.apply(txn, allowed_columns=allowed, dry_run=dry_run)
        print(f"  {txn.table_name}: status={result.status}")
        if result.error:
            raise RuntimeError(f"{txn.table_name}: {result.error}")
    finally:
        applicator.close()


def section_ops(dry_run: bool) -> None:
    ops = [
        AddColumn(
            column="TissueSectionSchema_join",
            value_sql="section_id",
            tool="join_key",
            reason="sections are identified corpus-side by their study-namespaced core id",
        ),
    ]
    apply(
        CurationTransaction(table_name="TissueSectionSchema", changes=ops),
        {"TissueSectionSchema_join"},
        dry_run=dry_run,
    )


def section_image_ops(dry_run: bool) -> None:
    ops = [
        AddColumn(
            column="section_uid_TissueSectionSchema_join",
            value_sql="section_id",
            tool="join_key",
            reason="the section these pixels were acquired of",
        ),
        AddColumn(
            column="channel_names",
            value=COMPOSITE_CHANNELS,
            tool="schema_align",
            reason=(
                "the stored image is a three-channel rendering of the 60-plane CODEX stack, "
                "one marker per colour, so the trailing axis can be named for its markers "
                "rather than for RGB"
            ),
        ),
        AddColumn(
            column="n_z_planes",
            data_type="int64",
            tool="schema_align",
            reason="CODEX images one focal plane per cycle; the composite is a single plane",
        ),
    ]
    apply(
        CurationTransaction(table_name="SectionImageSchema", changes=ops),
        {"section_uid_TissueSectionSchema_join", "channel_names", "n_z_planes"},
        dry_run=dry_run,
    )


def panel_ops(dry_run: bool) -> None:
    ops = [
        AddColumn(
            column="PanelSchema_join",
            value_sql="panel_name",
            tool="join_key",
            reason="a panel is identified by its versioned name",
        )
    ]
    apply(
        CurationTransaction(table_name="PanelSchema", changes=ops),
        {"PanelSchema_join"},
        dry_run=dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not os.path.isdir(LANCE_DB):
        raise FileNotFoundError(LANCE_DB)
    section_ops(args.dry_run)
    section_image_ops(args.dry_run)
    panel_ops(args.dry_run)


if __name__ == "__main__":
    main()
