#!/usr/bin/env python3
"""Harmonize a Xenium package's per-dataset tables to the atlas schema, spec-driven.

The generalised form of ``harmonize_xenium_lung_datasets.py``: same operations,
with the study's constants in a spec so one script covers any Xenium outs bundle.

The only genuinely per-assay work is the feature table. A Xenium feature axis is
one panel plus its controls, and 10x labels the four kinds in its own vocabulary;
those labels are mapped onto the schema enum in place, so the audit trail records
where each went. Gene columns are nulled for the controls rather than left
holding a codeword name in a field the schema declares as a gene symbol.

Run:
    python scripts/harmonize_xenium_package.py --spec specs/<dataset>.json [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
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

# 10x's own feature labels -> the schema's FeatureType members.
FEATURE_TYPES = {
    "Gene Expression": "gene",
    "Negative Control Probe": "negative_control_probe",
    "Negative Control Codeword": "negative_control_codeword",
    "Unassigned Codeword": "blank_codeword",
    "Deprecated Codeword": "blank_codeword",
}


def lance_db(package: str, sample: str) -> str:
    return os.path.join(package, sample, "lance_db")


def key_column(path: str, table: str, stem: str) -> str:
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
    organism = spec["organism"]
    print(sample)

    var_key = key_column(path, "GenomicFeatureSchema", "var")
    present = set(
        lancedb.connect(path)
        .open_table("GenomicFeatureSchema")
        .to_arrow()
        .column("feature_type")
        .to_pylist()
    )
    unknown = present - set(FEATURE_TYPES)
    if unknown:
        raise ValueError(f"{sample}: unknown 10x feature type(s) {sorted(unknown)}")

    genes: list = [
        RenameColumn(
            column=var_key,
            new_name="feature_id",
            tool="schema_align",
            reason=(
                "the measured feature's identity as published: an Ensembl gene id for panel "
                "targets, and the codeword's own name for controls and blanks"
            ),
        ),
        AddColumn(
            column="feature_key",
            value_sql=f"'{organism}:' || feature_id",
            tool="schema_align",
            reason="corpus-wide identity, organism-composed so codewords cannot collide",
        ),
        AddColumn(
            column="organism",
            value=organism,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name for the study organism",
        ),
        *[
            ReplaceValue(
                column="feature_type",
                old_value=raw,
                new_value=mapped,
                tool="schema_align",
                reason=f"10x label {raw!r} maps onto the schema enum",
            )
            for raw, mapped in FEATURE_TYPES.items()
            if raw in present
        ],
        AddColumn(
            column="is_control",
            value_sql="feature_type != 'gene'",
            tool="schema_align",
            reason="redundant with feature_type but cheap to filter on",
        ),
        AddColumn(
            column="ensembl_gene_id",
            value_sql="CASE WHEN feature_type = 'gene' THEN feature_id ELSE NULL END",
            tool="schema_align",
            reason="only panel targets carry an Ensembl id; a codeword name is not one",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="GenomicFeatureSchema", changes=genes),
        {"feature_id", "feature_key", "organism", "feature_type", "is_control", "ensembl_gene_id"},
        dry_run,
    )

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
            reason="controlled platform name",
        ),
        AddColumn(
            column="organism",
            value=organism,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name",
        ),
        AddColumn(
            column="tissue", value=spec["tissue"], tool="resolve_tissues", reason="UBERON label"
        ),
        AddColumn(
            column="disease_state",
            value=entry["disease_state"],
            tool="schema_align",
            reason="section-level health status",
        ),
        AddColumn(
            column="disease",
            value=entry.get("disease"),
            tool="resolve_diseases",
            reason="MONDO label; null for a healthy section",
        ),
        AddColumn(
            column="spatial_unit",
            value=spec["spatial_unit"],
            tool="schema_align",
            reason="an obs row is a segmented cell",
        ),
        AddColumn(
            column="segmentation_method",
            value=spec["segmentation_method"],
            tool="schema_align",
            reason="how the cell boundary was drawn",
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason="source columns with no schema field",
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
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value=spec["panel"]["panel_name"],
            tool="join_key",
            reason="natural key finalization resolves to the panel uid",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="SpatialObs_gene_expression", changes=obs),
        {c.column for c in obs} | {"additional_metadata"},
        dry_run,
    )

    with open(os.path.join(package, "other_files", "dataset_registry.csv")) as handle:
        row = {r["folder_name"]: r for r in csv.DictReader(handle)}[sample]
    dataset = [
        AddColumn(column=column, value=row[column], tool="schema_align", reason=reason)
        for column, reason in (
            ("study_name", "the vendor release this section belongs to"),
            ("sample_name", "the sample label the vendor uses"),
            ("accession_database", "a vendor showcase release, not an archive deposit"),
            ("data_access_link", "the landing page describing the release"),
            ("download_url", "the outs bundle every feature space here derives from"),
            ("dataset_description", "sample, platform and run summary"),
        )
    ] + [
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value=spec["panel"]["panel_name"],
            tool="join_key",
            reason="every feature space here was measured with one panel",
        ),
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
