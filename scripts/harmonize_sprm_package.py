#!/usr/bin/env python3
"""Harmonize a Cytokit + SPRM package's tables to the atlas schema, spec-driven.

Per region, three tables in the region's Lance db:

- **ProteinSchema** -- the antigen axis. Targets are resolved to UniProt only
  through the table the Monkman CODEX package established with
  ``resolve_proteins`` (matched case-insensitively, so ``CD11C`` finds
  ``CD11c``); anything else keeps its channel name as identity, with
  ``uniprot_id`` null and the reason in the audit trail. HuBMAP panels reach
  55 targets and differ by dataset, so resolving the long tail is a
  reference-cache job for a later pass, not a hand-typed table here -- a wrong
  accession is worse than a null one.
- **SpatialObs_protein_abundance** -- the obs table: schema alignment and the
  three join keys (section, donor, panel). Nothing is measured here; the
  geometry came from SPRM and the builder.
- **SpatialDatasetSchema** -- provenance, one row per feature space, from the
  ``dataset_registry.csv`` the assembler wrote.

And once per package, in the package-root Lance db, the section image's
``channel_names`` (a list column, which a CSV cannot carry) and ``n_z_planes``.

Run:
    python scripts/harmonize_sprm_package.py --spec specs/sprm/<dataset>.json [--dry-run]
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
    RenameColumn,
    ReplaceValue,
    default_audit_db_path,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harmonize_monkman_datasets import PROTEIN_RESOLUTION  # noqa: E402

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


RESOLVED_BY_NORM = {_norm(k): v for k, v in PROTEIN_RESOLUTION.items()}


def lance_db(package: str, sample: str) -> str:
    return os.path.join(package, sample, "lance_db")


def apply(path: str, label: str, txn: CurationTransaction, allowed: set[str], dry_run: bool):
    # Re-running is expected while a package is being brought up; an AddColumn
    # onto an existing column is an error rather than a no-op, so ops a previous
    # run satisfied are dropped instead of failing the batch.
    existing = set(lancedb.connect(path).open_table(txn.table_name).to_arrow().column_names)
    kept = [op for op in txn.changes if not (isinstance(op, AddColumn) and op.column in existing)]
    kept = [op for op in kept if not (isinstance(op, RenameColumn) and op.column not in existing)]
    if not kept:
        print(f"  {label}/{txn.table_name}: nothing to do")
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


def harmonize_proteins(spec: dict, package: str, sample: str, dry_run: bool) -> None:
    path = lance_db(package, sample)
    targets = (
        lancedb.connect(path)
        .open_table("ProteinSchema")
        .to_arrow()
        .column("target_name")
        .to_pylist()
    )
    organism = spec["organism"]
    ops: list = []
    for position, column in enumerate(("uniprot_id", "gene_name", "protein_name")):
        ops.append(
            AddColumn(
                column=column,
                value_sql="target_name",
                tool="schema_align",
                reason="seed with the target name so the per-target resolution can key on it",
            )
        )
        for target in targets:
            resolved = RESOLVED_BY_NORM.get(_norm(target), (None, None, None))[position]
            if target.startswith(("DAPI", "Blank", "Empty", "HOECHST", "Hoechst")):
                reason = "a counterstain, blank or unused channel rather than an antibody target"
            elif _norm(target) not in RESOLVED_BY_NORM:
                reason = (
                    "not in the resolve_proteins table this corpus has verified; left null "
                    "rather than typed from memory -- resolve against the reference cache later"
                )
            else:
                reason = f"{target} resolves to {resolved}"
            ops.append(
                ReplaceValue(
                    column=column,
                    old_value=target,
                    new_value=resolved,
                    tool="resolve_proteins" if resolved else "schema_align",
                    reason=reason,
                    confidence=1.0 if resolved else 0.0,
                    source="reference_db" if resolved else "none",
                    input_value=target,
                )
            )
    ops += [
        AddColumn(
            column="protein_key",
            value_sql=f"'{organism}' || ':' || coalesce(uniprot_id, target_name)",
            tool="schema_align",
            reason=(
                "corpus-wide identity for the target: the accession where one is verified, "
                "otherwise the channel's own name"
            ),
        ),
        AddColumn(
            column="modification",
            data_type="string",
            tool="schema_align",
            reason="no post-translational modification is targeted by these panels",
        ),
        AddColumn(
            column="antibody_clone",
            data_type="string",
            tool="schema_align",
            reason=(
                "HuBMAP publishes clones in the submission's antibodies.tsv, which the processed "
                "dataset does not carry; left null rather than fetched from a different record"
            ),
        ),
        AddColumn(
            column="organism",
            value=organism,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name; human tissue",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="ProteinSchema", changes=ops),
        {
            "uniprot_id",
            "gene_name",
            "protein_name",
            "protein_key",
            "modification",
            "antibody_clone",
            "organism",
        },
        dry_run,
    )


def harmonize_obs(spec: dict, package: str, sample: str, panel: str, dry_run: bool) -> None:
    path = lance_db(package, sample)
    entry = spec["samples"][sample]
    disease = entry.get("disease")
    ops: list = [
        AddColumn(
            column="spatial_unit",
            value=spec["spatial_unit"],
            tool="schema_align",
            reason="one row is one Cytokit-segmented cell",
        ),
        AddColumn(
            column="assay",
            value=spec["assay"],
            tool="resolve_assays",
            reason="EFO canonical label for the platform (EFO folds CODEX into PhenoCycler)",
        ),
        AddColumn(
            column="technology",
            value=spec["technology"],
            tool="schema_align",
            reason="SpatialTechnology enum member; the controlled name EFO does not separate",
        ),
        AddColumn(
            column="organism",
            value=spec["organism"],
            tool="resolve_organisms",
            reason="NCBITaxon canonical name; human tissue",
        ),
        AddColumn(
            column="z_um",
            data_type="double",
            tool="schema_align",
            reason="the pipeline extracts one focal plane per channel; the cell table is 2-D",
        ),
        AddColumn(
            column="unit_size_um",
            data_type="double",
            tool="schema_align",
            reason="segmented cells have no fixed footprint",
        ),
        AddColumn(
            column="segmentation_method",
            value=spec["segmentation_method"],
            tool="schema_align",
            reason=spec.get(
                "segmentation_note",
                "Cytokit detects nuclei with a U-Net on the nuclear channel and grows cell "
                "boundaries by marker-controlled watershed on the membrane channel",
            ),
        ),
        AddColumn(
            column="tissue",
            value=entry.get("tissue", spec["tissue"]),
            tool="resolve_tissues",
            reason="UBERON canonical label; HuBMAP's organ for the sample",
        ),
        AddColumn(
            column="anatomical_region",
            data_type="string",
            tool="schema_align",
            reason="no per-cell region annotation is published",
        ),
        AddColumn(
            column="disease_state",
            value=entry.get("disease_state", spec.get("disease_state", "unknown")),
            tool="schema_align",
            reason="HuBMAP publishes no diagnosis with the processed dataset",
        ),
        (
            AddColumn(
                column="disease",
                value=disease,
                tool="resolve_diseases",
                reason="MONDO label for this section's diagnosis",
            )
            if disease
            else AddColumn(
                column="disease",
                data_type="string",
                tool="schema_align",
                reason="no diagnosis published: disease is null, not absent",
            )
        ),
        AddColumn(
            column="cell_type",
            data_type="string",
            tool="schema_align",
            reason=(
                "SPRM publishes k-means cluster ids, not cell types; nothing here is a CL term"
            ),
        ),
        AddColumn(
            column="cell_type_original",
            data_type="string",
            tool="schema_align",
            reason="no source cell-type label",
        ),
        AddColumn(
            column="n_counts",
            data_type="double",
            tool="schema_align",
            reason="an imaging proteomics assay counts no transcripts",
        ),
        AddColumn(
            column="n_genes",
            data_type="int64",
            tool="schema_align",
            reason="an imaging proteomics assay detects no genes",
        ),
        AddColumn(
            column="negative_control_counts",
            data_type="double",
            tool="schema_align",
            reason="blank channels are columns of the protein axis, flagged is_control there",
        ),
        AddColumn(
            column="unassigned_counts",
            data_type="double",
            tool="schema_align",
            reason="no transcripts, so nothing to leave unassigned",
        ),
        AddColumn(
            column="cell_area_um2",
            data_type="double",
            tool="schema_align",
            reason=(
                "SPRM's cell_shape.csv is a shape descriptor, not an area, and the mask is not "
                "read; derivable later as total / mean per channel"
            ),
        ),
        AddColumn(
            column="nucleus_area_um2",
            data_type="double",
            tool="schema_align",
            reason="not published per cell",
        ),
        AddColumn(
            column="in_tissue",
            data_type="bool",
            tool="schema_align",
            reason="a segmented cell is in tissue by construction; SPRM states nothing further",
        ),
        AddColumn(
            column="passes_qc",
            data_type="bool",
            tool="schema_align",
            reason="SPRM drops cells it cannot measure but publishes no QC flag on the rest",
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason="the acquisition region and which file the centroid came from",
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
            value=panel,
            tool="join_key",
            reason="the antibody panel measured on this cell",
        ),
    ]
    apply(
        path,
        sample,
        CurationTransaction(table_name="SpatialObs_protein_abundance", changes=ops),
        {op.column for op in ops if hasattr(op, "column")} | {"additional_metadata"},
        dry_run,
    )


def harmonize_dataset(package: str, sample: str, panel: str, dry_run: bool) -> None:
    with open(os.path.join(package, "other_files", "dataset_registry.csv")) as handle:
        row = {r["folder_name"]: r for r in csv.DictReader(handle)}[sample]
    ops: list = [
        AddColumn(column=column, value=row[column], tool="schema_align", reason=reason)
        for column, reason in (
            ("study_name", "the HuBMAP dataset title"),
            ("sample_name", "the acquisition region within the dataset"),
            ("source_dataset_id", "the corpus-side dataset id in data/datasets.csv"),
            ("folder_name", "the per-region folder in this package"),
            ("accession_database", "the consortium that holds the deposit"),
            ("accession_id", "the HuBMAP dataset id"),
            ("data_access_link", "the portal page for the dataset"),
            ("download_url", "the SPRM table this package's matrix was built from"),
            ("dataset_description", "sample, platform and processing summary"),
        )
    ]
    ops += [
        AddColumn(
            column="source_path",
            data_type="string",
            tool="schema_align",
            reason="fetched from the somics mirror of HuBMAP; no local-only source",
        ),
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value=panel,
            tool="join_key",
            reason="every feature space of this dataset was measured with the one panel",
        ),
    ]
    apply(
        lance_db(package, sample),
        sample,
        CurationTransaction(table_name="SpatialDatasetSchema", changes=ops),
        {op.column for op in ops},
        dry_run,
    )


def harmonize_images(package: str, geometry: list[dict], dry_run: bool) -> None:
    """channel_names and n_z_planes onto the package-root SectionImageSchema.

    One row per region and the channel list can differ between regions of one
    dataset in principle, so the list is written per row via ReplaceValue on a
    seeded column keyed by section_id -- which is why section_id is kept on the
    image registry until cleanup drops it.
    """
    path = os.path.join(package, "lance_db")
    names = {g["section_id"]: g["channel_names"] for g in geometry}
    distinct = {json.dumps(v) for v in names.values()}
    ops: list = []
    if len(distinct) == 1:
        ops.append(
            AddColumn(
                column="channel_names",
                value=list(next(iter(names.values()))),
                tool="schema_align",
                reason="the OME-XML channel names of the expression stack, in stored order",
            )
        )
    else:
        raise NotImplementedError(
            "regions of one dataset carry different channel lists; per-row channel_names "
            "need a keyed write this script does not do yet"
        )
    ops.append(
        AddColumn(
            column="n_z_planes",
            value=1,
            tool="schema_align",
            reason="Cytokit extracts the best-focus plane; the stack is 2-D per channel",
        )
    )
    apply(
        path,
        "package",
        CurationTransaction(table_name="SectionImageSchema", changes=ops),
        {"channel_names", "n_z_planes"},
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
    with open(os.path.join(package, "other_files", "sample_geometry.json")) as handle:
        geometry = json.load(handle)
    with open(os.path.join(package, "other_files", "dataset_registry.csv")) as handle:
        panel = next(csv.DictReader(handle))["panel_name"]

    samples = args.samples or list(spec["samples"])
    for sample in samples:
        print(sample)
        harmonize_proteins(spec, package, sample, args.dry_run)
        harmonize_obs(spec, package, sample, panel, args.dry_run)
        harmonize_dataset(package, sample, panel, args.dry_run)
    harmonize_images(package, [g for g in geometry if g["sample"] in samples], args.dry_run)


if __name__ == "__main__":
    main()
