"""Harmonize the per-dataset tables of the CosMx NSCLC package to the atlas schema.

One transaction per table per sample, in the order the schema needs them:

- **GenomicFeatureSchema** — the panel is published as HUGO symbols, so the
  Ensembl id every other dataset in the atlas keys genes on has to be resolved
  before ``feature_id`` (and with it the corpus-wide ``feature_key``) can be
  written. Negative-control probes resolve to nothing by design and keep their
  probe name as their identity.
- **ProteinSchema** — the four morphology stains plus the membrane marker. Only
  CD45 resolves to a single accession: PanCK is an antibody against several
  keratins, CD3 against a three-chain complex, and DAPI and the membrane stain
  are dyes rather than antibody targets at all. Those keep the panel's own name
  as their identity, which is what ``protein_key`` is for.
- **SpatialObs_gene_expression** — the obs table proper. The geometry columns
  are derived in SQL from the vendor's own values so the arithmetic is in the
  audit trail: microns are pixels scaled by the 0.18 um pixel edge, and the cell
  area is the vendor's pixel count scaled by its square.
- **SpatialDatasetSchema** — provenance, one row per feature space.

The protein obs table needs nothing: it exists only to carry the barcode that
joins the two feature spaces, and finalization merges it into the obs table
without validating it.

Run:
    python scripts/harmonize_cosmx_datasets.py [--samples Lung6 ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import os

import lancedb
from polycomb import (
    AddColumn,
    CastColumn,
    CurationApplicator,
    CurationTransaction,
    RenameColumn,
    ReplaceValue,
    default_audit_db_path,
)
from polycomb.genes import resolve_genes

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/cosmx_nsclc_ffpe"
SAMPLES = [
    "Lung5_Rep1",
    "Lung5_Rep2",
    "Lung5_Rep3",
    "Lung6",
    "Lung9_Rep1",
    "Lung9_Rep2",
    "Lung12",
    "Lung13",
]

ORGANISM = "Homo sapiens"
# EFO's own label for the platform, as resolve_assays returns it.
ASSAY = "CosMx SMI"
UM_PER_PX = 0.18
PANEL_NAME = "CosMx Human Universal Cell Characterization RNA Panel (960-plex prototype)"
STUDY = "CosMx_NSCLC"
BASE_URL = "https://nanostring-public-share.s3.us-west-2.amazonaws.com/SMI-Compressed"
LANDING = (
    "https://brukerspatialbiology.com/products/cosmx-spatial-molecular-imager/"
    "ffpe-dataset/nsclc-ffpe-dataset/"
)

# resolve_proteins finds an accession for CD45 only; see the module docstring.
PROTEIN_RESOLUTION = {
    "CD45": {
        "uniprot_id": "P08575",
        "gene_name": "PTPRC",
        "protein_name": "Receptor-type tyrosine-protein phosphatase C",
    },
}

DATASET_DESCRIPTION = (
    "One FFPE section of non-small-cell lung cancer profiled on a CosMx Spatial Molecular "
    "Imager prototype with the 960-plex CosMx Human Universal Cell Characterization RNA panel "
    "(980 measured targets, 20 of them negative-control probes). Cells were segmented from a "
    "morphology stain of PanCK, CD45, CD3, DAPI and a membrane marker at 0.18 um/px. The "
    "vendor's per-field-of-view RGB composites are stitched into one section image in the same "
    "global pixel frame the cell coordinates use, and the mean fluorescence of each morphology "
    "channel within each cell is carried as the protein readout. Released by NanoString (now "
    "Bruker Spatial Biology) alongside He et al. 2022, Nat Biotechnol."
)


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


def gene_replacements(symbols: list[str]) -> list[ReplaceValue]:
    """Symbol -> Ensembl gene id, as ops against a column seeded with symbols.

    Every distinct symbol gets an op, including the ones that resolve to
    nothing: those are set to null rather than left holding a symbol in a
    column the schema declares as an Ensembl cross-reference.
    """
    distinct = list(dict.fromkeys(symbols))
    report = resolve_genes(distinct, organism="human", input_type="symbol")
    by_input = {res.input_value: res for res in report.results}
    print(f"  resolve_genes: {report.resolved}/{report.total} symbols -> Ensembl")

    ops: list[ReplaceValue] = []
    for symbol in distinct:
        res = by_input.get(symbol)
        ensembl = getattr(res, "ensembl_gene_id", None) if res else None
        ops.append(
            ReplaceValue(
                column="ensembl_gene_id",
                old_value=symbol,
                new_value=ensembl,
                tool="resolve_genes",
                reason=(
                    "panel gene symbol -> Ensembl gene id"
                    if ensembl
                    else "symbol resolves to no Ensembl gene; the cross-reference stays null"
                ),
                confidence=getattr(res, "confidence", None) if res else None,
                source=getattr(res, "source", None) if res else None,
                input_value=symbol,
            )
        )
    return ops


def harmonize_genes(sample: str, replacements: list[ReplaceValue], *, dry_run: bool) -> None:
    ops: list = [
        AddColumn(
            column="ensembl_gene_id",
            value_sql="gene_name",
            tool="schema_align",
            reason="seed the cross-reference column with the symbols about to be resolved",
        ),
        *replacements,
        AddColumn(
            column="feature_id",
            value_sql="coalesce(ensembl_gene_id, var_index)",
            tool="schema_align",
            reason=(
                "the measured feature's identity: its Ensembl gene id where the panel's symbol "
                "resolved to one, and otherwise the panel's own probe name, which is what a "
                "negative-control probe has instead"
            ),
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="a human panel; NCBITaxon canonical name",
        ),
        AddColumn(
            column="feature_type",
            value_sql="CAST(is_negative_probe AS STRING)",
            tool="schema_align",
            reason="staging the negative-probe flag before mapping it onto the FeatureType enum",
        ),
        ReplaceValue(
            column="feature_type",
            old_value="true",
            new_value="negative_control_probe",
            tool="schema_align",
            reason="NegPrb* rows are the panel's negative-control probes",
        ),
        ReplaceValue(
            column="feature_type",
            old_value="false",
            new_value="gene",
            tool="schema_align",
            reason="every other row is a gene target",
        ),
        RenameColumn(
            column="is_negative_probe",
            new_name="is_control",
            tool="schema_align",
            reason="a negative probe is exactly the control this flag means",
        ),
        AddColumn(
            column="ensembl_version",
            data_type="string",
            tool="schema_align",
            reason=(
                "the panel is published as symbols with no reference release; the Ensembl ids "
                "above come from the resolver's own reference, not from the source"
            ),
        ),
        AddColumn(
            column="feature_key",
            value_sql=f"'{ORGANISM}' || ':' || feature_id",
            tool="schema_align",
            reason=(
                "corpus-wide stable identity: organism composed with the measured feature id, so "
                "a gene dedupes across datasets while probe names stay species-distinct"
            ),
        ),
    ]
    apply(
        sample,
        CurationTransaction(table_name="GenomicFeatureSchema", changes=ops),
        {
            "ensembl_gene_id",
            "feature_id",
            "organism",
            "feature_type",
            "is_control",
            "ensembl_version",
            "feature_key",
        },
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# ProteinSchema
# ---------------------------------------------------------------------------


def harmonize_proteins(sample: str, targets: list[str], *, dry_run: bool) -> None:
    # A resolution that lands on some rows only has to be keyed on something the
    # column already holds, so each resolved column is seeded with the target
    # name and then replaced per target — the same staging pattern the gene
    # symbols use.
    ops: list = []
    for column in ("uniprot_id", "gene_name", "protein_name"):
        ops.append(
            AddColumn(
                column=column,
                value_sql="target_name",
                tool="schema_align",
                reason="seed with the target name so the per-target resolution can key on it",
            )
        )
        for target in targets:
            resolved = PROTEIN_RESOLUTION.get(target, {}).get(column)
            ops.append(
                ReplaceValue(
                    column=column,
                    old_value=target,
                    new_value=resolved,
                    tool="resolve_proteins",
                    reason=(
                        f"{target} resolves to a single UniProt entry"
                        if resolved
                        else f"{target} maps to no single UniProt entry; the field stays null"
                    ),
                    confidence=0.9 if resolved else 0.0,
                    source="lancedb" if resolved else "none",
                    input_value=target,
                )
            )
    ops += [
        AddColumn(
            column="protein_key",
            value_sql=f"'{ORGANISM}' || ':' || coalesce(uniprot_id, target_name)",
            tool="schema_align",
            reason=(
                "corpus-wide stable identity for the target: the accession where one resolved, "
                "and otherwise the panel's own name — PanCK is raised against several keratins, "
                "CD3 against a three-chain complex, and DAPI and the membrane stain are dyes, so "
                "none of them has a single accession to key on"
            ),
        ),
        AddColumn(
            column="modification",
            data_type="string",
            tool="schema_align",
            reason="none of these antibodies is specific to a post-translational state",
        ),
        AddColumn(
            column="antibody_clone",
            data_type="string",
            tool="schema_align",
            reason="the vendor does not publish clones for the morphology kit",
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="human tissue; NCBITaxon canonical name",
        ),
        AddColumn(
            column="is_control",
            value=False,
            tool="schema_align",
            reason=(
                "all five channels are measurements; the two dyes are segmentation aids rather "
                "than the isotype or normalization controls this flag means"
            ),
        ),
    ]
    apply(
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
            "is_control",
        },
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# SpatialObs
# ---------------------------------------------------------------------------


def harmonize_obs(sample: str, *, dry_run: bool) -> None:
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
            value="cosmx",
            tool="schema_align",
            reason="SpatialTechnology enum member for this platform",
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name; human donors",
        ),
        # Geometry. x_px/y_px are already in the stitched image's frame; microns
        # are that frame scaled by the pixel edge, so the two agree by
        # construction rather than by a second convention.
        AddColumn(
            column="x_um",
            value_sql="x_px * um_per_px",
            tool="schema_align",
            reason="column position in the section image scaled by the 0.18 um pixel edge",
        ),
        AddColumn(
            column="y_um",
            value_sql="y_px * um_per_px",
            tool="schema_align",
            reason="row position in the section image scaled by the 0.18 um pixel edge",
        ),
        AddColumn(
            column="z_um",
            data_type="double",
            tool="schema_align",
            reason="the published cell table is 2-D; the z-stacks are not part of this release",
        ),
        RenameColumn(
            column="um_per_px",
            new_name="pixel_size_um",
            tool="schema_align",
            reason="the vendor's 180 nm pixel edge is the micron/pixel conversion for the image",
        ),
        AddColumn(
            column="unit_size_um",
            data_type="double",
            tool="schema_align",
            reason="segmented cells have no fixed footprint; their extent is in cell_area_um2",
        ),
        AddColumn(
            column="cell_area_um2",
            # The cast is load-bearing: the pixel count is an integer column and
            # LanceDB will not widen it to multiply by a float literal.
            value_sql=f"CAST(Area_px AS DOUBLE) * {UM_PER_PX * UM_PER_PX}",
            tool="schema_align",
            reason="the segmentation's pixel count scaled by the square of the pixel edge",
        ),
        AddColumn(
            column="nucleus_area_um2",
            data_type="double",
            tool="schema_align",
            reason="the flat files report one whole-cell area and no separate nuclear area",
        ),
        AddColumn(
            column="segmentation_method",
            value="cell_boundary_stain",
            tool="schema_align",
            reason=(
                "cells are segmented from the morphology antibodies staining membrane, PanCK, "
                "CD45 and CD3 together with the DAPI nuclear signal"
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
            value="diseased",
            tool="schema_align",
            reason="every section is tumour tissue from an NSCLC resection",
        ),
        AddColumn(
            column="disease",
            value="non-small cell lung carcinoma",
            tool="resolve_diseases",
            reason="MONDO canonical label; denormalized from the section",
        ),
        AddColumn(
            column="cell_type",
            data_type="string",
            tool="schema_align",
            reason=(
                "the flat files carry no cell-type call; the paper's calls are published only "
                "inside an R Giotto object, which this package does not read"
            ),
        ),
        AddColumn(
            column="cell_type_original",
            data_type="string",
            tool="schema_align",
            reason="no published per-cell label to preserve",
        ),
        AddColumn(
            column="unassigned_counts",
            data_type="double",
            tool="schema_align",
            reason=(
                "transcripts assigned to no cell are reported once per field of view, not per "
                "cell, so there is no per-row value"
            ),
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
                "the released cell table is already the vendor's filtered set, and carries no "
                "per-cell verdict of its own"
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
            reason="distinct genes detected per cell",
        ),
        CastColumn(
            column="negative_control_counts",
            data_type="double",
            tool="schema_align",
            reason="integer NegPrb totals -> the schema's float column",
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason=(
                "preserve the source columns with no schema field: the field-of-view and cell "
                "numbers the vendor keys cells on, the segmentation's aspect ratio and bounding "
                "box, and the maximum fluorescence per morphology channel"
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
            "x_um",
            "y_um",
            "z_um",
            "pixel_size_um",
            "unit_size_um",
            "cell_area_um2",
            "nucleus_area_um2",
            "segmentation_method",
            "tissue",
            "anatomical_region",
            "disease_state",
            "disease",
            "cell_type",
            "cell_type_original",
            "unassigned_counts",
            "in_tissue",
            "passes_qc",
            "n_counts",
            "n_genes",
            "negative_control_counts",
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


def harmonize_dataset(sample: str, *, dry_run: bool) -> None:
    prefix = f"{BASE_URL}/{sample}/{sample}%20SMI%20Flat%20data.tar.gz"
    ops: list = [
        AddColumn(
            column="study_name",
            value="CosMx SMI NSCLC FFPE dataset",
            tool="schema_align",
            reason="the vendor release these eight sections belong to",
        ),
        AddColumn(
            column="sample_name",
            value=sample,
            tool="schema_align",
            reason="the sample label the vendor and the paper both use",
        ),
        AddColumn(
            column="source_dataset_id",
            value="bruker_cosmx_nsclc_ffpe",
            tool="schema_align",
            reason="the corpus-side dataset id in data/datasets.csv",
        ),
        AddColumn(
            column="folder_name",
            value=sample,
            tool="schema_align",
            reason="the per-sample folder in the SMI-Compressed release",
        ),
        AddColumn(
            column="accession_database",
            value="Bruker Spatial Biology public share",
            tool="schema_align",
            reason="the release is an S3 public share, not a deposit in an archive",
        ),
        AddColumn(
            column="accession_id",
            value=f"SMI-Compressed/{sample}",
            tool="schema_align",
            reason="path of this sample within the public share",
        ),
        AddColumn(
            column="data_access_link",
            value=LANDING,
            tool="schema_align",
            reason="the vendor's landing page for the dataset",
        ),
        AddColumn(
            column="download_url",
            value=prefix,
            tool="schema_align",
            reason=(
                "the flat-file bundle every feature space here is derived from: the expression "
                "matrix, the cell metadata, and the field-of-view composites"
            ),
        ),
        AddColumn(
            column="source_path",
            data_type="string",
            tool="schema_align",
            reason="fetched online; no local-only source",
        ),
        AddColumn(
            column="dataset_description",
            value=DATASET_DESCRIPTION,
            tool="schema_align",
            reason="sample-prep and protocol summary from the vendor record and the paper",
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
    parser.add_argument("--samples", nargs="*", default=SAMPLES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # The panel is identical in all eight samples, so the symbols are resolved
    # once and the same ops are replayed per table.
    db = lancedb.connect(lance_db(args.samples[0]))
    symbols = [
        s
        for s in db.open_table("GenomicFeatureSchema").to_arrow().column("gene_name").to_pylist()
        if s
    ]
    replacements = gene_replacements(symbols)
    targets = db.open_table("ProteinSchema").to_arrow().column("target_name").to_pylist()

    for sample in args.samples:
        print(sample)
        harmonize_genes(sample, replacements, dry_run=args.dry_run)
        harmonize_proteins(sample, targets, dry_run=args.dry_run)
        harmonize_obs(sample, dry_run=args.dry_run)
        harmonize_dataset(sample, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
