"""Harmonize the per-region tables of the monkman NSCLC CODEX package.

Four tables per region:

- **ProteinSchema** — the 58-column feature axis. 31 of the 36 antibody targets
  resolve to a single UniProt accession, half of them only after the CD number is
  put through its HCDM gene symbol (the resolver knows PECAM1, not CD31). Five do
  not, for reasons that are about the antibodies rather than the lookup: CD45RA
  and CD45RO are isoform-specific antibodies against one PTPRC accession and
  would collide with CD45 on `protein_key`, CD15 is a carbohydrate epitope with
  no protein of its own, PanCK is raised against several keratins, and HLA-DR
  against a two-chain complex. Those keep the panel's own name as their identity,
  which is what `protein_key` is for. The 22 counterstain, blank, and unused
  channels keep their channel name and are flagged `is_control`.

- **SpatialObs_protein_abundance** — the obs table. The geometry needs no
  arithmetic here: the source reports microns directly and the builder derived
  the pixel frame, so this transaction is schema alignment and the cell-type
  mapping.

- The cell types are the paper's own labels, which are cluster names rather than
  ontology terms, so each is first mapped to the CL concept it denotes and the
  resolved label written to `cell_type`. Four tumour states collapse onto one CL
  term and two labels (`Unclassified`, `Artifact`) denote no cell type at all and
  go to null; every published label survives verbatim in `cell_type_original`.

- **SpatialDatasetSchema** — provenance, one row per feature space.

Run:
    python scripts/harmonize_monkman_datasets.py [--regions reg001 ...] [--dry-run]
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
    default_audit_db_path,
)

PACKAGE_ROOT = "/home/ubuntu/polycomb_data_packages/monkman_nsclc_codex"
STAGING_ROOT = "/home/ubuntu/datasets/monkman_nsclc_codex/staging"

ORGANISM = "Homo sapiens"
# EFO's label for the fluidics platform. EFO carries "CODEX" as a synonym of both
# PhenoCycler (EFO:0700002, the fluidics system this study used) and
# PhenoCycler-Fusion (EFO:0700001, that system paired with a Fusion imager); the
# deposit evidences the former and says nothing about the imager.
ASSAY = "PhenoCycler"
UM_PER_PX = 0.3775
PANEL_NAME = "Monkman NSCLC 36-plex CODEX antibody panel"
STUDY = "Monkman_NSCLC_CODEX"
RECORD_URL = "https://zenodo.org/records/10258578"
DOI = "10.5281/zenodo.10258578"

# target -> (uniprot_id, gene_name, protein_name), from resolve_proteins. The
# commented targets went through their gene symbol; see the module docstring.
PROTEIN_RESOLUTION: dict[str, tuple[str | None, str | None, str | None]] = {
    "Siglec8": ("Q9NYZ4", "SIGLEC8", "Sialic acid-binding Ig-like lectin 8"),
    "CD4": ("P01730", "CD4", "T-cell surface glycoprotein CD4"),
    "CD44": ("P16070", "CD44", "CD44 antigen"),
    "CD107a": ("P11279", "LAMP1", "Lysosome-associated membrane glycoprotein 1"),
    "CD20": ("P11836", "MS4A1", "B-lymphocyte antigen CD20"),
    "CD38": ("P28907", "CD38", "ADP-ribosyl cyclase/cyclic ADP-ribose hydrolase 1"),
    "CD68": ("P34810", "CD68", "Macrosialin"),
    "CD34": ("P28906", "CD34", "Hematopoietic progenitor cell antigen CD34"),
    "CD45": ("P08575", "PTPRC", "Receptor-type tyrosine-protein phosphatase C"),
    "CD11b": ("P11215", "ITGAM", "Integrin alpha-M"),
    "CD11c": ("P20702", "ITGAX", "Integrin alpha-X"),
    "Podoplanin": ("Q86YL7", "PDPN", "Podoplanin"),
    "SPP1": ("P10451", "SPP1", "Osteopontin"),
    "FoxP3": ("Q9BZS1", "FOXP3", "Forkhead box protein P3"),
    "Vimentin": ("P08670", "VIM", "Vimentin"),
    "CD14": ("P08571", "CD14", "Monocyte differentiation antigen CD14"),
    "CD163": ("Q86VB7", "CD163", "Scavenger receptor cysteine-rich type 1 protein M130"),
    "CD3e": ("P07766", "CD3E", "T-cell surface glycoprotein CD3 epsilon chain"),
    "PGP9.5": ("P09936", "UCHL1", "Ubiquitin carboxyl-terminal hydrolase isozyme L1"),
    "CD31": ("P16284", "PECAM1", "Platelet endothelial cell adhesion molecule"),
    "CD141": ("P07204", "THBD", "Thrombomodulin"),
    "CD197": ("P32248", "CCR7", "C-C chemokine receptor type 7"),
    "RORgammaT": ("P51449", "RORC", "Nuclear receptor ROR-gamma"),
    "CD8": ("P01732", "CD8A", "T-cell surface glycoprotein CD8 alpha chain"),
    "GranzymeB": ("P10144", "GZMB", "Granzyme B"),
    "CD25": ("P01589", "IL2RA", "Interleukin-2 receptor subunit alpha"),
    "CD21": ("P20023", "CR2", "Complement receptor type 2"),
    "CD56": ("P13591", "NCAM1", "Neural cell adhesion molecule 1"),
    "Ki67": ("P46013", "MKI67", "Proliferation marker protein Ki-67"),
    "CD117": ("P10721", "KIT", "Mast/stem cell growth factor receptor Kit"),
    "CD183": ("P49682", "CXCR3", "C-X-C chemokine receptor type 3"),
    # Isoform-specific antibodies. The gene is PTPRC either way, but sharing its
    # accession would give CD45, CD45RA and CD45RO one protein_key between them.
    "CD45RA": (None, "PTPRC", "Receptor-type tyrosine-protein phosphatase C isoform RA"),
    "CD45RO": (None, "PTPRC", "Receptor-type tyrosine-protein phosphatase C isoform RO"),
}

# Why the remaining five targets carry no accession, quoted into the audit trail.
NO_ACCESSION_REASON = {
    "CD15": "CD15 is the Lewis-x carbohydrate epitope, not a protein product",
    "PanCK": "the pan-cytokeratin antibody is raised against several keratins",
    "HLA-DR": "HLA-DR is a two-chain MHC class II complex, not a single gene product",
    "CD45RA": "an isoform-specific antibody; the PTPRC accession belongs to CD45",
    "CD45RO": "an isoform-specific antibody; the PTPRC accession belongs to CD45",
}

# Published label -> (CL label as resolve_ontology_terms returns it, why).
CELL_TYPE_RESOLUTION: dict[str, tuple[str | None, str]] = {
    "Tumour": ("malignant cell", "the paper's tumour cluster"),
    "Proliferating Tumour": (
        "malignant cell",
        "a Ki67-high state of the tumour cluster, not a distinct CL type",
    ),
    "HLADR Tumour": (
        "malignant cell",
        "an HLA-DR-high state of the tumour cluster, not a distinct CL type",
    ),
    "CD44 Tumour": (
        "malignant cell",
        "a CD44-high state of the tumour cluster, not a distinct CL type",
    ),
    "B Cells": ("B cell", "CD20-positive B cell cluster"),
    "CD4 Cells": ("CD4-positive, alpha-beta T cell", "CD3e/CD4-positive cluster"),
    "CD8 Cells": ("CD8-positive, alpha-beta T cell", "CD3e/CD8-positive cluster"),
    "Effector CD4": (
        "effector CD4-positive, alpha-beta T cell",
        "the paper's effector CD4 cluster",
    ),
    "Treg": ("regulatory T cell", "FoxP3/CD25-positive cluster"),
    "CCR7+ CD8/CD4 Cells": (
        "T cell",
        "a mixed CD4 and CD8 CCR7-positive cluster; the common ancestor is all CL supports",
    ),
    "Lymphocytes": ("lymphocyte", "a lineage-ambiguous lymphoid cluster"),
    "Proliferating Lymphocytes": (
        "lymphocyte",
        "a Ki67-high lymphoid cluster; the proliferative state is not a CL type",
    ),
    "Monocytes": ("monocyte", "CD14-positive cluster"),
    "Macrophages": ("macrophage", "CD68/CD163-positive cluster"),
    "Granulocytes": ("granulocyte", "CD15-positive cluster"),
    "Mast Cells": ("mast cell", "CD117-positive cluster"),
    "Blood Vessels": ("endothelial cell of vascular tree", "CD31-positive vascular cluster"),
    "Lymphatics": ("endothelial cell of lymphatic vessel", "podoplanin-positive cluster"),
    "Stroma": ("stromal cell", "vimentin-positive stromal cluster"),
    "Unclassified": (None, "cells the clustering could not assign; not a cell type"),
    "Artifact": (None, "objects the authors marked as segmentation artifacts"),
}

DATASET_DESCRIPTION = (
    "One core of a non-small-cell lung cancer tissue microarray imaged on an Akoya CODEX "
    "(PhenoCycler) over 15 staining cycles with a 36-marker antibody panel at 0.3775 um/px. "
    "Cells were segmented in QuPath and the per-cell mean intensity of all 58 imaged channels "
    "is carried as the protein readout, rounded to integers. Cell types are the authors' "
    "published Leiden labels. Three channels of the 60-plane stack - CD45, PanCK, DAPI - are "
    "rendered as the section composite the crop pointers box into. Published by Monkman et al. "
    "2024, J Transl Med, from a cohort that went on to receive PD-1 axis immunotherapy; the "
    "deposit carries no patient identifiers or clinical variables."
)


def lance_db(region: str) -> str:
    return os.path.join(PACKAGE_ROOT, region, "lance_db")


def apply(region: str, txn: CurationTransaction, allowed: set[str], *, dry_run: bool) -> None:
    """Apply one transaction, dropping ops a previous run already satisfied."""
    path = lance_db(region)
    existing = set(lancedb.connect(path).open_table(txn.table_name).to_arrow().column_names)
    kept = [op for op in txn.changes if not (isinstance(op, AddColumn) and op.column in existing)]
    kept = [op for op in kept if not (isinstance(op, RenameColumn) and op.column not in existing)]
    if not kept:
        print(f"  {region}/{txn.table_name}: already harmonized")
        return

    applicator = CurationApplicator(path, audit_db_path=default_audit_db_path(path))
    try:
        result = applicator.apply(
            CurationTransaction(table_name=txn.table_name, changes=kept),
            allowed_columns=allowed,
            dry_run=dry_run,
        )
        print(f"  {region}/{txn.table_name}: status={result.status} ({len(kept)} op(s))")
        if result.error:
            raise RuntimeError(f"{region}/{txn.table_name}: {result.error}")
    finally:
        applicator.close()


# ---------------------------------------------------------------------------
# ProteinSchema
# ---------------------------------------------------------------------------


def harmonize_proteins(region: str, targets: list[str], *, dry_run: bool) -> None:
    from polycomb import ReplaceValue

    # A resolution that lands on some rows only has to key on something the
    # column already holds, so each resolved column is seeded with the target
    # name and then replaced per target.
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
            resolved = PROTEIN_RESOLUTION.get(target, (None, None, None))[position]
            if resolved is None:
                reason = NO_ACCESSION_REASON.get(
                    target,
                    "a counterstain, blank, or unused channel rather than an antibody target"
                    if target not in PROTEIN_RESOLUTION
                    else "no single entry for this target",
                )
            else:
                reason = f"{target} resolves to {resolved}"
            ops.append(
                ReplaceValue(
                    column=column,
                    old_value=target,
                    new_value=resolved,
                    tool="resolve_proteins",
                    reason=reason,
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
                "and otherwise the channel's own name — which covers the isoform-specific CD45 "
                "antibodies, the carbohydrate and multi-chain epitopes, and the counterstain, "
                "blank and unused channels, none of which has an accession to key on"
            ),
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
            reason="the deposit publishes channel names only, with no clone or catalogue number",
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="human tissue; NCBITaxon canonical name",
        ),
    ]
    apply(
        region,
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
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# SpatialObs
# ---------------------------------------------------------------------------


def harmonize_obs(region: str, *, dry_run: bool) -> None:
    from polycomb import ReplaceValue

    ops: list = [
        AddColumn(
            column="spatial_unit",
            value="cell",
            tool="schema_align",
            reason="one row is one QuPath cell object",
        ),
        AddColumn(
            column="assay",
            value=ASSAY,
            tool="resolve_assays",
            reason="EFO canonical label for the platform",
        ),
        AddColumn(
            column="technology",
            value="codex",
            tool="schema_align",
            reason="SpatialTechnology enum member for this platform",
        ),
        AddColumn(
            column="organism",
            value=ORGANISM,
            tool="resolve_organisms",
            reason="NCBITaxon canonical name; human tissue",
        ),
        AddColumn(
            column="z_um",
            data_type="double",
            tool="schema_align",
            reason="CODEX images one focal plane per cycle; the cell table is 2-D",
        ),
        RenameColumn(
            column="um_per_px",
            new_name="pixel_size_um",
            tool="schema_align",
            reason="the 0.3775 um pixel edge is the micron/pixel conversion for the core image",
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
                "QuPath cell detection: nuclei are detected on the DAPI channel and the cell "
                "boundary is that nucleus expanded, which is why every row carries both a "
                "nuclear and a whole-cell area"
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
            reason="the deposit publishes no per-cell region annotation",
        ),
        AddColumn(
            column="disease_state",
            value="diseased",
            tool="schema_align",
            reason="every core is tumour tissue from an NSCLC resection",
        ),
        AddColumn(
            column="disease",
            value="non-small cell lung carcinoma",
            tool="resolve_diseases",
            reason="MONDO canonical label; denormalized from the section",
        ),
        AddColumn(
            column="cell_type",
            value_sql="cell_types",
            tool="schema_align",
            reason="seed with the published label so the per-label resolution can key on it",
        ),
    ]
    for label, (resolved, why) in CELL_TYPE_RESOLUTION.items():
        ops.append(
            ReplaceValue(
                column="cell_type",
                old_value=label,
                new_value=resolved,
                tool="resolve_cell_types",
                reason=(
                    f"{why}; CL canonical label" if resolved else f"{why}; the column stays null"
                ),
                confidence=1.0 if resolved else 0.0,
                source="reference_db" if resolved else "none",
                input_value=label,
            )
        )
    ops += [
        RenameColumn(
            column="cell_types",
            new_name="cell_type_original",
            tool="schema_align",
            reason=(
                "the paper's own cluster label, kept verbatim: the mapping onto CL collapses "
                "four tumour states onto one term and drops two non-types entirely"
            ),
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
            reason=(
                "the blank cycles are columns of the protein feature axis, flagged is_control "
                "there, rather than a per-cell count"
            ),
        ),
        AddColumn(
            column="unassigned_counts",
            data_type="double",
            tool="schema_align",
            reason="no transcripts, so nothing to leave unassigned",
        ),
        AddColumn(
            column="in_tissue",
            data_type="bool",
            tool="schema_align",
            reason="a segmented cell within a TMA core is in tissue by construction",
        ),
        AddColumn(
            column="passes_qc",
            value=True,
            tool="schema_align",
            reason=(
                "the package carries only the cells present in the authors' annotated object, "
                "i.e. the ones that survived their QC"
            ),
        ),
        RenameColumn(
            column="source_extras_json",
            new_name="additional_metadata",
            tool="schema_align",
            reason=(
                "preserve the source columns with no schema field: the nuclear and cell "
                "circularities, the Leiden cluster id and its coarse grouping, the TMA core "
                "position, and the source image file"
            ),
        ),
        AddColumn(
            column="section_uid_TissueSectionSchema_join",
            value_sql="section_id",
            tool="join_key",
            reason="the TMA core this cell was measured on",
        ),
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value_sql="panel_name",
            tool="join_key",
            reason="the antibody panel measured on this cell",
        ),
        # No donor_uid join column: the deposit has no patients to point at.
    ]
    apply(
        region,
        CurationTransaction(table_name="SpatialObs_protein_abundance", changes=ops),
        {
            "spatial_unit",
            "assay",
            "technology",
            "organism",
            "z_um",
            "pixel_size_um",
            "unit_size_um",
            "segmentation_method",
            "tissue",
            "anatomical_region",
            "disease_state",
            "disease",
            "cell_type",
            "cell_type_original",
            "n_counts",
            "n_genes",
            "negative_control_counts",
            "unassigned_counts",
            "in_tissue",
            "passes_qc",
            "additional_metadata",
            "section_uid_TissueSectionSchema_join",
            "panel_uid_PanelSchema_join",
        },
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# SpatialDatasetSchema
# ---------------------------------------------------------------------------


def harmonize_dataset(region: str, core: str, *, dry_run: bool) -> None:
    ops: list = [
        AddColumn(
            column="study_name",
            value=(
                "Spatial Immune Associations of Immunotherapy Response in Non-Small Cell Lung "
                "Cancer by Multiplexed Tissue Imaging"
            ),
            tool="schema_align",
            reason="the Zenodo record these 36 cores belong to",
        ),
        AddColumn(
            column="sample_name",
            value=f"{region} (TMA core {core})",
            tool="schema_align",
            reason="the acquisition region and the TMA position the paper names it by",
        ),
        AddColumn(
            column="source_dataset_id",
            value="monkman2024spatial",
            tool="schema_align",
            reason="the corpus-side dataset id in data/datasets.csv",
        ),
        AddColumn(
            column="folder_name",
            value=region,
            tool="schema_align",
            reason="the per-region folder in this package",
        ),
        AddColumn(
            column="accession_database",
            value="Zenodo",
            tool="schema_align",
            reason="the deposit's archive",
        ),
        AddColumn(
            column="accession_id",
            value=DOI,
            tool="schema_align",
            reason="the record DOI",
        ),
        AddColumn(
            column="data_access_link",
            value=RECORD_URL,
            tool="schema_align",
            reason="the Zenodo landing page",
        ),
        AddColumn(
            column="download_url",
            value=f"{RECORD_URL}/files/s293_c001_v001_r001_{region}.ome.tiff",
            tool="schema_align",
            reason=(
                "this region's image; the cell table and the annotated object are single "
                "files covering every region and are named in the record metadata"
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
            reason="sample-prep and protocol summary from the deposit and the paper",
        ),
        AddColumn(
            column="panel_uid_PanelSchema_join",
            value=PANEL_NAME,
            tool="join_key",
            reason="every feature space of this dataset was measured with the one panel",
        ),
    ]
    apply(
        region,
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
    parser.add_argument("--regions", nargs="*")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(os.path.join(STAGING_ROOT, "region_geometry.json")) as handle:
        core_of = {entry["region"]: entry["tma_core"] for entry in json.load(handle)}
    regions = args.regions or sorted(core_of)

    # The panel is identical in every region, so the feature axis is read once.
    db = lancedb.connect(lance_db(regions[0]))
    targets = db.open_table("ProteinSchema").to_arrow().column("target_name").to_pylist()

    for region in regions:
        print(region)
        harmonize_proteins(region, targets, dry_run=args.dry_run)
        harmonize_obs(region, dry_run=args.dry_run)
        harmonize_dataset(region, core_of[region], dry_run=args.dry_run)


if __name__ == "__main__":
    main()
