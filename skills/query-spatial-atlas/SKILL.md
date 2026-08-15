---
name: query-spatial-atlas
description: Query, load, and visualize the homeobox spatial transcriptomics atlas defined by schema/spatial_transcriptomics_atlas_schema.yaml. Use when asked to explore the spatial atlas — filtering spatial units by tissue/technology/disease/cell type, pulling gene expression or protein into AnnData, reading H&E or morphology image crops, plotting sections in physical coordinates, or building neighbourhood/QC analyses over it.
---

# Querying the spatial transcriptomics atlas

The atlas is a homeobox `RaggedAtlas`: obs metadata in LanceDB, arrays (expression, protein, image features, section images) in Zarr, joined at query time by a lazy fluent query builder. This skill covers what is specific to **this** atlas — its tables, its coordinate frames, its two image-crop columns, and the panel-raggedness that makes union joins dangerous here.

Read `schema/spatial_transcriptomics_atlas_schema.yaml` for the authoritative column list and enum values. Read the homeobox docs (`docs/querying.md`, `docs/atlas.md`) for query-builder semantics that are not atlas-specific.

**Reference files** (read on demand, not up front):

- `references/table_inventory.md` — every LanceDB table, its columns, join keys, and enum values.
- `references/images_and_crops.md` — how `he_crop` / `morphology_crop` address pixels, channel axis order, `SectionImageSchema`.
- `references/plotting_recipes.md` — section maps, expression overlays, crop montages, neighbourhood enrichment.
- `examples/explore_spatial_atlas.py` — a runnable jupytext walkthrough of the whole surface.

## The one-sentence model

One obs row = one measured spatial unit (a segmented cell/nucleus, a Visium spot, a Visium HD bin, a Slide-seq bead) carrying its **physical position in its own section's frame** plus optional pointers to expression, protein, image features, and image crops. Rows from every platform share one table; what differs is which pointers are populated.

## 1. Open the atlas

Queries only run against a checked-out snapshot. A writable atlas raises `RuntimeError`.

```python
import homeobox as hox
import numpy as np
import polars as pl
import matplotlib.pyplot as plt

ATLAS_PATH = "/home/ubuntu/polycomb_atlases/somics_spatial_atlas"  # or s3://... with store_kwargs=

hox.RaggedAtlas.list_versions(ATLAS_PATH)  # one row per snapshot
atlas = hox.RaggedAtlas.checkout_latest(ATLAS_PATH)
print(atlas)
```

`checkout(ATLAS_PATH, version)` pins an older snapshot. Neither call needs the schema classes — obs schemas and pointer fields are read from the snapshot record.

## 2. Orient before querying

```python
atlas.db.list_tables()  # every table in the atlas
atlas.list_datasets()  # one row per ingested dataset (provenance + summaries)
atlas.query().count(group_by="technology")
atlas.query().count(group_by=["tissue", "organism"])
```

`list_datasets()` is the cheapest way to see what exists: it carries `sample_name`, `accession_id`, `panel_uid`, and the `SummaryField` aggregates (`n_rows`, `n_sections`, `organism`, `tissue`, `disease`, `assay`) without touching obs.

`count(group_by=...)` scans the grouping column — fine for `technology`, slow for high-cardinality columns like `section_uid`.

## 3. Filter rows

`.where()` takes any LanceDB SQL predicate against obs columns.

```python
q = atlas.query().where(
    "CAST(technology AS STRING) = 'xenium' AND tissue = 'colon' AND n_counts > 50"
)
```

Four things trip people up here, and the first two produce *errors or silence*, not wrong numbers.

**Enum columns are Arrow dictionary-encoded — plain equality raises.** `technology`, `spatial_unit`, `disease_state`, and `segmentation_method` are stored as `Dictionary(Int32, Utf8)`, and LanceDB will not coerce a string literal against them:

```python
.where("technology = 'xenium'")
# RuntimeError: lance error: Invalid user input: Error resolving filter expression
#   ... Received literal Utf8("xenium") and could not convert to literal of
#   type 'Dictionary(Int32, Utf8)'
```

Wrap the column in a cast — this is the form to use everywhere:

```python
.where("CAST(technology AS STRING) = 'xenium'")
.where("CAST(spatial_unit AS STRING) IN ('cell', 'nucleus')")
.where("CAST(disease_state AS STRING) = 'diseased'")
```

`CAST(... AS VARCHAR)` is *not* supported (`Unsupported data type: Varchar`); `arrow_cast(technology, 'Utf8')` and bare `LIKE` also work. `IS NULL` / `IS NOT NULL` work uncast. Enum values themselves are the lowercase strings from the YAML (`visium_hd`, `nucleus_expansion`, ...).

**Ontology-aligned columns hold human-readable labels, not CURIEs.** `tissue`, `disease`, `cell_type`, `anatomical_region`, `assay`, `organism` — and `DonorSchema.human_development_stage` / `clinical_diagnosis` — are resolved to labels at harmonization time:

```python
.where("tissue = 'colon'")                     # 587,115
.where("tissue = 'UBERON:0001155'")            # 0
.where("organism = 'Homo sapiens'")            # 587,115
.where("assay = '10x Xenium'")                 # 587,115  (EFO label, not EFO:0022615)
```

These are ordinary string columns, so equality, `IN`, and `LIKE` all work uncast. Discover the exact spellings with `count(group_by=...)` before writing a predicate — a label typo returns zero rows silently. See §4 for hierarchy.

**Presence flags are the cheap modality filter.** Every pointer has a boolean companion, so you never need a struct-null test:

```python
.where("has_gene_expression = true")
.where("has_he_crop = true AND has_gene_expression = true")
```

**Nullable numerics arrive as NaN, not NULL.** Columns the source didn't populate (`z_um` and `unit_size_um` for a segmented-cell assay) are filled with float NaN, so `IS NULL` finds nothing, `IS NOT NULL` matches everything, and comparisons like `unit_size_um > 0` match every row rather than none. Do not probe these in SQL — pull the column and check in polars/numpy:

```python
df = atlas.query().select(["z_um"]).limit(1000).to_polars()
df["z_um"].is_nan().all()  # True for a 2D assay
```

String columns *are* properly null (`cell_type IS NULL` works).

Other useful predicates: `in_tissue = true` (drops off-tissue Visium spots), `n_counts > 50`, `cell_type IS NOT NULL`, `donor_uid IN (...)`.

### Sampling honestly

`.limit(n)` returns the *first* n matching rows in storage order — which in practice means one dataset, often one section. For anything meant to represent the corpus, use `.balanced_limit(n, column)`:

```python
atlas.query().balanced_limit(20_000, "section_uid").to_polars()  # spread across sections
atlas.query().balanced_limit(20_000, "tissue").to_polars()
```

It is slow when the column has many groups; filter first.

**`balanced_limit` does not work on enum columns.** It builds a `col = 'value'` predicate per group internally, so it hits the same dictionary-literal error as §3 — `balanced_limit(n, "technology")` raises, and so do `spatial_unit`, `disease_state`, `segmentation_method`. Plain string columns (`section_uid`, `dataset_uid`, `tissue`, `disease`) are fine. To balance across an enum, do it by hand:

```python
def balanced_by_enum(atlas, column, per_group, select, where=None):
    groups = atlas.query().count(group_by=column)[column].drop_nulls().to_list()
    frames = []
    for g in groups:
        pred = f"CAST({column} AS STRING) = '{g}'"
        if where:
            pred = f"({where}) AND {pred}"
        frames.append(atlas.query().where(pred).select(select).limit(per_group).to_polars())
    return pl.concat(frames, how="vertical_relaxed")
```

### Joining donor / section / panel metadata

The obs table stores `section_uid`, `donor_uid`, `panel_uid` as foreign keys and denormalizes only `tissue` and `donor`-independent fields. Everything else needs a join. There is no join helper for FK tables — pull the small table and join in polars:

```python
obs = (
    atlas.query()
    .where("technology = 'xenium'")
    .select(["uid", "section_uid", "donor_uid", "x_um", "y_um", "cell_type"])
    .to_polars()
)

sections = atlas.db.open_table("TissueSectionSchema").search().to_polars()
donors = atlas.db.open_table("DonorSchema").search().to_polars()

obs = obs.join(
    sections.select(["uid", "section_id", "block_id", "preservation", "section_thickness_um"]),
    left_on="section_uid",
    right_on="uid",
    how="left",
).join(
    donors.select(["uid", "sex", "age_value", "age_unit", "life_stage"]),
    left_on="donor_uid",
    right_on="uid",
    how="left",
)
```

Table names and their columns are in `references/table_inventory.md`.

## 4. Ontology-aware filtering

The schema deliberately stores only the **most specific** term and expects hierarchy to come from ontology traversal — "all cancer sections" is not a column. Since the stored values are labels, traverse in CURIE space and match on `.label`:

```python
from polycomb.ols import get_ols_descendants, search_ols

search_ols("colorectal adenocarcinoma", ontology="MONDO", rows=5)  # find the CURIE

# Every value actually present, so the descendant set can be intersected down
present = set(atlas.query().count(group_by="disease")["disease"].drop_nulls().to_list())

labels = {"cancer"} | {t.label for t in get_ols_descendants("MONDO:0004992")}
matched = sorted(present & labels)
in_clause = ", ".join(f"'{lab}'" for lab in matched)
cancer = atlas.query().where(f"disease IN ({in_clause})").count()
```

Intersecting against `present` first is not an optimisation — descendant sets run to thousands of terms and label spellings vary, so it is the only way to know what actually matched. The same pattern works for `tissue` (UBERON), `cell_type` (CL), and `anatomical_region`.

Matching on labels is lossier than matching on CURIEs — two ontologies can share a label, and a label can drift between releases. If hierarchical queries become common, the fix is to carry the CURIE alongside the label at harmonization time rather than to work around it here.

`disease IS NULL` is ambiguous on its own: it means healthy *or* unannotated. Pair it with `disease_state` (`'healthy'` vs `'unknown'`).

## 5. Load expression

```python
adata = (
    atlas.query()
    .where("section_uid = 'abc123' AND has_gene_expression = true")
    .feature_spaces("gene_expression")
    .to_anndata()
)
```

`to_anndata()` requires exactly one AnnData-capable pointer field to be active. This schema has three (`gene_expression`, `protein_abundance`, `image_features`), so **always name one** with `.feature_spaces(...)` or `.select_fields(...)`; otherwise it raises. Use `.to_mudata()` for one AnnData per modality, `.to_multimodal()` when crops are in the mix (§6).

Rows whose pointer is null are dropped automatically, and the limit is applied after that filter — so `.limit(5000)` yields 5000 rows *with* data.

### Panel raggedness — the decision that matters most here

This corpus mixes whole-transcriptome capture (Visium, Visium HD, Slide-seq) with targeted panels of a few hundred probes (Xenium, MERFISH, CosMx, CARTANA). `feature_join` decides what happens when they meet:

- `"union"` (default) — every feature from every contributing dataset; rows whose panel lacks a feature get **zero, not NaN**. Mixing one Visium and one Xenium dataset gives a ~20k-column matrix in which the Xenium rows are ~98% structural zeros. Those zeros are indistinguishable from measured zeros downstream.
- `"intersection"` — only features measured by every contributing dataset. Across mixed technologies this collapses to the smallest panel, or to nothing.

Practical rule: **restrict to one panel or one technology, or name the features explicitly.**

```python
# One panel — union is safe, all rows share a feature axis
adata = atlas.query().where("panel_uid = 'p123'").feature_spaces("gene_expression").to_anndata()

# Cross-technology comparison — pick the genes yourself
registry = atlas.feature_registry("gene_expression")
panel = registry.filter(
    pl.col("gene_name").is_in(["EPCAM", "MKI67", "PTPRC", "VIM"])
    & (~pl.col("is_control"))
    & (pl.col("organism") == "Homo sapiens")
)
# Check what actually matched — a targeted panel will not carry every marker
print(panel.select(["uid", "gene_name"]))
adata = (
    atlas.query()
    .where("has_gene_expression = true")
    .features(panel["uid"].to_list(), "gene_expression")
    .balanced_limit(20_000, "dataset_uid")
    .to_anndata()
)
```

`.features()` overrides `feature_join` and is the most targeted read available — where a CSC copy exists it reads only those features' byte ranges.

### Controls are in the var axis

Targeted panels report negative-control probes, blank codewords, and deprecated codewords as ordinary features. `FeatureType` distinguishes them and `is_control` is the cheap filter. **Filter controls out before normalising or clustering**, and use them for QC:

```python
qc = balanced_by_enum(  # defined in §3 — balanced_limit cannot take an enum column
    atlas,
    "technology",
    per_group=50_000,
    select=["technology", "n_counts", "negative_control_counts", "n_genes"],
    where="has_gene_expression = true",
).with_columns((pl.col("negative_control_counts") / pl.col("n_counts")).alias("neg_frac"))
```

`negative_control_counts` is null for capture-based assays — it is an imaging-assay specificity metric.

### var index and layers

`to_anndata()` sets `adata.var` from the feature registry, indexed by feature `uid`. For readable plots:

```python
adata.var["uid"] = adata.var.index
adata.var_names = adata.var["gene_name"].fillna(adata.var["uid"]).astype(str)
adata.var_names_make_unique()
```

`X` comes back as a `scipy.sparse.csr_matrix` of **uint32 raw counts**, and `adata.layers` is empty — as of v0 the only zarr layer written is `counts`, so `.layers("gene_expression", [...])` has nothing else to select. Normalise yourself; check the zarr group before assuming a `log_normalized` layer exists.

### Attach coordinates

Coordinates live on obs, so they survive into `adata.obs` automatically. Add the conventional key so scanpy/squidpy plotting works:

```python
adata.obsm["spatial"] = adata.obs[["x_um", "y_um"]].to_numpy()
```

### Streaming

```python
for batch in (
    atlas.query()
    .where("tissue = 'colon'")
    .feature_spaces("gene_expression")
    .to_batches(batch_size=4096)
):
    ...  # each batch is an AnnData
```

## 6. Load image crops

Two pointer columns — `he_crop` and `morphology_crop` — **share the feature space `discrete_image`**. So `.feature_spaces("discrete_image")` activates both; to read one, use `.select_fields()`:

```python
batch = (
    atlas.query()
    .where("has_morphology_crop = true")
    .select(["uid", "x_um", "y_um", "n_counts"])
    .select_fields("morphology_crop")
    .limit(24)
    .to_spatial_batch("morphology_crop")
)

crops = batch.layers["raw"]  # list of ndarrays, one per row
meta = batch.metadata  # polars DataFrame ALIGNED TO crops
```

Three hard rules:

1. **`to_spatial_batch` returns rows in zarr-group order, not query order.** Use `batch.metadata` for coordinates and labels. Never zip crops against a separate `.to_polars()` result. `metadata` also carries the raw pointer structs and internal `_zg` / `_min_corner` / `_max_corner` columns — select the ones you want rather than dumping it.
2. **Check `.ndim` before displaying.** In v0 the stored image is a single-channel uint16 mosaic, so crops are plain 2-D `(H, W)` — there is no channel axis to transpose. Multi-channel data will add one; `references/images_and_crops.md` has a shape-agnostic display helper.
3. **The batch is list-backed and crops may differ in shape.** They happen to be uniform 128×128 in v0, so `np.stack(batch.layers["raw"])` works — but that is a property of this ingest, not a guarantee.

Crops come back as **float32 regardless of stored dtype** (v0 stores uint16), with values in the raw intensity range — percentile-scale before `imshow` or they render black. Crops are boxes into one stored section image, not copied tiles, so neighbouring rows share pixels.

`SectionImageSchema` — which would carry `channel_names`, `pixel_size_um`, and `is_registered_to_expression` — **does not exist in the atlas yet**. Until it does, `pixel_size_um` on obs (0.2125 µm/px for the v0 Xenium section) is the only scale information available, and channel identity has to come from the source. Full mechanics: `references/images_and_crops.md`.

## 7. Visualize

Coordinates are in **each section's own frame**. Two sections' `x_um` ranges overlap and mean different things — always filter or facet by `section_uid` before plotting, and set an equal aspect ratio so the tissue is not sheared.

```python
df = (
    atlas.query()
    .where("section_uid = 'abc123'")
    .select(["x_um", "y_um", "cell_type", "n_counts"])
    .to_polars()
)

fig, ax = plt.subplots(figsize=(7, 7))
ax.scatter(df["x_um"], df["y_um"], s=2, c=df["n_counts"], cmap="viridis")
ax.set_aspect("equal")
ax.invert_yaxis()  # image convention: y grows downward
ax.set_xlabel("x (µm)")
ax.set_ylabel("y (µm)")
```

Recipes for cell-type maps, gene-expression overlays, multi-section facets, crop montages, and neighbourhood enrichment: `references/plotting_recipes.md`.

## 8. ML training

Standard homeobox pipeline; nothing atlas-specific:

```python
ds = (
    atlas.query()
    .where("has_gene_expression = true")
    .balanced_limit(100_000, "section_uid")  # string column — safe for balanced_limit
    .to_unimodal_dataset("gene_expression", mode="iterable", batch_size=256)
)
loader = hox.make_loader(ds)
```

Crops train the same way through `to_unimodal_dataset("he_crop")`, yielding `SpatialTileBatch`es. See homeobox `docs/dataloader.md`.

## Pitfalls

| Symptom | Cause |
|---|---|
| `RuntimeError: could not convert to literal of type 'Dictionary(Int32, Utf8)'` | Enum column; wrap in `CAST(col AS STRING)` |
| `Unsupported data type: Varchar` | `CAST(... AS VARCHAR)`; use `AS STRING` |
| `where("tissue = 'UBERON:0000955'")` returns nothing | Ontology columns hold **labels**, not CURIEs |
| `where("z_um IS NULL")` returns nothing | Unpopulated numerics are NaN, not NULL; check in polars |
| `balanced_limit(n, "technology")` raises | Same dictionary issue; balance enums by hand (§3) |
| `to_anndata()` raises about multiple fields | Three AnnData-capable pointers; name one with `.feature_spaces()` / `.select_fields()` |
| `.features()` returns fewer genes than asked | Targeted panel; check the registry match before querying |
| Enormous var axis, mostly zeros | Default `union` join across panels; restrict panel/technology or use `.features()` |
| Genes with no counts anywhere | Structural zeros from a union join, not biology |
| Weird "genes" clustering strongly | Control probes / blank codewords; filter `is_control` |
| Sections plot on top of each other | Per-section coordinate frames; facet by `section_uid` |
| Crops don't match their labels | `to_spatial_batch` reorders; use `batch.metadata` |
| `crop.transpose(1, 2, 0)` raises | Crops are 2-D in v0; check `.ndim` |
| Crop renders black | float32 in raw uint16 range; percentile-scale first |
| `.limit(1000)` returns one dataset | Storage order; use `.balanced_limit(n, col)` |
| `negative_control_counts` all null | Capture-based assay; the metric is imaging-only |
| `disease IS NULL` mixes healthy and unknown | Disambiguate with `disease_state` |

## Verified against snapshot v0

Checked on 2026-08-15 against `/home/ubuntu/polycomb_atlases/somics_spatial_atlas`, snapshot v0.

**What v0 contains.** One dataset — *Xenium Human Colon 1, Cancer, pre-designed + add-on panel* (`Xenium_V1_hColon_Cancer_Add_on_FFPE`) — 587,115 segmented cells, 1 section, 1 donor, FFPE colon adenocarcinoma. 541 registry features: 425 genes plus 116 controls (55 blank codewords, 41 negative-control codewords, 20 negative-control probes). Two zarr groups: `gene_expression` (CSR **and CSC**, layer `counts`, uint32) and `discrete_image` (one `raw` layer, a 34338 × 42905 uint16 mosaic).

**Populated:** `gene_expression` and `morphology_crop` on all rows. **Empty:** `protein_abundance`, `image_features`, `he_crop` — and their registries exist but hold zero rows. Obs columns `cell_type`, `cell_type_original`, `anatomical_region`, `in_tissue`, `passes_qc`, `additional_metadata` are entirely null; `z_um` and `unit_size_um` are all-NaN.

**Tables present:** `SpatialObs`, `SpatialDatasetSchema`, `DonorSchema`, `TissueSectionSchema`, `PanelSchema`, the three registries, `_feature_layouts`, `atlas_versions`. **`PublicationSchema` and `SectionImageSchema` are absent** — no rows were ingested for them, so the table was never created. Guard with `if "SectionImageSchema" in atlas.db.list_tables().tables` rather than assuming.

**Still unverified** (nothing in v0 exercises them):

1. **Channel axis order** for multi-channel imagery. v0's single-channel 2-D crops leave the TCZYX-vs-trailing question open, and the `he_crop` / `morphology_crop` doc comments in the schema YAML still claim trailing channels.
2. **Panel raggedness.** One panel, so union vs intersection is untested — the guidance in §5 is forward-looking.
3. **Cross-section behaviour.** One section, so per-section coordinate frames are unexercised.
4. **`balanced_limit` at scale** across many groups.
