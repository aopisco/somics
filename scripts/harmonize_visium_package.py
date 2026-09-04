#!/usr/bin/env python3
"""Harmonize a Visium package's per-dataset tables to the atlas schema.

Spec-driven, so one script covers any Visium study rather than one per family.
What it adds is the annotation that is constant for a study and the join keys
finalization resolves foreign keys through — the builder has already written
every measured column under its schema name.

Three things are specific to Visium and worth naming:

- **No panel join.** Whole-transcriptome, so ``panel_uid`` stays null. Every
  other family in this repo carries a panel.
- **``segmentation_method`` is ``grid``.** A spot is a fixed position on the
  capture area, not a segmented object, so the column records how the boundary
  was decided rather than which algorithm drew it.
- **The gene table needs no resolution.** 10x publishes Ensembl IDs in the
  matrix, so ``feature_id`` is the source's own ``gene_id`` and the symbols come
  along with it.

Run:
    python scripts/harmonize_visium_package.py --spec specs/<dataset>.json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os

import lancedb
from polycomb import (
    AddColumn,
    CurationApplicator,
    CurationTransaction,
    RenameColumn,
    ReplaceValue,
    default_audit_db_path,
)

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def lance_db(package: str, sample: str) -> str:
    return os.path.join(package, sample, "lance_db")


def key_column(path: str, table: str, stem: str) -> str:
    """Whichever name staging gave the table's identity column."""
    columns = lancedb.connect(path).open_table(table).to_arrow().column_names
    return f"{stem}_key" if f"{stem}_key" in columns else f"{stem}_index"


def apply(
    path: str, label: str, txn: CurationTransaction, allowed: set[str], dry_run: bool
) -> None:
    applicator = CurationApplicator(path, audit_db_path=default_audit_db_path(path))
    try:
        result = applicator.apply(txn, dry_run=dry_run, allowed_columns=allowed)
        print(
            f"  {label}/{txn.table_name}: status={result.status.value} ({len(txn.changes)} op(s))"
        )
        if result.error:
            raise RuntimeError(f"{label}/{txn.table_name}: {result.error}")
    finally:
        applicator.close()


def harmonize_sample(spec: dict, package: str, sample: str, dry_run: bool) -> None:
    path = lance_db(package, sample)
    entry = spec["samples"][sample]
    print(sample)

    var_key = key_column(path, "GenomicFeatureSchema", "var")
    genes = [
        RenameColumn(
            column=var_key,
            new_name="feature_id",
            tool="schema_align",
            reason="10x publishes Ensembl gene ids in the matrix; that is the feature identity",
        ),
        AddColumn(
            column="feature_key",
            value_sql=f"'{spec['organism']}:' || feature_id",
            tool="schema_align",
            reason=(
                "corpus-wide identity, organism-composed so the same Ensembl id in two "
                "species stays two features"
            ),
        ),
        AddColumn(
            column="organism",
            value=spec["organism"],
            tool="resolve_organisms",
            reason="NCBITaxon canonical name for the study organism",
        ),
        # feature_type already exists: the builder copies 10x's own label out of
        # the h5. Map it onto the schema enum in place so the audit trail records
        # where the vendor label went, rather than adding a second column.
        ReplaceValue(
            column="feature_type",
            old_value="Gene Expression",
            new_value="gene",
            tool="schema_align",
            reason="a whole-transcriptome capture has no probes or control codewords",
        ),
        AddColumn(
            column="is_control",
            value=False,
            tool="schema_align",
            reason="no controls on the feature axis of a capture assay",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="GenomicFeatureSchema", changes=genes),
        {"feature_id", "feature_key", "organism", "feature_type", "is_control"},
        dry_run,
    )

    obs_table = "SpatialObs_gene_expression"
    obs = [
        AddColumn(
            column="assay",
            value=spec["assay"],
            tool="resolve_assays",
            reason="EFO label for the platform",
        ),
        AddColumn(
            column="technology",
            value=spec["technology"],
            tool="schema_align",
            reason="controlled platform name; EFO conflates Visium and Visium HD",
        ),
        AddColumn(
            column="organism",
            value=spec["organism"],
            tool="resolve_organisms",
            reason="NCBITaxon canonical name",
        ),
        AddColumn(
            column="tissue",
            value=entry.get("tissue", spec.get("tissue")),
            tool="resolve_tissues",
            reason="UBERON label",
        ),
        AddColumn(
            column="disease_state",
            value=entry.get("disease_state", spec.get("disease_state")),
            tool="schema_align",
            reason="section-level health status",
        ),
        # Only a diseased section adds `disease`. A healthy one leaves the column
        # to finalization's null-init, which is what the LIBD sections did and
        # what the published atlas holds -- adding an explicit all-null column
        # here would change nothing but the audit trail.
        *(
            [
                AddColumn(
                    column="disease",
                    value=entry["disease"],
                    tool="resolve_diseases",
                    reason="MONDO label for this section's diagnosis",
                )
            ]
            if entry.get("disease")
            else []
        ),
        AddColumn(
            column="spatial_unit",
            value=spec["spatial_unit"],
            tool="schema_align",
            reason="an obs row is a capture spot or bin, not a segmented cell",
        ),
        AddColumn(
            column="segmentation_method",
            value=spec["segmentation_method"],
            tool="schema_align",
            reason=(
                "a spot or bin is a fixed position on the capture area, so the boundary comes "
                "from the grid rather than from segmentation"
            ),
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason="array position and study design, which have no schema field",
        ),
        AddColumn(
            column="section_uid_TissueSectionSchema_join",
            value=entry["section_id"],
            tool="join_key",
            reason="natural key finalization resolves to the section uid",
        ),
        AddColumn(
            column="donor_uid_DonorSchema_join",
            value=entry["donor_id"],
            tool="join_key",
            reason="natural key finalization resolves to the donor uid",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name=obs_table, changes=obs),
        {
            "assay",
            "technology",
            "organism",
            "tissue",
            "disease_state",
            "disease",
            "spatial_unit",
            "segmentation_method",
            "additional_metadata",
            "section_uid_TissueSectionSchema_join",
            "donor_uid_DonorSchema_join",
        },
        dry_run,
    )

    import csv

    with open(os.path.join(package, "other_files", "dataset_registry.csv")) as handle:
        registry = {r["folder_name"]: r for r in csv.DictReader(handle)}
    row = registry[sample]
    dataset = [
        AddColumn(column=column, value=row[column], tool="schema_align", reason=reason)
        for column, reason in (
            ("study_name", "the release these sections belong to"),
            ("sample_name", "the capture area id the source uses"),
            ("accession_database", "where the deposit lives"),
            ("accession_id", "the deposit's own identifier for this section"),
            ("data_access_link", "the landing page describing the study"),
            ("download_url", "the source file this package was built from"),
            ("dataset_description", "sample and platform summary"),
        )
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="SpatialDatasetSchema", changes=dataset),
        {c.column for c in dataset},
        dry_run,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--samples", nargs="*")
    parser.add_argument("--package")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec.get("dataset_key") or os.path.splitext(os.path.basename(args.spec))[0]
    package = args.package or os.path.join(DATA_HOME, "polycomb_data_packages", key)
    for sample in args.samples or list(spec["samples"]):
        harmonize_sample(spec, package, sample, args.dry_run)


if __name__ == "__main__":
    main()
