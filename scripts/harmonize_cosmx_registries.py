"""Harmonize the collection-level registry tables of the CosMx NSCLC package.

The ontology columns these tables carry (organism, tissue, disease,
clinical_diagnosis) are resolved by ``apply_resolution_pass.py --from-schema``;
what is left, and what this script does, is the work that script cannot:

- **Ethnicity.** HANCESTRO's binding is a custom resolver, so the pass skips it
  and the resolution is run here. The vendor reports race as "White" for the
  whole cohort.
- **Join keys.** Every ``RegistryKeyField`` needs the natural key that links it
  to its target recorded under the ``*_join`` convention, on both sides, so that
  finalization can resolve it to a uid. Donors and sections are keyed by the
  study-namespaced ids the package assigns; the panel by its name.
- **Channel names.** The section image's trailing axis is the composite's RGB,
  which no source column states.

Run:
    python scripts/harmonize_cosmx_registries.py [--dry-run]
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
from polycomb.ontologies import OntologyEntity, resolve_ontology_terms

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

LANCE_DB = f"{DATA_HOME}/polycomb_data_packages/cosmx_nsclc_ffpe/lance_db"


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


def donor_ops(dry_run: bool) -> None:
    db = lancedb.connect(LANCE_DB)
    values = db.open_table("DonorSchema").to_arrow().column("ethnicity").to_pylist()
    distinct = list(dict.fromkeys(v for v in values if v))
    report = resolve_ontology_terms(distinct, OntologyEntity.ETHNICITY, organism="human")
    ops = report.propose_column_replacements(
        distinct,
        column="ethnicity",
        reason="vendor-reported race -> canonical HANCESTRO label",
        resolution_field_name="resolved_value",
    )
    ops += [
        AddColumn(
            column="DonorSchema_join",
            value_sql="donor_id",
            tool="join_key",
            reason="donors are identified corpus-side by their study-namespaced id",
        ),
        AddColumn(
            column="age_unit",
            data_type="string",
            tool="schema_align",
            reason=(
                "the vendor reports the cohort as '60+' with no per-donor age, so there is no "
                "age to give a unit. The column is created here as a null string rather than "
                "left to finalization, which would build it from the AgeUnit enum: an all-null "
                "dictionary column is an empty dictionary, which Lance refuses to write"
            ),
        ),
    ]
    apply(
        CurationTransaction(table_name="DonorSchema", changes=ops),
        {"ethnicity", "DonorSchema_join", "age_unit"},
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
    ]
    apply(
        CurationTransaction(table_name="TissueSectionSchema", changes=ops),
        {"donor_uid_DonorSchema_join", "TissueSectionSchema_join"},
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
            value=["R", "G", "B"],
            tool="schema_align",
            reason=(
                "the stored image is the vendor's own RGB composite, not the separated "
                "morphology channels; which marker each colour carries is on the image row's "
                "description, because the mapping is many-to-one (CD3 renders as grey)"
            ),
        ),
        AddColumn(
            column="n_z_planes",
            data_type="int64",
            tool="schema_align",
            reason="the composite is a single rendered plane; the z-stacks are not published here",
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
