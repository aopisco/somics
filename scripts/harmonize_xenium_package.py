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
import re
import sys

import lancedb
from polycomb import (
    AddColumn,
    CurationApplicator,
    CurationTransaction,
    MergeColumns,
    RenameColumn,
    ReplaceValue,
    default_audit_db_path,
)

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
ENSEMBL_RE = re.compile(r"^ENS[A-Z]*G\d{6,}")

# 10x's own feature labels -> the schema's FeatureType members.
FEATURE_TYPES = {
    "Gene Expression": "gene",
    "Negative Control Probe": "negative_control_probe",
    "Negative Control Codeword": "negative_control_codeword",
    "Unassigned Codeword": "blank_codeword",
    "Blank Codeword": "blank_codeword",  # Onboard Analysis 1.0's name for the same thing
    "Deprecated Codeword": "blank_codeword",
    # Xenium Prime 5K panels carry genomic DNA control probes (21 on the 5K
    # human panel); the schema has a member for exactly this.
    "Genomic Control": "genomic_control",
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


def gene_rows(path: str, var_key: str) -> list[dict]:
    """Per-feature values that depend on the vendor's feature type.

    A control codeword has no gene identity, so gene_name and ensembl_gene_id
    are nulled rather than left holding a codeword name in a field the schema
    declares as a gene symbol.
    """
    table = lancedb.connect(path).open_table("GenomicFeatureSchema").to_arrow()
    ids = table.column(var_key).to_pylist()
    names = table.column("gene_name").to_pylist()
    types = table.column("feature_type").to_pylist()
    rows = []
    for feature_id, gene_name, raw in zip(ids, names, types, strict=True):
        is_gene = FEATURE_TYPES[raw] == "gene"
        # 10x and Allen publish Ensembl ids; Vizgen's Liu 2022 codebook and
        # seqFISH gene lists publish symbols only, and a symbol is not an
        # Ensembl id (left null; resolvable against the reference cache later).
        is_ensembl = bool(ENSEMBL_RE.match(str(feature_id)))
        rows.append(
            {
                "feature_id": feature_id,
                "gene_name": gene_name if is_gene else None,
                "ensembl_gene_id": feature_id if (is_gene and is_ensembl) else None,
                "is_control": not is_gene,
            }
        )
    return rows


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
        # MergeColumns fills existing columns; it does not create them. So the
        # two the merge populates are declared empty first.
        AddColumn(
            column="is_control",
            data_type="bool",
            tool="schema_align",
            reason="filled by the keyed merge below, per feature type",
        ),
        AddColumn(
            column="ensembl_gene_id",
            data_type="string",
            tool="schema_align",
            reason="filled by the keyed merge below; null for controls",
        ),
        # LanceDB's SQL dialect has no CASE WHEN, so the three columns that are
        # populated for panel targets and null for controls are computed here and
        # applied as one batch keyed on the published feature id.
        MergeColumns(
            column="gene_name",
            key_column="feature_id",
            rows=gene_rows(path, var_key),
            tool="schema_align",
            reason=(
                "gene_name, ensembl_gene_id and is_control keyed on the published feature id: "
                "populated for panel targets, null/true for control and blank codewords"
            ),
            source="10x cell_feature_matrix.h5 feature table",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="GenomicFeatureSchema", changes=genes),
        # Same set the lung harmonizer allows. gene_name belongs here because the
        # keyed merge writes it, not because any op names it directly, and
        # allowed_columns is checked against every column a transaction touches.
        {
            "feature_id",
            "feature_type",
            "is_control",
            "ensembl_gene_id",
            "gene_name",
            "organism",
            "ensembl_version",
            "feature_key",
        },
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
        # AddColumn will not take value=None; for a healthy section it has to be
        # told the column type instead. The column still has to exist rather than
        # be skipped -- the schema declares it, and finalization null-inits what
        # is missing, which is the path that produces an all-null enum column and
        # trips the Lance encoder.
        (
            AddColumn(
                column="disease",
                value=entry["disease"],
                tool="resolve_diseases",
                reason="MONDO label for this section's diagnosis",
            )
            if entry.get("disease")
            else AddColumn(
                column="disease",
                data_type="string",
                tool="schema_align",
                reason="healthy section: disease is null, not absent",
            )
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
    # A single-feature-space dataset (MERFISH, no image) is staged with a bare
    # obs table; the two-space Xenium shape suffixes it.
    tables = lancedb.connect(path).list_tables()
    tables = list(getattr(tables, "tables", tables))
    obs_table = "SpatialObs_gene_expression" if "SpatialObs_gene_expression" in tables else "SpatialObs"
    apply(
        path,
        sample,
        CurationTransaction(table_name=obs_table, changes=obs),
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


def harmonize_images(spec: dict, package: str, geometry: list[dict], dry_run: bool) -> None:
    """Name the image channels where the builder stacked several.

    A single DAPI focus image is left as the preview sections were (null
    channel_names); a 2.0+ channel directory gets the names the builder derived
    from the file names, in stored order. Library tables live in the
    package-root Lance db, not the per-sample ones.
    """
    names = next((g.get("channel_names") for g in geometry if g.get("channel_names")), None)
    if not names:
        return
    apply(
        os.path.join(package, "lance_db"),
        "package",
        CurationTransaction(
            table_name="SectionImageSchema",
            changes=[
                AddColumn(
                    column="channel_names",
                    value=list(names),
                    tool="schema_align",
                    reason="morphology focus channels as the bundle names them, stored order",
                )
            ],
        ),
        {"channel_names"},
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
    geometry_path = os.path.join(DATA_HOME, "datasets", key, "staging", "sample_geometry.json")
    geometry = json.load(open(geometry_path)) if os.path.exists(geometry_path) else []
    # A spec may leave the panel name to the bundle's gene_panel.json; the
    # builder resolved it into the geometry and the assembler registered it
    # under that name, so the obs join key must use the same string.
    if spec.get("panel") and not spec["panel"].get("panel_name"):
        names = [g.get("panel_name") for g in geometry if g.get("panel_name")]
        if not names:
            raise ValueError("no panel name in the spec or the builder's geometry")
        spec["panel"]["panel_name"] = names[0]
    for sample in args.samples or list(spec["samples"]):
        harmonize_sample(spec, package, sample, args.dry_run)
        if any(g.get("protein_targets") for g in geometry if g.get("sample") == sample):
            # Same antigen axis and the same verified UniProt table as the HuBMAP
            # SPRM packages; the SPRM harmonizer owns that logic.
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from harmonize_sprm_package import harmonize_proteins

            harmonize_proteins(spec, package, sample, args.dry_run)
    if geometry:
        harmonize_images(spec, package, geometry, args.dry_run)


if __name__ == "__main__":
    main()
