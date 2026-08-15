# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Exploring the spatial transcriptomics atlas
#
# A walkthrough of the homeobox spatial atlas defined by
# `schema/spatial_transcriptomics_atlas_schema.yaml`.
#
# One obs row is a single **measured spatial unit** — a segmented cell or
# nucleus (Xenium, MERFISH, CosMx, CARTANA, STARmap, seqFISH), a capture spot
# (Visium), a square bin (Visium HD), or a bead (Slide-seqV2) — carrying its
# position in the tissue plus optional pointers to expression, protein, image
# features, and crops of the section imagery.
#
# **What we cover:**
#
# 1. Opening a snapshot
# 2. The table inventory — datasets, sections, donors, panels, registries
# 3. Metadata queries: dictionary-encoded enums, label-valued ontology columns
# 4. Loading expression, and the panel-raggedness trap
# 5. Image crops
# 6. Spatial visualization
# 7. Neighbourhood analysis
# 8. Streaming and ML dataloaders
#
# > Verified end-to-end against **snapshot v0**: one Xenium human colon cancer
# > dataset, 587,115 cells, one section, a 425-gene panel plus 116 controls.
# > Cells marked *(future)* exercise features no ingested dataset has yet;
# > they are written to be correct when the data arrives and are guarded so
# > the notebook still runs top-to-bottom today.

# %%
import gc
import re

import homeobox as hox
import matplotlib.pyplot as plt
import numpy as np
import polars as pl


def brief(exc: Exception) -> str:
    """Lance errors append Rust source locations; drop them for readability."""
    return re.sub(r",\s*/[^,]*\.rs:\d+:\d+", "", str(exc)).strip()


def release_memory(*names: str) -> None:
    for name in names:
        globals().pop(name, None)
    plt.close("all")
    gc.collect()


# %% [markdown]
# ---
# ## 1. Open the atlas
#
# homeobox uses a snapshot model: each snapshot pins a consistent
# point-in-time view across every LanceDB table and zarr group. Queries only
# run against a checked-out snapshot, so concurrent ingestion never affects
# reads.

# %%
ATLAS_PATH = "/home/ubuntu/polycomb_atlases/somics_spatial_atlas"

# For an atlas on object storage, pass credentials through:
#   STORE_KWARGS = {"config": {"endpoint": ..., "aws_access_key_id": ..., ...}}
#   hox.RaggedAtlas.checkout_latest(ATLAS_PATH, store_kwargs=STORE_KWARGS)

# %%
hox.RaggedAtlas.list_versions(ATLAS_PATH).select(["version", "total_rows", "created_at"])

# %%
atlas = hox.RaggedAtlas.checkout_latest(ATLAS_PATH)
TABLES = set(atlas.db.list_tables().tables)
print(sorted(TABLES))

# %% [markdown]
# Note what is *not* there. A registry-key table is only created once rows are
# ingested for it, so `PublicationSchema` (the v0 dataset is a vendor release
# with no paper) and `SectionImageSchema` are absent. Guard before opening any
# entity table rather than assuming it exists.

# %%
for t in ["PublicationSchema", "SectionImageSchema"]:
    print(f"{t:22s} {'present' if t in TABLES else 'ABSENT'}")

# %% [markdown]
# ---
# ## 2. The tables
#
# Obs metadata, dataset provenance, feature registries, and the entity tables
# all live in LanceDB. Arrays live in zarr and are joined in at query time.

# %%
datasets = atlas.list_datasets()
datasets.select(
    ["dataset_uid", "sample_name", "feature_space", "zarr_group", "n_rows", "n_sections"]
)

# %% [markdown]
# One row per dataset **× feature space** — so a dataset with expression and
# imagery appears twice, pointing at two different zarr groups.

# %%
datasets.select(
    ["folder_name", "accession_database", "tissue", "disease", "assay", "panel_uid"]
).head(1).to_dicts()

# %% [markdown]
# ### Entity tables
#
# Sections, donors, and panels are first-class entities rather than obs
# columns, because several datasets can describe the same physical slice — an
# H&E scan, a Xenium run, and a post-Xenium Visium HD run.

# %%
sections = atlas.db.open_table("TissueSectionSchema").search().to_polars()
sections.select(["uid", "section_id", "tissue", "disease", "disease_state", "preservation"])

# %%
donors = atlas.db.open_table("DonorSchema").search().to_polars()
donors.select(["uid", "donor_id", "organism", "sex", "life_stage", "human_development_stage"])

# %% [markdown]
# Panels are first-class because two datasets are only directly comparable in
# feature space when their panels match.

# %%
panels = atlas.db.open_table("PanelSchema").search().to_polars()
panels.select(["panel_name", "vendor", "technology", "n_targets", "has_custom_addon"])

# %% [markdown]
# ### Feature registries
#
# One per feature space, mapping feature uids to annotations. The genomic
# registry spans whole-transcriptome assays and targeted panels, so it holds
# Ensembl genes alongside probes and control codewords that map to no gene.

# %%
registry = atlas.feature_registry("gene_expression")
print(f"{registry.height:,} features")
registry.select(["uid", "feature_id", "feature_type", "gene_name", "is_control"]).head(5)

# %% [markdown]
# **Controls live in the same var axis as real genes** — 116 of the 541
# features here. Filter them out before normalising or clustering, and use
# them for QC.

# %%
registry["feature_type"].value_counts(sort=True)

# %%
for space in ["protein_abundance", "image_features"]:
    print(f"{space:20s} {atlas.feature_registry(space).height} features")

# %% [markdown]
# ---
# ## 3. Metadata queries
#
# `atlas.query()` returns a lazy builder; nothing executes until a terminal
# method (`.to_polars()`, `.to_anndata()`, ...).

# %%
print(f"{atlas.query().count():,} spatial units")
atlas.query().count(group_by="technology")

# %%
atlas.query().count(group_by=["spatial_unit", "segmentation_method"])

# %% [markdown]
# ### Gotcha 1: enum columns are dictionary-encoded
#
# `technology`, `spatial_unit`, `disease_state`, and `segmentation_method` are
# stored as `Dictionary(Int32, Utf8)`. A bare string literal does not match —
# it **raises**.

# %%
try:
    atlas.query().where("technology = 'xenium'").count()
except RuntimeError as e:
    print("RuntimeError:", brief(e))

# %% [markdown]
# Wrap the column in `CAST(... AS STRING)`. (`arrow_cast(col, 'Utf8')` and a
# bare `LIKE` also work; `CAST(... AS VARCHAR)` does not.)

# %%
print(atlas.query().where("CAST(technology AS STRING) = 'xenium'").count())
print(atlas.query().where("CAST(spatial_unit AS STRING) IN ('cell', 'nucleus')").count())

# %% [markdown]
# ### Gotcha 2: ontology columns hold labels, not CURIEs
#
# `tissue`, `disease`, `cell_type`, `anatomical_region`, `assay`, and
# `organism` are resolved to human-readable labels at harmonization time. A
# CURIE predicate returns zero rows *silently* — much worse than the error
# above.

# %%
for pred in [
    "tissue = 'colon'",
    "tissue = 'UBERON:0001155'",
    "organism = 'Homo sapiens'",
    "organism = 'NCBITaxon:9606'",
    "assay = '10x Xenium'",
    "assay = 'EFO:0022615'",
]:
    print(f"  {pred:36s} -> {atlas.query().where(pred).count():>8,}")

# %% [markdown]
# So discover the spellings before writing a predicate.

# %%
atlas.query().count(group_by="tissue")

# %% [markdown]
# ### Gotcha 3: unpopulated numerics are NaN, not NULL
#
# `z_um` (2D assay) and `unit_size_um` (segmented units) carry float NaN. SQL
# null tests are useless on them — and comparisons match everything.

# %%
for pred in ["z_um IS NULL", "z_um IS NOT NULL", "unit_size_um > 0"]:
    print(f"  {pred:24s} -> {atlas.query().where(pred).count():>8,}")

probe = atlas.query().select(["z_um", "unit_size_um"]).limit(1000).to_polars()
print("  z_um all NaN in polars:", bool(probe["z_um"].is_nan().all()))

# %% [markdown]
# String columns *are* properly null, so this works as expected:

# %%
print("cell_type IS NULL ->", f"{atlas.query().where('cell_type IS NULL').count():,}")

# %% [markdown]
# ### Presence flags
#
# Every pointer has a boolean companion, so modality filtering never needs a
# struct-null test. v0 has expression and morphology crops; protein, image
# features, and H&E are empty.

# %%
for flag in [
    "has_gene_expression",
    "has_protein_abundance",
    "has_image_features",
    "has_he_crop",
    "has_morphology_crop",
]:
    print(f"{flag:24s} {atlas.query().where(f'{flag} = true').count():>12,}")

# %% [markdown]
# ### Sampling honestly
#
# `.limit(n)` returns the *first* n matching rows in storage order — usually
# one dataset, often one section. For anything meant to represent the corpus,
# use `.balanced_limit(n, column)`.

# %%
balanced = atlas.query().balanced_limit(5_000, "section_uid").select(["section_uid"]).to_polars()
balanced["section_uid"].value_counts(sort=True)

# %% [markdown]
# ### Gotcha 4: `balanced_limit` cannot take an enum column
#
# It builds a `col = 'value'` predicate per group internally, so it hits the
# same dictionary-literal error. String columns (`section_uid`, `dataset_uid`,
# `tissue`, `disease`) are fine; the four enum columns are not.

# %%
try:
    atlas.query().balanced_limit(1_000, "technology").select(["technology"]).to_polars()
except RuntimeError as e:
    print("RuntimeError:", brief(e))


# %% [markdown]
# Balance across an enum by hand instead:


# %%
def balanced_by_enum(atlas, column, per_group, select, where=None):
    groups = atlas.query().count(group_by=column)[column].drop_nulls().to_list()
    frames = []
    for g in groups:
        pred = f"CAST({column} AS STRING) = '{g}'"
        if where:
            pred = f"({where}) AND {pred}"
        frames.append(atlas.query().where(pred).select(select).limit(per_group).to_polars())
    return pl.concat(frames, how="vertical_relaxed")


print(balanced_by_enum(atlas, "technology", 1_000, ["technology", "n_counts"]).height, "rows")

# %% [markdown]
# ### Joining donor and section metadata
#
# Obs carries foreign keys; the entity tables are small enough to pull whole
# and join in polars.

# %%
obs = (
    atlas.query()
    .select(["uid", "section_uid", "donor_uid", "x_um", "y_um", "n_counts"])
    .limit(5_000)
    .to_polars()
)

joined = obs.join(
    sections.select(["uid", "section_id", "preservation", "disease"]),
    left_on="section_uid",
    right_on="uid",
    how="left",
).join(
    donors.select(["uid", "sex", "life_stage", "human_development_stage"]),
    left_on="donor_uid",
    right_on="uid",
    how="left",
)
joined.select(["section_id", "preservation", "disease", "sex", "life_stage"]).head(3)

# %% [markdown]
# ### Ontology-aware filtering *(future)*
#
# The schema stores only the **most specific** term and expects hierarchy to
# come from ontology traversal — there is no "is cancer" column. Because the
# stored values are labels, traverse in CURIE space and match on `.label`,
# intersected against the labels actually present.

# %%
from polycomb.ols import get_ols_descendants  # noqa: E402

present = set(atlas.query().count(group_by="disease")["disease"].drop_nulls().to_list())
print("disease labels present:", sorted(present))

cancer_labels = {"cancer"} | {t.label for t in get_ols_descendants("MONDO:0004992")}
matched = sorted(present & cancer_labels)
print(f"{len(cancer_labels):,} cancer labels from MONDO; matched here: {matched}")

if matched:
    in_clause = ", ".join(f"'{lab}'" for lab in matched)
    print(f"{atlas.query().where(f'disease IN ({in_clause})').count():,} units from cancer tissue")

# %% [markdown]
# Label matching is lossier than CURIE matching — two ontologies can share a
# label, and spellings drift between releases. If hierarchical queries become
# common, carry the CURIE alongside the label at harmonization time.

# %%
release_memory("balanced", "obs", "joined")

# %% [markdown]
# ---
# ## 4. Loading expression
#
# `to_anndata()` needs exactly one AnnData-capable pointer field active. This
# schema has three (gene expression, protein, image features), so naming one
# is mandatory.

# %%
try:
    atlas.query().limit(10).to_anndata()
except ValueError as e:
    print("ValueError:", e)

# %%
sec = atlas.query().count(group_by="section_uid")["section_uid"][0]

adata = (
    atlas.query()
    .where(f"section_uid = '{sec}' AND has_gene_expression = true")
    .feature_spaces("gene_expression")
    .limit(20_000)
    .to_anndata()
)
print(adata)
print("X:", type(adata.X).__name__, adata.X.dtype, "| layers:", list(adata.layers.keys()))

# %% [markdown]
# `X` is raw uint32 counts in a CSR matrix, and `adata.layers` is empty — the
# only zarr layer written is `counts`, so there is no `log_normalized` to
# select with `.layers()`. Normalise yourself.
#
# `var` is indexed by feature `uid`, with the registry columns alongside.

# %%
adata.var.head(3)

# %% [markdown]
# Coordinates live on obs, so they arrive in `adata.obs` automatically. Add
# the conventional key so scanpy/squidpy plotting works, and make `var_names`
# readable — controls have no `gene_name`, so fall back to the uid.

# %%
adata.obsm["spatial"] = adata.obs[["x_um", "y_um"]].to_numpy()
adata.var["uid"] = adata.var.index
adata.var_names = adata.var["gene_name"].fillna(adata.var["uid"]).astype(str)
adata.var_names_make_unique()
print(list(adata.var_names[:5]), "...", list(adata.var_names[-3:]))

# %% [markdown]
# ### Naming the features you want
#
# `.features()` restricts the read to specific registry uids, overriding
# `feature_join`. With a CSC copy present — v0 has one — it reads only those
# features' byte ranges.
#
# Always check what actually matched: a 425-gene panel will not carry every
# marker you name.

# %%
wanted = ["EPCAM", "MKI67", "PTPRC", "VIM", "GFAP"]
markers = registry.filter(pl.col("gene_name").is_in(wanted) & (~pl.col("is_control")))
print("asked for:", wanted)
print("found:   ", markers["gene_name"].to_list())

# %%
adata_markers = (
    atlas.query()
    .where("has_gene_expression = true")
    .features(markers["uid"].to_list(), "gene_expression")
    .limit(20_000)
    .to_anndata()
)
print(f"marker panel: {adata_markers.n_obs:,} x {adata_markers.n_vars}")

# %% [markdown]
# ### The panel-raggedness trap *(future)*
#
# This corpus will mix whole-transcriptome capture (Visium, Visium HD,
# Slide-seq) with targeted panels of a few hundred probes. `feature_join`
# decides what happens when they meet:
#
# - **union** (default) — every feature from every contributing dataset.
#   Rows whose panel lacks a feature get **zero, not NaN**, and those
#   structural zeros are indistinguishable from measured zeros downstream.
# - **intersection** — only features measured by every contributing dataset.
#   Across mixed technologies this collapses to the smallest panel, or to
#   nothing.
#
# With one panel in v0 the two agree; re-run this once a second panel lands.

# %%
gex_uids = datasets.filter(pl.col("feature_space") == "gene_expression")["dataset_uid"].to_list()
ds_clause = ", ".join(f"'{u}'" for u in gex_uids)

union = (
    atlas.query()
    .where(f"dataset_uid IN ({ds_clause})")
    .feature_spaces("gene_expression")
    .limit(2_000)
    .to_anndata()
)
inter = (
    atlas.query()
    .where(f"dataset_uid IN ({ds_clause})")
    .feature_spaces("gene_expression")
    .feature_join("intersection")
    .limit(2_000)
    .to_anndata()
)
print(f"union:        {union.n_obs:,} x {union.n_vars:,}")
print(f"intersection: {inter.n_obs:,} x {inter.n_vars:,}")

# %%
release_memory("union", "inter", "adata_markers")

# %% [markdown]
# ### QC with the control probes
#
# The negative-control fraction is the specificity metric for imaging-based
# assays; it is null for capture-based ones.

# %%
qc = balanced_by_enum(
    atlas,
    "technology",
    per_group=60_000,
    select=["technology", "n_counts", "n_genes", "negative_control_counts", "cell_area_um2"],
    where="has_gene_expression = true",
).with_columns((pl.col("negative_control_counts") / pl.col("n_counts")).alias("neg_frac"))
qc.group_by("technology").agg(
    pl.len().alias("n"),
    pl.col("n_counts").median().alias("median_counts"),
    pl.col("n_genes").median().alias("median_genes"),
    pl.col("neg_frac").mean().alias("mean_neg_frac"),
)

# %% [markdown]
# ### Multimodal output
#
# `.to_multimodal()` returns every modality in its native type — AnnData for
# matrices, `SpatialTileBatch` for crops — with per-row presence masks.

# %%
mm = atlas.query().where("has_gene_expression = true").limit(2_000).to_multimodal()
print(f"{len(mm.obs):,} rows, modalities: {list(mm.mod.keys())}")
for name, data in mm.mod.items():
    print(f"  {name:20s} {type(data).__name__:18s} {int(mm.present[name].sum()):,} rows with data")

# %%
release_memory("mm", "qc")

# %% [markdown]
# ---
# ## 5. Image crops
#
# Section imagery is stored **once per section** as a `discrete_image` zarr
# array. An obs row owns a bounding box into it, not a copy of the pixels — so
# neighbouring cells' crops overlap and share bytes.

# %%
img_zg = datasets.filter(pl.col("feature_space") == "discrete_image")["zarr_group"][0]
mosaic = atlas.open_zarr_group(img_zg)["layers"]["raw"]
print(f"stored mosaic: shape={mosaic.shape} dtype={mosaic.dtype} chunks={mosaic.chunks}")

# %% [markdown]
# A single-channel uint16 mosaic — **two dimensions, no channel axis**.
#
# `he_crop` and `morphology_crop` share the feature space `discrete_image`, so
# `.feature_spaces("discrete_image")` would activate both. `.select_fields()`
# picks one.

# %%
batch = (
    atlas.query()
    .where("has_morphology_crop = true")
    .select(["uid", "x_um", "y_um", "x_px", "y_px", "n_counts"])
    .select_fields("morphology_crop")
    .limit(12)
    .to_spatial_batch("morphology_crop")
)
crops = batch.layers["raw"]
meta = batch.metadata.select(["uid", "x_um", "y_um", "x_px", "y_px", "n_counts"])

print("layers:", list(batch.layers.keys()))
print("crop:", crops[0].shape, crops[0].dtype, f"range {crops[0].min():.0f}-{crops[0].max():.0f}")
print("distinct shapes:", {c.shape for c in crops})

# %% [markdown]
# Four things to notice:
#
# 1. **Row order is zarr-group order, not query order** — take labels from
#    `batch.metadata`, never from a separate `.to_polars()`. `metadata` also
#    carries the raw pointer structs and internal `_zg` / `_min_corner` /
#    `_max_corner` columns, so select what you want.
# 2. **Crops are 2-D here.** There is no channel axis to transpose. Check
#    `.ndim` rather than assuming.
# 3. **Dtype is float32, not the stored uint16** — the reconstructor casts to
#    the feature space's layer dtype, keeping raw intensity values.
# 4. **Uniform 128×128 is a property of this ingest, not a guarantee.** The
#    batch is list-backed because boxes may be clipped at tissue edges.
#
# Corners are `(row, col)` = `(y, x)`, centred on the cell's pixel position:

# %%
raw_ptr = atlas.obs_table.search().where("has_morphology_crop = true").limit(1).to_polars()
p = raw_ptr["morphology_crop"][0]
print("min_corner:", p["min_corner"], " max_corner:", p["max_corner"])
print("y_px:", round(raw_ptr["y_px"][0], 1), " x_px:", round(raw_ptr["x_px"][0], 1))

# %% [markdown]
# Raw intensities need percentile scaling or `imshow` renders them near-black.


# %%
def norm(img, p=99.5):
    img = np.asarray(img, dtype=np.float32)
    hi = np.percentile(img, p)
    return np.clip(img / hi, 0, 1) if hi > 0 else img


fig, axes = plt.subplots(2, 6, figsize=(13, 4.5))
for ax, crop, n in zip(axes.ravel(), crops, meta["n_counts"], strict=False):
    ax.imshow(norm(crop), cmap="gray")
    ax.set_title(f"{int(n)} counts", fontsize=7)
    ax.axis("off")
fig.suptitle("Xenium morphology crops (128 x 128)", y=1.01)
fig.tight_layout()
plt.show()

# %% [markdown]
# ### Multi-channel crops *(future)*
#
# The channel-axis convention is unresolved: homeobox's `discrete_image` spec
# declares TCZYX (channels leading), while the schema YAML's `he_crop` /
# `morphology_crop` doc comments describe channels as trailing. Nothing in v0
# settles it, so write shape-agnostic code — and get `channel_names` from
# `SectionImageSchema`, which does not exist yet.


# %%
def to_hwc(crop, n_channels=None):
    """Return (H, W, C) whether channels lead, trail, or are absent."""
    if crop.ndim == 2:
        return crop[..., None]
    if n_channels is not None and crop.shape[0] == n_channels:
        return np.moveaxis(crop, 0, -1)
    if crop.shape[0] <= 5 and crop.shape[-1] > 5:
        return np.moveaxis(crop, 0, -1)
    return crop


print("v0 crop through to_hwc:", to_hwc(crops[0]).shape)

if "SectionImageSchema" in TABLES:
    imgs = atlas.db.open_table("SectionImageSchema").search().to_polars()
    print(imgs.select(["section_uid", "image_modality", "channel_names", "pixel_size_um"]))
else:
    print("SectionImageSchema absent; scale is obs.pixel_size_um =", raw_ptr["pixel_size_um"][0])

# %%
release_memory("batch", "crops", "fig", "axes")

# %% [markdown]
# ---
# ## 6. Spatial visualization
#
# `x_um` / `y_um` are microns in **each section's own frame**. Two sections'
# coordinates overlap and mean different things, so every plot fixes or facets
# by `section_uid`. Set an equal aspect ratio, and invert y — imaging
# convention has y growing downward.

# %%
df = (
    atlas.query()
    .where(f"section_uid = '{sec}'")
    .select(["x_um", "y_um", "n_counts", "n_genes", "cell_area_um2"])
    .to_polars()
)
print(f"{df.height:,} units")

fig, axes = plt.subplots(1, 3, figsize=(16, 5))
for ax, col, cmap in zip(
    axes, ["n_counts", "n_genes", "cell_area_um2"], ["viridis", "magma", "cividis"], strict=True
):
    vals = df[col].to_numpy().astype(float)
    s = ax.scatter(
        df["x_um"], df["y_um"], c=vals, s=0.5, cmap=cmap, vmax=np.nanpercentile(vals, 99)
    )
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_title(col)
    fig.colorbar(s, ax=ax, fraction=0.046)
fig.tight_layout()
plt.show()

# %% [markdown]
# ### Gene expression overlay
#
# `.features()` pulls a single column rather than the whole matrix — the right
# way to make a marker map on a 587k-cell section.

# %%
gene_uid = markers.filter(pl.col("gene_name") == "EPCAM")["uid"][0]

g = (
    atlas.query()
    .where(f"section_uid = '{sec}' AND has_gene_expression = true")
    .features([gene_uid], "gene_expression")
    .to_anndata()
)
x = g[:, gene_uid].X
vals = np.asarray(x.todense()).ravel() if hasattr(x, "todense") else np.asarray(x).ravel()

fig, ax = plt.subplots(figsize=(8, 8))
s = ax.scatter(g.obs["x_um"], g.obs["y_um"], c=np.log1p(vals), s=0.5, cmap="magma")
ax.set_aspect("equal")
ax.invert_yaxis()
ax.set_title("EPCAM (log1p counts)")
fig.colorbar(s, ax=ax, fraction=0.046)
plt.show()

# %% [markdown]
# ### Cell-type map *(future)*
#
# `cell_type` is entirely null in v0, so this returns an empty frame until a
# dataset with CL annotations lands.

# %%
ct = (
    atlas.query()
    .where(f"section_uid = '{sec}' AND cell_type IS NOT NULL")
    .select(["x_um", "y_um", "cell_type"])
    .to_polars()
)
print(f"{ct.height:,} annotated units")

if ct.height:
    top = ct["cell_type"].value_counts(sort=True).head(12)["cell_type"].to_list()
    fig, ax = plt.subplots(figsize=(8, 8))
    rest = ct.filter(~pl.col("cell_type").is_in(top))
    ax.scatter(rest["x_um"], rest["y_um"], s=1, color="lightgrey", label="other")
    for c, color in zip(top, plt.cm.tab20.colors, strict=False):
        sub = ct.filter(pl.col("cell_type") == c)
        ax.scatter(sub["x_um"], sub["y_um"], s=2, color=color, label=c)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.legend(markerscale=6, fontsize=7, bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    plt.show()

# %%
release_memory("df", "g", "x", "vals", "ct", "fig", "axes", "ax", "s")

# %% [markdown]
# ---
# ## 7. Neighbourhood analysis
#
# The analysis the atlas exists for: which labels sit next to which, in
# physical space. Pick the radius from the assay — ~30 µm is a couple of cell
# diameters for segmented data; for Visium use the 100 µm spot pitch.
#
# With `cell_type` empty in v0, we demonstrate the machinery on expression
# quartiles instead; swap `group` for `cell_type` once annotations land.

# %%
from scipy.spatial import cKDTree  # noqa: E402

nb = (
    atlas.query()
    .where(f"section_uid = '{sec}'")
    .select(["x_um", "y_um", "n_counts"])
    .limit(150_000)
    .to_polars()
    .with_columns(pl.col("n_counts").qcut(4, labels=["q1", "q2", "q3", "q4"]).alias("group"))
)

coords = nb.select(["x_um", "y_um"]).to_numpy()
cats, codes = np.unique(nb["group"].cast(pl.Utf8).to_numpy(), return_inverse=True)

pairs = cKDTree(coords).query_pairs(r=30.0, output_type="ndarray")
obs_counts = np.zeros((len(cats), len(cats)))
np.add.at(obs_counts, (codes[pairs[:, 0]], codes[pairs[:, 1]]), 1)
obs_counts += obs_counts.T

freq = np.bincount(codes, minlength=len(cats)) / len(codes)
exp_counts = np.outer(freq, freq) * obs_counts.sum()
enrich = np.log2((obs_counts + 1) / (exp_counts + 1))

print(f"{len(pairs):,} neighbour pairs within 30 µm")

fig, ax = plt.subplots(figsize=(6, 5))
im = ax.imshow(enrich, cmap="coolwarm", vmin=-1, vmax=1)
ax.set_xticks(range(len(cats)), cats)
ax.set_yticks(range(len(cats)), cats)
ax.set_title("neighbourhood enrichment, r = 30 µm\n(log2 obs/exp, counts quartiles)")
fig.colorbar(im, ax=ax, fraction=0.046)
fig.tight_layout()
plt.show()

# %%
release_memory("nb", "coords", "pairs", "obs_counts", "exp_counts", "enrich", "fig", "ax", "im")

# %% [markdown]
# ---
# ## 8. Streaming and ML
#
# `.to_batches()` streams AnnData objects for queries that would not fit in
# memory. All query parameters apply identically to every batch.

# %%
n = 0
for b in (
    atlas.query()
    .where("has_gene_expression = true")
    .feature_spaces("gene_expression")
    .limit(20_000)
    .to_batches(batch_size=4096)
):
    n += b.n_obs
print(f"streamed {n:,} units")

# %% [markdown]
# For training, `.to_unimodal_dataset()` returns a PyTorch dataset backed by
# zarr reads. Crops train the same way — `to_unimodal_dataset("morphology_crop")`
# yields `SpatialTileBatch`es at native crop shape.

# %%
dataset = (
    atlas.query()
    .where("has_gene_expression = true")
    .limit(40_960)
    .to_unimodal_dataset(
        field_name="gene_expression",
        mode="iterable",
        batch_size=128,
        io_batch_size=4096,
        shuffle=True,
        drop_last=True,
    )
)
print(f"{dataset.n_rows:,} units, {dataset.n_features:,} features")

# %%
# import torch
# loader = hox.make_loader(dataset, generator=torch.Generator().manual_seed(42))
# for batch in loader:
#     ...

# %%
release_memory("dataset", "b")

# %% [markdown]
# ---
# ## Summary
#
# | Goal | Method |
# |------|--------|
# | Filter on an enum | `.where("CAST(technology AS STRING) = 'xenium'")` |
# | Filter on an ontology column | `.where("tissue = 'colon'")` — labels, not CURIEs |
# | Filter by modality | `.where("has_morphology_crop = true")` |
# | Representative sample | `.balanced_limit(n, "section_uid")` |
# | Hierarchical disease/tissue | OLS descendants → match `.label` → `IN (...)` |
# | Pick a modality | `.feature_spaces("gene_expression")` |
# | Pick one of two crop columns | `.select_fields("morphology_crop")` |
# | Named gene panel | `.features(uids, "gene_expression")` |
# | Reconcile ragged panels | `.feature_join("intersection")` |
# | Metadata only | `.to_polars()` |
# | One modality | `.to_anndata()` |
# | Several modalities | `.to_mudata()` / `.to_multimodal()` |
# | Image crops | `.to_spatial_batch("morphology_crop")` |
# | Streaming | `.to_batches(batch_size=4096)` |
# | Training | `.to_unimodal_dataset(...)` + `hox.make_loader(...)` |
#
# **Five things specific to this atlas:**
#
# 1. Enum columns are dictionary-encoded — bare equality **raises**; cast.
# 2. Ontology columns hold labels — a CURIE predicate returns zero rows
#    silently.
# 3. Unpopulated numerics are NaN, not NULL — SQL null tests are useless.
# 4. Coordinates are per-section — never plot two sections in one frame.
# 5. `he_crop` and `morphology_crop` share a feature space, so
#    `select_fields` — not `feature_spaces` — is what picks between them.
