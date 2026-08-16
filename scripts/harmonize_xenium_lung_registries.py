"""Harmonize the collection-level registry tables of the Xenium lung package.

The ontology columns these tables carry (organism, tissue, disease,
clinical_diagnosis) are resolved by ``apply_resolution_pass.py --from-schema``;
what is left, and what this script does, is the work that script cannot:

- **Join keys.** Every ``RegistryKeyField`` needs the natural key that links it
  to its target recorded under the ``*_join`` convention, on both sides, so that
  finalization can resolve it to a uid. Donors and sections are keyed by the
  study-namespaced ids the package assigns; the panel by its name.
- **Channel names.** ``morphology_focus.ome.tif`` is single-channel DAPI, which
  the CSV cannot carry as a list.
- **Null typed columns.** Columns the source has no value for are created with
  an explicit type here rather than left to finalization, which would build an
  enum column as an all-null dictionary — something Lance refuses to write.

Run:
    python scripts/harmonize_xenium_lung_registries.py [--dry-run]
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

LANCE_DB = "/home/ubuntu/polycomb_data_packages/xenium_lung_preview/lance_db"


def apply(txn: CurationTransaction, allowed: set[str], *, dry_run: bool) -> None:
    # Re-running is expected while a package is being brought up, and an
    # AddColumn onto a column that already exists is an error rather than a
    # no-op, so already-satisfied ops are dropped instead of failing the batch.
    existing = set(lancedb.connect(LANCE_DB).open_table(txn.table_name).to_arrow().column_names)
    kept = [op for op in txn.changes if not (isinstance(op, AddColumn) and op.column in existing)]
    # A re-run *after* finalization is the other expected case: finalization
    # drops both the join scaffolding and the source columns it resolved, so
    # re-adding a target join key must not fail because the natural key it was
    # built from has since been consumed. An op whose source column is gone has
    # already served its purpose.
    consumed = [
        op
        for op in kept
        if isinstance(op, AddColumn) and op.value_sql and op.value_sql not in existing
    ]
    if consumed:
        print(
            f"  {txn.table_name}: source column(s) already consumed by finalization, "
            f"skipping {[op.column for op in consumed]}"
        )
        kept = [op for op in kept if op not in consumed]
    if len(kept) != len(txn.changes):
        skipped = [op.column for op in txn.changes if op not in kept]
        print(f"  {txn.table_name}: skipping {skipped}")
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


def donor_ops(dry_run: bool) -> None:
    ops = [
        AddColumn(
            column="DonorSchema_join",
            value_sql="donor_id",
            tool="join_key",
            reason="donors are identified corpus-side by their study-namespaced id",
        ),
        AddColumn(
            column="age_value",
            data_type="double",
            tool="schema_align",
            reason="10x publishes no donor age for this release",
        ),
        AddColumn(
            column="age_unit",
            data_type="string",
            tool="schema_align",
            reason=(
                "no age to give a unit. Created here as a null string rather than left to "
                "finalization, which would build it from the AgeUnit enum: an all-null "
                "dictionary column is an empty dictionary, which Lance refuses to write"
            ),
        ),
        AddColumn(
            column="mouse_development_stage",
            data_type="string",
            tool="schema_align",
            reason="human donors; the MmusDv column stays null by construction",
        ),
        AddColumn(
            column="ethnicity",
            data_type="string",
            tool="schema_align",
            reason="10x publishes no donor ethnicity for this release",
        ),
    ]
    apply(
        CurationTransaction(table_name="DonorSchema", changes=ops),
        {
            "DonorSchema_join",
            "age_value",
            "age_unit",
            "mouse_development_stage",
            "ethnicity",
        },
        dry_run=dry_run,
    )


def section_ops(dry_run: bool) -> None:
    ops = [
        AddColumn(
            column="donor_uid_DonorSchema_join",
            value_sql="donor_id",
            tool="join_key",
            reason="the donor this section was cut from",
        ),
        AddColumn(
            column="TissueSectionSchema_join",
            value_sql="section_id",
            tool="join_key",
            reason="sections are identified corpus-side by their study-namespaced id",
        ),
        AddColumn(
            column="block_id",
            data_type="string",
            tool="schema_align",
            reason="10x publishes no block identifier; each section is its own block here",
        ),
        AddColumn(
            column="section_index",
            data_type="int64",
            tool="schema_align",
            reason="one section per block, so there is no ordinal within a block",
        ),
        AddColumn(
            column="slide_id",
            data_type="string",
            tool="schema_align",
            reason=(
                "experiment.xenium reports slide_id as 'N/A' on this preview run, so there is "
                "no slide serial to record"
            ),
        ),
        AddColumn(
            column="section_thickness_um",
            data_type="double",
            tool="schema_align",
            reason="10x publishes no section thickness for this release",
        ),
    ]
    apply(
        CurationTransaction(table_name="TissueSectionSchema", changes=ops),
        {
            "donor_uid_DonorSchema_join",
            "TissueSectionSchema_join",
            "block_id",
            "section_index",
            "slide_id",
            "section_thickness_um",
        },
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
            value=["DAPI"],
            tool="schema_align",
            reason=(
                "morphology_focus.ome.tif is a single-channel image and its OME metadata names "
                "that channel DAPI; the boundary and interior-RNA channels later Xenium "
                "releases add are not part of this 1.3.0 bundle"
            ),
        ),
        AddColumn(
            column="n_z_planes",
            data_type="int64",
            tool="schema_align",
            reason=(
                "the stored image is the vendor's autofocus projection, a single plane; the "
                "multi-z stack it was projected from is not ingested"
            ),
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
    donor_ops(args.dry_run)
    section_ops(args.dry_run)
    section_image_ops(args.dry_run)
    panel_ops(args.dry_run)


if __name__ == "__main__":
    main()
