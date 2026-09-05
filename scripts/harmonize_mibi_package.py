#!/usr/bin/env python3
"""Harmonize a MIBI package's per-dataset tables to the atlas schema, spec-driven.

The ontology columns on the registries (organism, tissue, disease, ethnicity)
are resolved by ``apply_resolution_pass.py --from-schema``; this script does
what that pass cannot -- schema alignment, the join keys finalization resolves
to uids, and the feature identities:

- **ProteinSchema.** The submission's ``antibodies.tsv`` publishes a UniProt
  accession per antibody, so ``uniprot_id`` is that accession verbatim and
  ``protein_key`` is ``Homo sapiens:<accession>``; the elemental and
  background channels have no accession and key on their channel name, as the
  Monkman blanks do. ``gene_name`` and ``protein_name`` are left for a
  resolver: the source states neither, and an accession is enough identity.
- **SpatialObs_protein_abundance.** Constant annotation for the dataset,
  nulls declared where the assay has nothing to say (transcript counts,
  z, a fixed unit size), and the three join keys. ``segmentation_method``
  is typed but null: the submission does not say how the mask was drawn.
- **SectionImageSchema.** ``channel_names`` as a list, from the stack's OME
  metadata via the builder's geometry file.

Run:
    python scripts/harmonize_mibi_package.py --spec specs/mibi/<dataset>.json [--dry-run]
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
    default_audit_db_path,
)

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def apply(
    path: str, label: str, txn: CurationTransaction, allowed: set[str], dry_run: bool
) -> None:
    """Apply one transaction, dropping AddColumns a previous run already satisfied."""
    existing = set(lancedb.connect(path).open_table(txn.table_name).to_arrow().column_names)
    kept = [op for op in txn.changes if not (isinstance(op, AddColumn) and op.column in existing)]
    kept = [op for op in kept if not (isinstance(op, RenameColumn) and op.column not in existing)]
    if not kept:
        print(f"  {label}/{txn.table_name}: already harmonized")
        return
    applicator = CurationApplicator(path, audit_db_path=default_audit_db_path(path))
    try:
        result = applicator.apply(
            CurationTransaction(table_name=txn.table_name, changes=kept),
            dry_run=dry_run,
            allowed_columns=allowed,
        )
        print(f"  {label}/{txn.table_name}: status={result.status.value} ({len(kept)} op(s))")
        if result.error:
            raise RuntimeError(f"{label}/{txn.table_name}: {result.error}")
    finally:
        applicator.close()


def harmonize_proteins(spec: dict, path: str, sample: str, dry_run: bool) -> None:
    organism = spec["organism"]
    ops = [
        RenameColumn(
            column="uniprot_accession",
            new_name="uniprot_id",
            tool="schema_align",
            reason="the accession the submission's antibodies.tsv publishes for this antibody",
        ),
        AddColumn(
            column="protein_key",
            value_sql=f"'{organism}' || ':' || coalesce(uniprot_id, target_name)",
            tool="schema_align",
            reason=(
                "corpus-wide identity: the accession where the submission gives one, otherwise "
                "the channel's own name -- the elemental and background channels have none"
            ),
        ),
        AddColumn(
            column="organism",
            value=organism,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name; human tissue",
        ),
        AddColumn(
            column="modification",
            data_type="string",
            tool="schema_align",
            reason="no antibody in this panel is specific to a post-translational state",
        ),
        AddColumn(
            column="antibody_clone",
            data_type="string",
            tool="schema_align",
            reason="antibodies.tsv gives RRID and lot, not the clone; the RRID is kept alongside",
        ),
        AddColumn(
            column="gene_name",
            data_type="string",
            tool="schema_align",
            reason="not stated by the source; resolvable from uniprot_id by a later pass",
        ),
        AddColumn(
            column="protein_name",
            value_sql="antibody_name",
            tool="schema_align",
            reason="the submission's own antibody name; null on the control channels",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="ProteinSchema", changes=ops),
        {
            "uniprot_id",
            "protein_key",
            "organism",
            "modification",
            "antibody_clone",
            "gene_name",
            "protein_name",
        },
        dry_run,
    )


def harmonize_obs(spec: dict, path: str, sample: str, dry_run: bool) -> None:
    entry = spec["samples"][sample]
    ops = [
        AddColumn(
            column="spatial_unit",
            value=spec["spatial_unit"],
            tool="schema_align",
            reason="one row is one label of the submitted segmentation mask",
        ),
        AddColumn(
            column="assay",
            value=spec["assay"],
            tool="resolve_assays",
            reason="the source's own assay name; EFO alignment by the resolver",
        ),
        AddColumn(
            column="technology",
            value=spec["technology"],
            tool="schema_align",
            reason="SpatialTechnology enum member for this platform",
        ),
        AddColumn(
            column="organism",
            value=spec["organism"],
            tool="resolve_organisms",
            reason="NCBITaxon canonical name",
        ),
        AddColumn(
            column="tissue",
            value=entry.get("tissue", spec["tissue"]),
            tool="resolve_tissues",
            reason="UBERON label; the organ HuBMAP records for the sample",
        ),
        AddColumn(
            column="disease_state",
            value=entry.get("disease_state", "unknown"),
            tool="schema_align",
            reason="the HuBMAP record states no diagnosis for this donor or sample",
        ),
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
            column="segmentation_method",
            data_type="string",
            tool="schema_align",
            reason=(
                "the submission ships a label mask and does not say how it was made; "
                "null, not 'other', so 'unreported' stays distinguishable"
            ),
        ),
        AddColumn(
            column="z_um",
            data_type="double",
            tool="schema_align",
            reason="a single acquisition plane; the mask is 2-D",
        ),
        AddColumn(
            column="unit_size_um",
            data_type="double",
            tool="schema_align",
            reason="segmented cells have no fixed footprint; their extent is cell_area_um2",
        ),
        AddColumn(
            column="n_counts",
            data_type="double",
            tool="schema_align",
            reason="an imaging proteomics assay counts no transcripts; ion counts are per channel",
        ),
        AddColumn(
            column="n_genes",
            data_type="int64",
            tool="schema_align",
            reason="an imaging proteomics assay detects no genes",
        ),
        AddColumn(
            column="in_tissue",
            value=True,
            tool="schema_align",
            reason="a segmented cell in the field of view is in tissue by construction",
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason="mask label, pixel area and the ROI description, which have no schema field",
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
            value_sql="panel_name",
            tool="join_key",
            reason="the antibody panel measured on this cell",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="SpatialObs_protein_abundance", changes=ops),
        {
            "spatial_unit",
            "assay",
            "technology",
            "organism",
            "tissue",
            "disease_state",
            "disease",
            "segmentation_method",
            "z_um",
            "unit_size_um",
            "n_counts",
            "n_genes",
            "in_tissue",
            "additional_metadata",
            "section_uid_TissueSectionSchema_join",
            "donor_uid_DonorSchema_join",
            "panel_uid_PanelSchema_join",
        },
        dry_run,
    )


def harmonize_dataset(spec: dict, package: str, path: str, sample: str, dry_run: bool) -> None:
    with open(os.path.join(package, "other_files", "dataset_registry.csv")) as handle:
        row = {r["folder_name"]: r for r in csv.DictReader(handle)}[sample]
    ops = [
        AddColumn(column=column, value=row[column], tool="schema_align", reason=reason)
        for column, reason in (
            ("study_name", "the HuBMAP dataset title"),
            ("sample_name", "the title HuBMAP gives this field of view"),
            ("source_dataset_id", "the corpus-side dataset id in data/datasets.csv"),
            ("accession_database", "where the deposit lives"),
            ("accession_id", "the HuBMAP id of this dataset"),
            ("data_access_link", "the portal page"),
            ("download_url", "the ion-count stack this package was built from"),
            ("dataset_description", "acquisition and derivation summary"),
        )
    ] + [
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value=spec["panel"]["panel_name"],
            tool="join_key",
            reason="the one antibody panel every feature space of this dataset was measured with",
        )
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="SpatialDatasetSchema", changes=ops),
        {op.column for op in ops},
        dry_run,
    )


def harmonize_images(spec: dict, package: str, geometry: list[dict], dry_run: bool) -> None:
    names = geometry[0]["channel_names"]
    ops = [
        AddColumn(
            column="channel_names",
            value=list(names),
            tool="schema_align",
            reason="the OME channel names of the stack, in stored (trailing-axis) order",
        ),
        AddColumn(
            column="n_z_planes",
            data_type="int64",
            tool="schema_align",
            reason="a single acquisition plane; null for 2-D",
        ),
    ]
    # Library tables live in the package-root Lance db, not the per-sample one.
    apply(
        os.path.join(package, "lance_db"),
        spec["dataset_key"],
        CurationTransaction(table_name="SectionImageSchema", changes=ops),
        {"channel_names", "n_z_planes"},
        dry_run,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--package")
    parser.add_argument("--staging")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec["dataset_key"]
    package = args.package or os.path.join(DATA_HOME, "polycomb_data_packages", key)
    staging = args.staging or os.path.join(DATA_HOME, "datasets", key, "staging")
    with open(os.path.join(staging, "sample_geometry.json")) as handle:
        geometry = json.load(handle)

    for sample in spec["samples"]:
        print(sample)
        path = os.path.join(package, sample, "lance_db")
        harmonize_proteins(spec, path, sample, args.dry_run)
        harmonize_obs(spec, path, sample, args.dry_run)
        harmonize_dataset(spec, package, path, sample, args.dry_run)
    harmonize_images(spec, package, geometry, args.dry_run)


if __name__ == "__main__":
    main()
