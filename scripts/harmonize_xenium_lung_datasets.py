"""Harmonize the per-dataset tables of the Xenium lung package to the atlas schema.

One transaction per table per sample, in the order the schema needs them:

- **GenomicFeatureSchema** — no gene resolution is needed here, unlike the CosMx
  panel: 10x publishes the Xenium feature table with Ensembl gene ids already
  attached, so ``feature_id`` is the source's own ``gene_id``. The 149 control
  and blank codewords carry a codeword name in that column instead, which is
  their identity, and their gene columns are nulled rather than left holding a
  codeword name in a field the schema declares as a gene symbol.
- **SpatialObs_gene_expression** — the obs table proper. The package builder
  already emitted the geometry and count columns under their schema names, so
  what is added here is the constant annotation (platform, organism, tissue,
  disease) and the enum members, plus the join keys.
- **SpatialDatasetSchema** — provenance, one row per feature space, read from
  the ``dataset_registry.csv`` the assembler wrote so that the per-sample text
  lives in one place rather than being duplicated between the two scripts.

``disease`` and ``disease_state`` are the only obs columns that differ between
the two samples, which is the point of the pair: same panel, same run, same
pixel size, one healthy section and one adenocarcinoma section.

Run:
    python scripts/harmonize_xenium_lung_datasets.py [--samples ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import os

import lancedb
import pandas as pd
from polycomb import (
    AddColumn,
    CastColumn,
    CurationApplicator,
    CurationTransaction,
    MergeColumns,
    RenameColumn,
    ReplaceValue,
    default_audit_db_path,
)

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/xenium_lung_preview"
DATASET_REGISTRY = os.path.join(PACKAGE_ROOT, "other_files", "dataset_registry.csv")

ORGANISM = "Homo sapiens"
# EFO's own label for the platform, as resolve_assays returns it.
ASSAY = "10x Xenium"
PANEL_NAME = "Xenium Human Lung Panel v1 + hLung_100g Add-On"

SAMPLES = {
    "Xenium_Preview_Human_Non_diseased_Lung_With_Add_on_FFPE": {
        "disease_state": "healthy",
        "disease": None,
        "disease_reason": (
            "a non-diseased donor section; disease stays null and disease_state carries the "
            "healthy verdict, which is how the schema keeps healthy distinct from unannotated"
        ),
    },
    "Xenium_Preview_Human_Lung_Cancer_With_Add_on_2_FFPE": {
        "disease_state": "diseased",
        "disease": "lung adenocarcinoma",
        "disease_reason": (
            "MONDO canonical label; denormalized from the section. 10x describes the section as "
            "invasive adenocarcinoma, and data/st_corpus.csv curates it to MONDO:0005061 at its "
            "most specific level — coarser groupings are recovered by ontology traversal"
        ),
    },
}

# The 10x feature table's own type labels -> the schema's FeatureType members.
FEATURE_TYPES = {
    "Gene Expression": "gene",
    "Negative Control Probe": "negative_control_probe",
    "Negative Control Codeword": "negative_control_codeword",
    "Unassigned Codeword": "blank_codeword",
}


def lance_db(sample: str) -> str:
    return os.path.join(PACKAGE_ROOT, sample, "lance_db")


def apply(sample: str, txn: CurationTransaction, allowed: set[str], *, dry_run: bool) -> None:
    """Apply one transaction, dropping ops a previous run already satisfied."""
    path = lance_db(sample)
    existing = set(lancedb.connect(path).open_table(txn.table_name).to_arrow().column_names)
    kept = [op for op in txn.changes if not (isinstance(op, AddColumn) and op.column in existing)]
    kept = [op for op in kept if not (isinstance(op, RenameColumn) and op.column not in existing)]
    if not kept:
        print(f"  {sample}/{txn.table_name}: already harmonized")
        return

    applicator = CurationApplicator(path, audit_db_path=default_audit_db_path(path))
    try:
        result = applicator.apply(
            CurationTransaction(table_name=txn.table_name, changes=kept),
            allowed_columns=allowed,
            dry_run=dry_run,
        )
        print(f"  {sample}/{txn.table_name}: status={result.status} ({len(kept)} op(s))")
        if result.error:
            raise RuntimeError(f"{sample}/{txn.table_name}: {result.error}")
    finally:
        applicator.close()


# ---------------------------------------------------------------------------
# GenomicFeatureSchema
# ---------------------------------------------------------------------------


def gene_rows(sample: str) -> list[dict]:
    """The per-feature values that depend on the vendor's feature type.

    LanceDB's SQL dialect has no ``CASE WHEN``, so the three columns that are
    populated for panel genes and null for control codewords are computed here
    and applied as one keyed batch rather than as per-row SQL.
    """
    table = lancedb.connect(lance_db(sample)).open_table("GenomicFeatureSchema").to_arrow()
    # Staging makes the source's leading column (10x's gene_id) the table's key
    # column. It was called var_index when this package was first ingested and
    # is var_key in current polycomb; accept either so the script works against
    # both, rather than pinning us to one skill version.
    key = "var_key" if "var_key" in table.column_names else "var_index"
    ids = table.column(key).to_pylist()
    names = table.column("gene_name").to_pylist()
    types = table.column("feature_type").to_pylist()
    rows = []
    for feature_id, gene_name, raw_type in zip(ids, names, types, strict=True):
        if raw_type not in FEATURE_TYPES:
            raise ValueError(f"{sample}: unknown 10x feature type {raw_type!r}")
        is_gene = FEATURE_TYPES[raw_type] == "gene"
        rows.append(
            {
                "feature_id": feature_id,
                "gene_name": gene_name if is_gene else None,
                "ensembl_gene_id": feature_id if is_gene else None,
                "is_control": not is_gene,
            }
        )
    return rows


def feature_key_column(sample: str) -> str:
    """Whichever name staging gave the feature-identity column."""
    table = lancedb.connect(lance_db(sample)).open_table("GenomicFeatureSchema").to_arrow()
    return "var_key" if "var_key" in table.column_names else "var_index"


def harmonize_genes(sample: str, *, dry_run: bool) -> None:
    rows = gene_rows(sample)
    ops: list = [
        RenameColumn(
            column=feature_key_column(sample),
            new_name="feature_id",
            tool="schema_align",
            reason=(
                "the measured feature's identity as published: an Ensembl gene id for the 392 "
                "panel targets, and the codeword's own name for the 149 controls and blanks"
            ),
        ),
        # feature_type is staged with the vendor's labels and mapped onto the
        # enum in place, so the audit trail records each label's destination.
        *[
            ReplaceValue(
                column="feature_type",
                old_value=raw,
                new_value=member,
                tool="schema_align",
                reason=f"10x feature table type {raw!r} -> FeatureType.{member}",
            )
            for raw, member in FEATURE_TYPES.items()
        ],
        AddColumn(
            column="is_control",
            data_type="bool",
            tool="schema_align",
            reason="null-initialized so the keyed batch below can fill it",
        ),
        AddColumn(
            column="ensembl_gene_id",
            data_type="string",
            tool="schema_align",
            reason="null-initialized so the keyed batch below can fill it",
        ),
        # One batch fills the three columns whose value depends on whether the
        # row is a panel gene or a control codeword. The source repeats the
        # codeword name in gene_name; a codeword maps to no gene, so the symbol
        # is nulled there rather than left holding a codeword name in a field
        # the schema declares as a gene symbol. Ensembl ids need no resolver:
        # 10x publishes the panel against Ensembl already.
        MergeColumns(
            column="gene_name",
            key_column="feature_id",
            rows=rows,
            tool="schema_align",
            reason=(
                "gene_name, ensembl_gene_id and is_control keyed on the published feature id: "
                "populated for the 392 panel targets, null/true for the 149 control and blank "
                "codewords"
            ),
            source="10x cell_feature_matrix.h5 feature table",
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="a human panel; NCBITaxon canonical name",
        ),
        AddColumn(
            column="ensembl_version",
            data_type="string",
            tool="schema_align",
            reason="10x does not publish the Ensembl release the panel was designed against",
        ),
        AddColumn(
            column="feature_key",
            value_sql=f"'{ORGANISM}' || ':' || feature_id",
            tool="schema_align",
            reason=(
                "corpus-wide stable identity: organism composed with the measured feature id, so "
                "a gene dedupes across datasets while codeword names stay species-distinct"
            ),
        ),
    ]
    apply(
        sample,
        CurationTransaction(table_name="GenomicFeatureSchema", changes=ops),
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
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# SpatialObs
# ---------------------------------------------------------------------------


def harmonize_obs(sample: str, *, dry_run: bool) -> None:
    spec = SAMPLES[sample]
    ops: list = [
        AddColumn(
            column="spatial_unit",
            value="cell",
            tool="schema_align",
            reason="one row is one segmented cell",
        ),
        AddColumn(
            column="assay",
            value=ASSAY,
            tool="resolve_assays",
            reason="EFO canonical label for the platform",
        ),
        AddColumn(
            column="technology",
            value="xenium",
            tool="schema_align",
            reason="SpatialTechnology enum member for this platform",
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name; human donors",
        ),
        AddColumn(
            column="z_um",
            data_type="double",
            tool="schema_align",
            reason=(
                "the published cell table reports a 2-D centroid; the z extent of the run is in "
                "the morphology stack, which is not ingested"
            ),
        ),
        AddColumn(
            column="unit_size_um",
            data_type="double",
            tool="schema_align",
            reason="segmented cells have no fixed footprint; their extent is in cell_area_um2",
        ),
        AddColumn(
            column="segmentation_method",
            value="nucleus_expansion",
            tool="schema_align",
            reason=(
                "Xenium Onboard Analysis 1.3.0 segments cells by expanding the DAPI nucleus "
                "boundary up to 15 um; the boundary-stain multimodal segmentation is a later "
                "chemistry and was not used on this preview run"
            ),
        ),
        AddColumn(
            column="tissue",
            value="lung",
            tool="resolve_tissues",
            reason="UBERON canonical label; denormalized from the section",
        ),
        AddColumn(
            column="anatomical_region",
            data_type="string",
            tool="schema_align",
            reason="the release publishes no per-cell region annotation",
        ),
        AddColumn(
            column="disease_state",
            value=spec["disease_state"],
            tool="schema_align",
            reason=f"the whole section is {spec['disease_state']} tissue",
        ),
        # The healthy section gets a typed null column rather than a value; the
        # adenocarcinoma section gets the MONDO label.
        AddColumn(
            column="disease",
            tool="resolve_diseases" if spec["disease"] else "schema_align",
            reason=spec["disease_reason"],
            **({"value": spec["disease"]} if spec["disease"] else {"data_type": "string"}),
        ),
        AddColumn(
            column="cell_type",
            data_type="string",
            tool="schema_align",
            reason=(
                "10x publishes only unannotated graph-based clusters for this release, which "
                "are not cell-type calls"
            ),
        ),
        AddColumn(
            column="cell_type_original",
            data_type="string",
            tool="schema_align",
            reason="no published per-cell label to preserve",
        ),
        AddColumn(
            column="in_tissue",
            data_type="bool",
            tool="schema_align",
            reason="a segmented cell is in tissue by construction; the source publishes no flag",
        ),
        AddColumn(
            column="passes_qc",
            data_type="bool",
            tool="schema_align",
            reason=(
                "the released cell table is already the instrument's own set, and carries no "
                "per-cell verdict for a consumer to re-apply"
            ),
        ),
        CastColumn(
            column="n_counts",
            data_type="double",
            tool="schema_align",
            reason="integer transcript totals -> the schema's float column",
        ),
        CastColumn(
            column="n_genes",
            data_type="int64",
            tool="schema_align",
            reason="distinct panel genes detected per cell",
        ),
        CastColumn(
            column="negative_control_counts",
            data_type="double",
            tool="schema_align",
            reason=(
                "the assay's specificity metric: negative-control probe and codeword counts "
                "summed, integer -> the schema's float column"
            ),
        ),
        CastColumn(
            column="unassigned_counts",
            data_type="double",
            tool="schema_align",
            reason="blank/unassigned codeword counts, integer -> the schema's float column",
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason=(
                "preserve the source columns with no schema field: the three control buckets "
                "Xenium reports separately, and the vendor's own total_counts"
            ),
        ),
        AddColumn(
            column="section_uid_TissueSectionSchema_join",
            value_sql="section_id",
            tool="join_key",
            reason="the section this cell was measured on",
        ),
        AddColumn(
            column="donor_uid_DonorSchema_join",
            value_sql="donor_id",
            tool="join_key",
            reason="the donor, denormalized from the section",
        ),
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value_sql="panel_name",
            tool="join_key",
            reason="the probe panel measured on this cell",
        ),
    ]
    apply(
        sample,
        CurationTransaction(table_name="SpatialObs_gene_expression", changes=ops),
        {
            "spatial_unit",
            "assay",
            "technology",
            "organism",
            "z_um",
            "unit_size_um",
            "segmentation_method",
            "tissue",
            "anatomical_region",
            "disease_state",
            "disease",
            "cell_type",
            "cell_type_original",
            "in_tissue",
            "passes_qc",
            "n_counts",
            "n_genes",
            "negative_control_counts",
            "unassigned_counts",
            "additional_metadata",
            "section_uid_TissueSectionSchema_join",
            "donor_uid_DonorSchema_join",
            "panel_uid_PanelSchema_join",
        },
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# SpatialDatasetSchema
# ---------------------------------------------------------------------------


def harmonize_dataset(sample: str, registry: pd.DataFrame, *, dry_run: bool) -> None:
    row = registry.loc[registry.folder_name == sample]
    if len(row) != 1:
        raise ValueError(f"{sample}: {len(row)} row(s) in {DATASET_REGISTRY}, expected 1")
    row = row.iloc[0]

    ops: list = [
        AddColumn(
            column="study_name",
            value=row.study_name,
            tool="schema_align",
            reason="the vendor release both sections belong to",
        ),
        AddColumn(
            column="sample_name",
            value=row.sample_name,
            tool="schema_align",
            reason="the sample label 10x uses for this section",
        ),
        AddColumn(
            column="source_dataset_id",
            value=str(row.source_dataset_id),
            tool="schema_align",
            reason="the corpus-side dataset id both sections carry in data/st_corpus.csv",
        ),
        AddColumn(
            column="folder_name",
            value=sample,
            tool="schema_align",
            reason="the 10x sample folder name; the most stable source key for this release",
        ),
        AddColumn(
            column="accession_database",
            value=row.accession_database,
            tool="schema_align",
            reason="a vendor showcase release, not a deposit in an archive",
        ),
        AddColumn(
            column="accession_id",
            data_type="string",
            tool="schema_align",
            reason="10x assigns no accession within its dataset catalogue",
        ),
        AddColumn(
            column="data_access_link",
            value=row.data_access_link,
            tool="schema_align",
            reason="the 10x landing page describing both sections of the release",
        ),
        AddColumn(
            column="download_url",
            value=row.download_url,
            tool="schema_align",
            reason=(
                "the outs bundle every feature space here is derived from: the cell feature "
                "matrix, the cell table, and the morphology projection"
            ),
        ),
        AddColumn(
            column="source_path",
            value=row.source_path,
            tool="schema_align",
            reason="the downloaded bundle the package was built from, kept alongside it",
        ),
        AddColumn(
            column="dataset_description",
            value=row.dataset_description,
            tool="schema_align",
            reason="sample-prep and protocol summary from the 10x record and experiment.xenium",
        ),
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value=PANEL_NAME,
            tool="join_key",
            reason="every feature space of this dataset was measured with the one panel",
        ),
    ]
    apply(
        sample,
        CurationTransaction(table_name="SpatialDatasetSchema", changes=ops),
        {
            "study_name",
            "sample_name",
            "source_dataset_id",
            "folder_name",
            "accession_database",
            "accession_id",
            "data_access_link",
            "download_url",
            "source_path",
            "dataset_description",
            "panel_uid_PanelSchema_join",
        },
        dry_run=dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", nargs="*", default=list(SAMPLES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    registry = pd.read_csv(DATASET_REGISTRY)
    for sample in args.samples:
        print(sample)
        harmonize_genes(sample, dry_run=args.dry_run)
        harmonize_obs(sample, dry_run=args.dry_run)
        harmonize_dataset(sample, registry, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
