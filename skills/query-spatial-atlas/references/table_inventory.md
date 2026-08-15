# Table inventory

Every LanceDB table in the atlas, what it holds, and how to join it. Authoritative column definitions live in `schema/spatial_transcriptomics_atlas_schema.yaml`; this file is the query-side map.

Confirm names with `atlas.db.list_tables()` on first contact — they follow polycomb's `ingest_collection` defaults (obs and dataset tables named after their schema classes, FK/other tables named after their classes verbatim, feature registries as `{snake_case(class)}_registry`), which an ingestion script can override.

| Table | Accessor | One row is |
|---|---|---|
| `SpatialObs` | `atlas.query()`, `atlas.obs_table` | one measured spatial unit |
| `SpatialDatasetSchema` | `atlas.list_datasets()` | one ingested dataset × feature space |
| `genomic_feature_schema_registry` | `atlas.feature_registry("gene_expression")` | one gene / probe / codeword |
| `protein_schema_registry` | `atlas.feature_registry("protein_abundance")` | one protein target |
| `image_feature_schema_registry` | `atlas.feature_registry("image_features")` | one image-feature dimension |
| `TissueSectionSchema` | `atlas.db.open_table(...)` | one physical section |
| `DonorSchema` | `atlas.db.open_table(...)` | one donor |
| `PanelSchema` | `atlas.db.open_table(...)` | one probe panel |
| `PublicationSchema` † | `atlas.db.open_table(...)` | one paper / data release |
| `SectionImageSchema` † | `atlas.db.open_table(...)` | one image of one section |
| `_feature_layouts`, `atlas_versions` | internal | — |

† **Absent as of snapshot v0.** A registry-key table is only created when rows are ingested for it; the v0 dataset is a vendor release with no publication, and no section-image rows were written. Guard before opening:

```python
if "SectionImageSchema" in atlas.db.list_tables().tables:
    ...
```

Every table has a `uid` primary key. FK columns are named `<target>_uid` and join to that table's `uid`.

## Join graph

```
SpatialObs.section_uid  -> TissueSectionSchema.uid
SpatialObs.donor_uid    -> DonorSchema.uid          (denormalized; agrees with section's donor)
SpatialObs.panel_uid    -> PanelSchema.uid
SpatialObs.dataset_uid  -> SpatialDatasetSchema.dataset_uid

TissueSectionSchema.donor_uid       -> DonorSchema.uid
SpatialDatasetSchema.publication_uid -> PublicationSchema.uid
SpatialDatasetSchema.panel_uid       -> PanelSchema.uid

SectionImageSchema.section_uid -> TissueSectionSchema.uid
SectionImageSchema.dataset_uid -> SpatialDatasetSchema.dataset_uid
```

`he_crop` / `morphology_crop` boxes on an obs row address the image of that row's `section_uid` whose `image_modality` matches (`he` for `he_crop`; `morphology` / `dapi` / `immunofluorescence` for `morphology_crop`).

FK tables are small — read them whole and join in polars:

```python
sections = atlas.db.open_table("TissueSectionSchema").search().to_polars()
obs.join(sections, left_on="section_uid", right_on="uid", how="left")
```

## `SpatialObs` columns by role

**Identity / provenance** — `uid` (atlas-assigned), `source_obs_id` (barcode / cell_id as published), `dataset_uid`.

**What and where** — `spatial_unit`, `assay` (EFO), `technology`, `organism` (NCBITaxon), `x_um`, `y_um`, `z_um`, `x_px`, `y_px`, `pixel_size_um`, `unit_size_um`.

- `x_um`/`y_um` are in the **section's own physical frame**. Not comparable across sections.
- `x_px`/`y_px` are in the frame of the image the crop pointers index; `pixel_size_um` converts.
- `unit_size_um` is the footprint where the grid defines one — 55 (Visium spot), 10 (Slide-seqV2 bead), bin edge (Visium HD). Undefined for segmented units, whose extent is `cell_area_um2`.
- **Undefined floats arrive as NaN, not NULL.** `z_um` and `unit_size_um` are all-NaN in v0, so `IS NULL` matches nothing, `IS NOT NULL` matches everything, and `unit_size_um > 0` matches every row. Test these in polars (`df["z_um"].is_nan()`), never in a SQL predicate.

**Entity references** — `section_uid`, `donor_uid`, `panel_uid` (null for whole-transcriptome assays).

**Biological annotation** — `tissue` (UBERON), `anatomical_region` (UBERON, per row), `disease_state`, `disease` (MONDO), `cell_type` (CL), `cell_type_original` (as published).

The ontology names above say which ontology a value was resolved *against*. The stored value is the **human-readable label**, not the CURIE — `tissue = 'colon'`, `disease = 'colon adenocarcinoma'`, `organism = 'Homo sapiens'`, `assay = '10x Xenium'`. The same holds for `DonorSchema.human_development_stage` (`'adult stage'`) and `clinical_diagnosis`. These are plain string columns, so equality / `IN` / `LIKE` work uncast; discover exact spellings with `count(group_by=...)`.

**QC / segmentation** — `n_counts`, `n_genes`, `negative_control_counts`, `unassigned_counts`, `cell_area_um2`, `nucleus_area_um2`, `segmentation_method`, `in_tissue`, `passes_qc`, `additional_metadata` (JSON string).

`passes_qc` is the *source's* verdict, carried through rather than applied. Rows failing it are present.

**Pointers + presence flags**

| Pointer | Type | Feature space | Flag | v0 |
|---|---|---|---|---|
| `gene_expression` | `SparseZarrPointer` | `gene_expression` | `has_gene_expression` | 587,115 |
| `protein_abundance` | `DenseZarrPointer` | `protein_abundance` | `has_protein_abundance` | 0 |
| `image_features` | `DenseZarrPointer` | `image_features` | `has_image_features` | 0 |
| `he_crop` | `DiscreteSpatialPointer` | `discrete_image` | `has_he_crop` | 0 |
| `morphology_crop` | `DiscreteSpatialPointer` | `discrete_image` | `has_morphology_crop` | 587,115 |

Filter on the flags, never on the struct. Two pointers share `discrete_image`, so `.select_fields()` — not `.feature_spaces()` — picks between them.

## Enum values (as stored)

Enum columns — `spatial_unit`, `technology`, `disease_state`, `segmentation_method` — are Arrow **dictionary-encoded**. A bare string literal raises rather than matching:

```python
.where("technology = 'xenium'")               # RuntimeError: could not convert to
                                              #   literal of type 'Dictionary(Int32, Utf8)'
.where("CAST(technology AS STRING) = 'xenium'")   # correct
```

`CAST(... AS STRING)`, `arrow_cast(col, 'Utf8')`, and bare `LIKE` all work; `CAST(... AS VARCHAR)` is unsupported. `IS NULL` / `IS NOT NULL` work uncast. They come back as polars `Categorical` from `.to_polars()`, so cast there too: `pl.col("technology").cast(pl.Utf8) == "xenium"`.

The values themselves are lowercase strings, exactly as they appear in the cast predicate.

- `SpatialUnit`: `cell`, `nucleus`, `spot`, `bin`, `bead`, `transcript_neighborhood`, `other`
- `SpatialTechnology`: `visium`, `visium_hd`, `xenium`, `merfish`, `cosmx`, `cartana`, `slideseqv2`, `starmap`, `starmap_plus`, `seqfish`, `other`
- `DiseaseState`: `healthy`, `diseased`, `unknown`
- `Sex`: `female`, `male`, `mixed`, `unknown`
- `AgeUnit`: `day`, `week`, `month`, `year`, `carnegie`
- `LifeStage`: `embryonic`, `fetal`, `nursing`, `juvenile`, `young_adult`, `middle_aged`, `late_adult`, `unknown`
- `Preservation`: `ffpe`, `fresh_frozen`, `fixed_frozen`, `fresh`, `other`, `unknown`
- `SegmentationMethod`: `nucleus_expansion`, `cell_boundary_stain`, `watershed`, `cellpose`, `baysor`, `proseg`, `manual`, `grid`, `other`
- `ImageModality`: `he`, `morphology`, `immunofluorescence`, `dapi`, `other`
- `FeatureType`: `gene`, `transcript`, `probe`, `negative_control_probe`, `negative_control_codeword`, `blank_codeword`, `genomic_control`, `antisense_control`, `deprecated_codeword`, `other`

`technology` exists alongside the EFO `assay` because EFO conflates platforms — Visium and Visium HD share `EFO:0010961`, and CARTANA has no term. **Filter on `technology` when you mean a platform.**

## Feature registries

`atlas.feature_registry(space)` returns the whole registry as a polars DataFrame. All registries carry `uid` (join key for `.features()` and for `adata.var`) and `global_index`.

**`gene_expression`** — `feature_id` (Ensembl ID where one exists, else the panel's probe/codeword name), `feature_type`, `gene_name`, `ensembl_gene_id`, `organism`, `ensembl_version`, `is_control`.

Spans whole-transcriptome and targeted assays, so a large fraction of rows are controls with no gene. In v0: **541 features — 425 genes and 116 controls** (55 `blank_codeword`, 41 `negative_control_codeword`, 20 `negative_control_probe`), all `Homo sapiens`. Controls have `gene_name = None` and `feature_id` like `BLANK_0128`. Filter `is_control` and match `organism` — probe names collide across species where Ensembl IDs would not.

`organism` here is a label (`'Homo sapiens'`), matching the obs convention.

**`protein_abundance`** — `uniprot_id`, `protein_name`, `gene_name`, `antibody_clone`, `organism`, `is_control`. Empty in v0.

**`image_features`** — `feature_name`, `description`, `extractor`. Empty in v0. Embeddings are only comparable **within one `extractor`**; check it before combining datasets.

```python
reg = atlas.feature_registry("image_features")
reg["extractor"].value_counts()
```

## `SpatialDatasetSchema`

Provenance: `publication_uid`, `study_name`, `sample_name`, `source_dataset_id`, `folder_name`, `accession_database`, `accession_id`, `data_access_link`, `download_url`, `source_path`, `panel_uid`, `dataset_description`.

Summaries computed at ingestion: `n_rows`, `n_sections`, and list-valued `organism`, `tissue`, `disease`, `assay`. Use these to pick datasets without scanning obs:

```python
ds = atlas.list_datasets()
colon = ds.filter(pl.col("tissue").list.contains("colon"))
```

Also carries the homeobox-managed `dataset_uid`, `zarr_group`, `feature_space`, `layout_uid` — one row per dataset × feature space, so a multimodal dataset appears more than once.

## `SectionImageSchema`

**Not created as of v0** — see the † note above. When it lands it carries:

`section_uid`, `dataset_uid`, `image_modality`, `channel_names` (list, in stored order), `pixel_size_um`, `height_px`, `width_px`, `n_z_planes`, `is_registered_to_expression`, `source_path`, `description`.

Until then, `pixel_size_um` on obs (0.2125 µm/px for the v0 Xenium section) is the only scale information the atlas exposes, and crop channel identity has to come from the source dataset.

`is_registered_to_expression = false` means obs pixel coordinates and crop boxes are valid only in that image's own frame — do not overlay expression on it without registering first.
