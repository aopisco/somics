"""
Spatial transcriptomics atlas schema.

One obs row is a single measured spatial unit — a segmented cell or nucleus
(Xenium, MERFISH, CosMx, CARTANA, STARmap, seqFISH), a capture spot
(Visium), a square bin (Visium HD), or a bead (Slide-seqV2) — carrying its
position in the tissue and, optionally, crops of the images taken of that
tissue. Every measurement modality is an optional pointer, so a row may have
expression only, expression plus imagery, or imagery only.

Physical position lives on the obs table itself (x_um/y_um/z_um), since a
coordinate is a property of the measured unit rather than a feature space.
Imagery is stored once per section as a `discrete_image` and addressed by
per-row DiscreteSpatialPointer boxes, so overlapping crops share pixels.
"""

from datetime import datetime
from enum import StrEnum
from typing import Self

from lancedb.pydantic import LanceModel
from pydantic import model_validator

from homeobox.pointer_types import DenseZarrPointer, DiscreteSpatialPointer, SparseZarrPointer
from homeobox.schema import CrossReferenceField, DatasetSchema, FeatureBaseSchema, HoxBaseSchema, OntologyAlignedField, PointerField, RegistryBaseSchema, RegistryKeyField, StableUIDField, SummaryField, _iter_pointer_annotations, combine_markers


class SpatialUnit(StrEnum):
    """The physical entity a single obs row measures."""

    CELL = 'cell'
    NUCLEUS = 'nucleus'
    SPOT = 'spot'
    BIN = 'bin'
    BEAD = 'bead'
    TRANSCRIPT_NEIGHBORHOOD = 'transcript_neighborhood'
    OTHER = 'other'


class SpatialTechnology(StrEnum):
    """
    The spatial platform, as a controlled name.

    Kept alongside the EFO-aligned `assay` column because EFO does not
    separate every platform in this corpus — Visium HD shares EFO:0010961
    with Visium, and CARTANA has no EFO term at all.
    """

    VISIUM = 'visium'
    VISIUM_HD = 'visium_hd'
    XENIUM = 'xenium'
    MERFISH = 'merfish'
    COSMX = 'cosmx'
    CARTANA = 'cartana'
    SLIDESEQ_V2 = 'slideseqv2'
    STARMAP = 'starmap'
    STARMAP_PLUS = 'starmap_plus'
    SEQFISH = 'seqfish'
    IMC = 'imc'
    CODEX = 'codex'
    OTHER = 'other'


class DiseaseState(StrEnum):
    """
    Coarse health status, kept separate from the MONDO `disease` term so that
    "healthy" is distinguishable from "not reported" (both leave `disease`
    null). Corresponds to PATO:0000461 vs an unknown/absent annotation in the
    source corpus.
    """

    HEALTHY = 'healthy'
    DISEASED = 'diseased'
    UNKNOWN = 'unknown'


class Sex(StrEnum):
    """Donor sex. PATO is not a registered ontology, so this is an enum."""

    FEMALE = 'female'
    MALE = 'male'
    MIXED = 'mixed'
    UNKNOWN = 'unknown'


class AgeUnit(StrEnum):
    """Unit for DonorSchema.age_value."""

    DAY = 'day'
    WEEK = 'week'
    MONTH = 'month'
    YEAR = 'year'
    CARNEGIE_STAGE = 'carnegie'


class LifeStage(StrEnum):
    """Coarse life stage, for grouping without an ontology lookup."""

    EMBRYONIC = 'embryonic'
    FETAL = 'fetal'
    NURSING = 'nursing'
    JUVENILE = 'juvenile'
    YOUNG_ADULT = 'young_adult'
    MIDDLE_AGED = 'middle_aged'
    LATE_ADULT = 'late_adult'
    UNKNOWN = 'unknown'


class Preservation(StrEnum):
    """How the tissue section was preserved before profiling."""

    FFPE = 'ffpe'
    FRESH_FROZEN = 'fresh_frozen'
    FIXED_FROZEN = 'fixed_frozen'
    FRESH = 'fresh'
    OTHER = 'other'
    UNKNOWN = 'unknown'


class SegmentationMethod(StrEnum):
    """
    How the boundary of an obs row was defined. GRID for assays whose units
    are defined by the capture grid (Visium spots, Visium HD bins, Slide-seq
    beads) rather than by segmentation; null only when the method is
    unreported, so "laid out by the grid" stays distinguishable from
    "not annotated".
    """

    NUCLEUS_EXPANSION = 'nucleus_expansion'
    CELL_BOUNDARY_STAIN = 'cell_boundary_stain'
    WATERSHED = 'watershed'
    CELLPOSE = 'cellpose'
    BAYSOR = 'baysor'
    PROSEG = 'proseg'
    MANUAL = 'manual'
    GRID = 'grid'
    OTHER = 'other'


class ImageModality(StrEnum):
    """What a section image depicts."""

    HE = 'he'
    MORPHOLOGY = 'morphology'
    IMMUNOFLUORESCENCE = 'immunofluorescence'
    DAPI = 'dapi'
    OTHER = 'other'


class FeatureType(StrEnum):
    """
    The level of resolution a measured feature represents. Targeted panels
    report control probes and blank codewords in the same var axis as real
    genes, so they get their own members rather than being dropped.
    """

    GENE = 'gene'
    TRANSCRIPT = 'transcript'
    PROBE = 'probe'
    NEGATIVE_CONTROL_PROBE = 'negative_control_probe'
    NEGATIVE_CONTROL_CODEWORD = 'negative_control_codeword'
    BLANK_CODEWORD = 'blank_codeword'
    GENOMIC_CONTROL = 'genomic_control'
    ANTISENSE_CONTROL = 'antisense_control'
    DEPRECATED_CODEWORD = 'deprecated_codeword'
    OTHER = 'other'


class PublicationSchema(RegistryBaseSchema):
    """
    A paper or data release a dataset came from. Roughly half the corpus is
    vendor showcase data with no publication, so datasets may reference none.
    """
    # PubMed ID; the canonical identity when the source is a paper.
    pmid: int | None = combine_markers(StableUIDField.declare(), CrossReferenceField.declare(database_name='PubMed'), default=None)
    doi: str | None = CrossReferenceField.declare(database_name='DOI', default=None)
    title: str | None = None
    journal: str | None = None
    publication_date: datetime | None = None


class DonorSchema(RegistryBaseSchema):
    """
    The individual a section came from.

    The source corpus carries donor attributes but no donor identifiers, so
    `donor_id` is often null and such rows will not dedupe across datasets —
    deliberately, since two unlabelled donors of the same age and sex are not
    evidence of the same person.
    """
    # Source-provided donor identifier, namespaced by study where the source's own IDs are only locally unique.
    donor_id: str | None = StableUIDField.declare(default=None)
    organism: str = OntologyAlignedField.declare(ontology_name='NCBITaxon', default=...)
    sex: Sex = 'unknown'
    # Age in `age_unit` at the time of sampling.
    age_value: float | None = None
    age_unit: AgeUnit | None = None
    # Coarse life stage, for grouping without an ontology lookup.
    life_stage: LifeStage = 'unknown'
    # Developmental stage for human donors. Human and mouse stages are separate columns because HsapDv and MmusDv are separate ontologies, and a single column would resolve against the wrong one for half the corpus. Exactly one of the two is populated per donor.
    human_development_stage: str | None = OntologyAlignedField.declare(ontology_name='HsapDv', default=None)
    # Developmental stage for mouse donors.
    mouse_development_stage: str | None = OntologyAlignedField.declare(ontology_name='MmusDv', default=None)
    # Self-reported ethnicity; human donors only, usually unreported.
    ethnicity: str | None = OntologyAlignedField.declare(ontology_name='HANCESTRO', default=None)
    # The donor's diagnosis, which is not the same as the disease state of any one section — a cancer donor also yields normal-adjacent tissue.
    clinical_diagnosis: str | None = OntologyAlignedField.declare(ontology_name='MONDO', default=None)
    # Free-text notes about the donor.
    description: str | None = None


class TissueSectionSchema(RegistryBaseSchema):
    """
    One physical slice of tissue placed on a slide.

    Sections are their own entity because several datasets can describe the
    same slice — an H&E scan, a Xenium run, and a post-Xenium Visium HD run —
    and because serial sections of one block need to be relatable.
    """
    # Stable identifier for the section, namespaced by study or slide so it is unique corpus-wide. Null for sections that cannot be identified across datasets.
    section_id: str | None = StableUIDField.declare(default=None)
    donor_uid: str | None = RegistryKeyField.declare(target_schema=DonorSchema, default=None)
    # The tissue block this section was cut from, if known.
    block_id: str | None = None
    # Ordinal position within the block, so serial sections can be ordered and registered to one another.
    section_index: int | None = None
    # Slide or capture-area identifier, e.g. a Visium slide serial.
    slide_id: str | None = None
    tissue: str | None = OntologyAlignedField.declare(ontology_name='UBERON', default=None)
    disease_state: DiseaseState = 'unknown'
    # Disease of this section specifically, which may differ from the donor's diagnosis for normal-adjacent or metastatic sections.
    disease: str | None = OntologyAlignedField.declare(ontology_name='MONDO', default=None)
    preservation: Preservation = 'unknown'
    section_thickness_um: float | None = None


class PanelSchema(RegistryBaseSchema):
    """
    A probe panel measured by a targeted assay.

    Panels are a first-class entity because the same panel recurs across
    datasets and because two datasets are only directly comparable in feature
    space when their panels match.
    """
    # Panel name including version where the vendor versions it, e.g. "Xenium Human Brain Panel v1", "CosMx Human Universal Cell 1000".
    panel_name: str | None = StableUIDField.declare(default=None)
    # "10x Genomics", "Vizgen", "NanoString", "CARTANA", or a lab name.
    vendor: str | None = None
    version: str | None = None
    # Platform the panel is designed for.
    technology: SpatialTechnology | None = None
    organism: str | None = OntologyAlignedField.declare(ontology_name='NCBITaxon', default=None)
    # Number of biological targets, excluding controls.
    n_targets: int | None = None
    # Whether a custom add-on was spiked into a base panel — common in this corpus, and it changes the feature axis relative to the base panel.
    has_custom_addon: bool = False
    description: str | None = None


class GenomicFeatureSchema(FeatureBaseSchema):
    """
    A single measurable feature in a dataset's var space.

    Spans whole-transcriptome capture assays and targeted panels, so the
    registry must hold both Ensembl-identified genes and panel probes and
    control codewords that map to no gene at all.
    """
    # The identity a feature dedupes on corpus-wide, composed as organism:feature_id — for example Homo sapiens:ENSG00000243485. The organism half is the NCBITaxon label the `organism` column holds, not its CURIE, so the two columns stay consistent. It is a separate column because the uid must be stable across datasets while feature_id alone is not unique: Ensembl IDs are species-specific, but the probe and blank-codeword names that stand in for them on targeted panels are not, so a codeword like BLANK_0001 would otherwise merge a human and a mouse panel into one feature. Composing the organism in keeps the two apart, and one column is what compute_stable_uids can key on.
    feature_key: str = StableUIDField.declare(default=...)
    # The feature identity as measured. The Ensembl gene ID where one exists; otherwise the panel's own probe or codeword name.
    feature_id: str
    # What this feature is — a gene, a probe, or a control codeword.
    feature_type: FeatureType
    # Canonical gene symbol this feature maps to, if any.
    gene_name: str | None = None
    ensembl_gene_id: str | None = CrossReferenceField.declare(database_name='ENSEMBL', default=None)
    # The organism this feature belongs to. Required, because panel probe names collide across species where Ensembl IDs would not.
    organism: str = OntologyAlignedField.declare(ontology_name='NCBITaxon', default=...)
    # Ensembl release used for the annotation, if known.
    ensembl_version: str | None = None
    # Whether the feature is a control rather than a biological target. Redundant with `feature_type` but cheap to filter on.
    is_control: bool = False


class ProteinSchema(FeatureBaseSchema):
    """
    A protein target in a multiplexed protein panel.

    A row is a target as an antibody measures it, not a protein entry as
    UniProt defines it: a phospho-specific antibody and one against the
    unmodified protein share an accession but are different measurements,
    and are therefore different features.
    """
    # The identity a protein target dedupes on corpus-wide, composed as organism:target_id — for example Homo sapiens:P01732 for CD8a. The organism half is the NCBITaxon label the `organism` column holds, not its CURIE, matching GenomicFeatureSchema.feature_key. The target_id half is the UniProt accession where one resolved, suffixed with +`modification` for modification-specific antibodies (Homo sapiens:P62753+phospho-S235/S236), and otherwise the published `target_name` — panels routinely carry targets that map to no single accession: DNA intercalators, multi-chain targets like collagen I, and antibodies the source names but does not identify. It is a separate column because `uniprot_id` is null for those targets and cannot be the uid, while a name alone would merge a human and a mouse panel's CD45 into one feature; composing the organism in keeps the two apart, and one column is what compute_stable_uids can key on.
    protein_key: str = StableUIDField.declare(default=...)
    # The target as the panel names it, e.g. "CD8a", "pS6", "DNA1". Required, because it is the fallback identity for targets that resolve to no accession, and because the panel's own label is what the source's channel names join against.
    target_name: str
    # UniProt accession, e.g. "P04637".
    uniprot_id: str | None = CrossReferenceField.declare(database_name='UniProt', default=None)
    # The post-translational state the antibody is specific to, e.g. "phospho-S235/S236", "cleaved". Null for antibodies against the unmodified protein, which is most of a panel.
    modification: str | None = None
    # Recommended protein name from UniProt.
    protein_name: str | None = None
    # Primary gene symbol encoding this protein.
    gene_name: str | None = None
    # Clone identifier of the detection antibody, where reported.
    antibody_clone: str | None = None
    organism: str | None = OntologyAlignedField.declare(ontology_name='NCBITaxon', default=None)
    # Whether this target is an isotype or normalization control.
    is_control: bool = False


class ImageFeatureSchema(FeatureBaseSchema):
    """One dimension of a per-row image feature vector."""
    # Name of the feature, e.g. "mean_intensity_DAPI", "uni_dim_0".
    feature_name: str = StableUIDField.declare(default=...)
    # What the feature measures and how it was computed.
    description: str | None = None
    # Model or pipeline that produced the feature, e.g. a tile-encoder name and version. Embeddings are only comparable within an extractor.
    extractor: str | None = None


class SectionImageSchema(LanceModel):
    """
    One image acquired of one tissue section — the thing that obs crop
    pointers box into.

    Separate from the dataset row because a section can have several images
    (an H&E scan and a multichannel morphology stack), and separate from the
    section because an image carries its own frame, scale, and channel names.
    """
    section_uid: str = RegistryKeyField.declare(target_schema=TissueSectionSchema, default=...)
    # The ingested dataset whose zarr group holds these pixels.
    dataset_uid: str | None
    # What the image depicts; selects which obs crop pointer boxes it.
    image_modality: ImageModality
    # Names of the trailing channel axis, in stored order — ["R","G","B"] for H&E, ["DAPI","boundary","interior_RNA"] for a Xenium morphology stack. Crop pointers box the leading spatial axes and take channels in full, so this is how a channel is identified after a read.
    channel_names: list[str] | None
    # Microns per pixel at full resolution.
    pixel_size_um: float | None
    height_px: int | None
    width_px: int | None
    # Number of focal planes for volumetric stacks; null for 2D.
    n_z_planes: int | None
    # Whether the image frame has been aligned to the expression coordinate frame. False means obs pixel coordinates and crop boxes are only valid in this image's own frame.
    is_registered_to_expression: bool = False
    # Where the image came from, e.g. an OME-TIFF path or URL.
    source_path: str | None
    description: str | None


class SpatialDatasetSchema(DatasetSchema):
    """
    One ingested dataset: the unit of provenance in the corpus, roughly a
    published sample or a vendor showcase release.
    """
    publication_uid: str | None = RegistryKeyField.declare(target_schema=PublicationSchema, default=None)
    # Free-text study the dataset belongs to, as named by the source.
    study_name: str | None = None
    # Sample label as published, e.g. "Xenium Human Brain 2".
    sample_name: str | None = None
    # The corpus-side dataset identifier, where one was assigned.
    source_dataset_id: str | None = None
    # Vendor or source folder name; often the most stable source key.
    folder_name: str | None = None
    # Database the data came from, e.g. "GEO", "Zenodo", "CELLxGENE".
    accession_database: str | None = None
    # Accession within that database, e.g. "GSE234713".
    accession_id: str | None = None
    # Landing page describing the dataset.
    data_access_link: str | None = None
    # Direct download URL for the raw files. Pipe-separated when a dataset ships as several files.
    download_url: str | None = None
    # Path to the raw files on local storage, when not fetched online.
    source_path: str | None = None
    panel_uid: str | None = RegistryKeyField.declare(target_schema=PanelSchema, default=None)
    # Sample-prep and protocol text from the source record.
    dataset_description: str | None = None
    n_rows: int = SummaryField.declare(target_schema='SpatialObs', target_field='uid', op='count', default=0)
    n_sections: int = SummaryField.declare(target_schema='SpatialObs', target_field='section_uid', op='nunique', default=0)
    organism: list[str] | None = SummaryField.declare(target_schema='SpatialObs', target_field='organism', op='unique', default=None)
    tissue: list[str] | None = SummaryField.declare(target_schema='SpatialObs', target_field='tissue', op='unique', default=None)
    disease: list[str] | None = SummaryField.declare(target_schema='SpatialObs', target_field='disease', op='unique', default=None)
    assay: list[str] | None = SummaryField.declare(target_schema='SpatialObs', target_field='assay', op='unique', default=None)


class SpatialObs(HoxBaseSchema):
    """
    A single measured spatial unit and its position in a tissue section.

    Rows from every platform in the corpus share this table; what differs
    between platforms is which columns and pointers are populated, not which
    table the row lives in. `spatial_unit` says what the row physically is,
    and `unit_size_um` gives its footprint where one is defined.
    """
    # The row identifier as published — a Visium barcode, a Xenium cell_id, a Slide-seq bead barcode. Kept so a row can be traced back to the original files and joined against source-side annotations.
    source_obs_id: str | None
    # The physical entity this row measures.
    spatial_unit: SpatialUnit
    # Assay as an EFO term, e.g. "EFO:0022615" for Xenium.
    assay: str = OntologyAlignedField.declare(ontology_name='EFO', default=...)
    # Platform name; disambiguates assays EFO conflates.
    technology: SpatialTechnology
    # NCBITaxon:9606 (human) or NCBITaxon:10090 (mouse) in this corpus.
    organism: str = OntologyAlignedField.declare(ontology_name='NCBITaxon', default=...)
    # Position along the section's first in-plane axis, in microns, in the canonical physical frame of the section. Centroid for segmented units, capture-centre for spots, bins, and beads.
    x_um: float
    # Position along the section's second in-plane axis, in microns.
    y_um: float
    # Depth in microns for volumetric assays (STARmap PLUS, 3D MERFISH). Null for the 2D assays that make up most of the corpus.
    z_um: float | None = None
    # Position in the pixel frame of the section image this row's crops index into. Stored alongside the micron frame because the two are related by a per-image scale factor and origin that is not always recoverable after ingestion.
    x_px: float | None = None
    # Position along the second image axis, in pixels.
    y_px: float | None = None
    # Microns per pixel of the image the crop pointers address, i.e. the conversion between the micron and pixel frames above.
    pixel_size_um: float | None = None
    # Footprint of the measured unit in microns: spot or bead diameter (55 for Visium, 10 for Slide-seqV2), bin edge length for Visium HD. Null for segmented units, whose extent is in `cell_area_um2`.
    unit_size_um: float | None = None
    # The physical section this row was measured on.
    section_uid: str | None = RegistryKeyField.declare(target_schema=TissueSectionSchema, default=None)
    # The donor this row came from. Denormalized from the section for query convenience; must agree with TissueSectionSchema.donor_uid.
    donor_uid: str | None = RegistryKeyField.declare(target_schema=DonorSchema, default=None)
    # The probe panel measured on this row. Null for whole-transcriptome capture assays that use no panel.
    panel_uid: str | None = RegistryKeyField.declare(target_schema=PanelSchema, default=None)
    # The tissue sampled, as UBERON. Denormalized from the section.
    tissue: str | None = OntologyAlignedField.declare(ontology_name='UBERON', default=...)
    # Sub-tissue region this row falls in — a cortical layer, a glomerulus, a crypt. Finer than `tissue`, and assigned per row rather than per section.
    anatomical_region: str | None = OntologyAlignedField.declare(ontology_name='UBERON', default=None)
    # Coarse health status; distinguishes healthy from unannotated.
    disease_state: DiseaseState = 'unknown'
    # The most specific disease term available for this row. MONDO encodes the hierarchy, so coarser groupings (e.g. cancer, MONDO:0004992) are recovered by ontology traversal rather than stored as extra columns. Null when healthy or unannotated — see `disease_state`.
    disease: str | None = OntologyAlignedField.declare(ontology_name='MONDO', default=None)
    # Harmonized cell type as a Cell Ontology term.
    cell_type: str | None = OntologyAlignedField.declare(ontology_name='CL', default=None)
    # The label as published, before harmonization. Preserved because the mapping to CL is lossy and sometimes contested.
    cell_type_original: str | None = None
    # Total transcripts or UMIs assigned to this row.
    n_counts: float | None = None
    # Number of distinct genes detected in this row.
    n_genes: int | None = None
    # Counts assigned to negative-control probes — the specificity metric for imaging-based assays. Null for capture-based assays.
    negative_control_counts: float | None = None
    # Counts on unassigned, blank, or deprecated codewords, where the platform reports them separately from negative controls.
    unassigned_counts: float | None = None
    # Area of the segmented cell boundary in square microns.
    cell_area_um2: float | None = None
    # Area of the segmented nucleus in square microns.
    nucleus_area_um2: float | None = None
    # How this row's boundary was defined. Capture-grid assays record `grid` rather than null, so an unsegmented unit is distinguishable from one whose method was never reported.
    segmentation_method: SegmentationMethod | None = None
    # Whether the unit overlaps tissue rather than empty slide — Visium's in_tissue flag and its equivalents.
    in_tissue: bool | None = None
    # The source's own filtering verdict, carried through rather than applied, so downstream users can choose their own threshold.
    passes_qc: bool | None = None
    # JSON dump for source columns that do not fit this schema.
    additional_metadata: str | None = None
    gene_expression: SparseZarrPointer | None = PointerField.declare(feature_space='gene_expression', feature_registry_schema=GenomicFeatureSchema, default=None)
    # Multiplexed protein readout, e.g. CosMx protein panels.
    protein_abundance: DenseZarrPointer | None = PointerField.declare(feature_space='protein_abundance', feature_registry_schema=ProteinSchema, default=None)
    # Per-row image embeddings or morphology statistics.
    image_features: DenseZarrPointer | None = PointerField.declare(feature_space='image_features', feature_registry_schema=ImageFeatureSchema, default=None)
    # Box into the section's H&E image. No feature registry: pixels are not features. Crops are boxes into one stored image rather than copied tiles, so neighbouring rows share overlapping pixels instead of duplicating them.
    he_crop: DiscreteSpatialPointer | None = PointerField.declare(feature_space='discrete_image', default=None)
    # Box into the section's morphology stack — DAPI, boundary, and interior-RNA channels on Xenium, MERFISH, and CosMx. Channels are trailing axes of the stored array and are named on the section image row, so this pointer boxes the spatial axes only.
    morphology_crop: DiscreteSpatialPointer | None = PointerField.declare(feature_space='discrete_image', default=None)

    has_gene_expression: bool = False
    has_protein_abundance: bool = False
    has_image_features: bool = False
    has_he_crop: bool = False
    has_morphology_crop: bool = False

    @classmethod
    def has_pointer_field_map(cls) -> dict[str, str]:
        return {f"has_{name}": name for name, _ in _iter_pointer_annotations(cls)}

    @model_validator(mode="after")
    def _generate_has_pointer_flags(self) -> Self:
        for flag, source in type(self).has_pointer_field_map().items():
            setattr(self, flag, getattr(self, source) is not None)
        return self


REGISTRY_SCHEMAS: dict[str, type[FeatureBaseSchema]] = {
    'gene_expression': GenomicFeatureSchema,
    'protein_abundance': ProteinSchema,
    'image_features': ImageFeatureSchema,
}
