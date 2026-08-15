# Plotting recipes

Self-contained recipes for the spatial atlas. All assume:

```python
import homeobox as hox, numpy as np, polars as pl, matplotlib.pyplot as plt

atlas = hox.RaggedAtlas.checkout_latest(ATLAS_PATH)
```

Two things to know before running these against snapshot v0:

- **`cell_type` is entirely null in v0**, so the cell-type map and neighbourhood-enrichment recipes return empty frames until a dataset with CL annotations lands. They are written for that future, not for v0.
- **Enum columns need `CAST(col AS STRING)`** in any `.where()` predicate (`technology`, `spatial_unit`, `disease_state`, `segmentation_method`) and `.cast(pl.Utf8)` in polars. The recipes below filter on string columns (`section_uid`, `tissue`) which need no cast.

## The coordinate contract

`x_um` / `y_um` are microns in **each section's own frame**. Two sections' coordinates overlap and mean different things. Every plot below therefore either fixes one `section_uid` or facets by it.

Three defaults that keep tissue looking like tissue:

- `ax.set_aspect("equal")` — otherwise the section is sheared.
- `ax.invert_yaxis()` — imaging convention has y growing downward, so without this the tissue appears vertically mirrored.
- small `s` and `alpha` — sections run from thousands (Visium) to millions (Xenium) of units.

Pick a section to work with:

```python
sections = atlas.query().count(group_by="section_uid").sort("count", descending=True)
sec = sections["section_uid"][0]
```

## Density / QC map

```python
df = (
    atlas.query()
    .where(f"section_uid = '{sec}'")
    .select(["x_um", "y_um", "n_counts", "n_genes", "cell_area_um2"])
    .to_polars()
)

fig, axes = plt.subplots(1, 3, figsize=(16, 5))
for ax, col, cmap in zip(
    axes, ["n_counts", "n_genes", "cell_area_um2"], ["viridis", "magma", "cividis"], strict=True
):
    vals = df[col].to_numpy()
    hi = np.nanpercentile(vals, 99)
    s = ax.scatter(df["x_um"], df["y_um"], c=vals, s=2, cmap=cmap, vmax=hi)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_title(col)
    fig.colorbar(s, ax=ax, fraction=0.046)
fig.tight_layout()
```

Clipping at the 99th percentile matters — count distributions are long-tailed enough that a handful of units otherwise flatten the whole map.

## Cell-type map

```python
df = (
    atlas.query()
    .where(f"section_uid = '{sec}' AND cell_type IS NOT NULL")
    .select(["x_um", "y_um", "cell_type", "cell_type_original"])
    .to_polars()
)

top = df["cell_type"].value_counts(sort=True).head(12)["cell_type"].to_list()
palette = dict(zip(top, plt.cm.tab20.colors, strict=False))

fig, ax = plt.subplots(figsize=(8, 8))
for ct in top:
    sub = df.filter(pl.col("cell_type") == ct)
    ax.scatter(sub["x_um"], sub["y_um"], s=2, color=palette[ct], label=ct)
rest = df.filter(~pl.col("cell_type").is_in(top))
ax.scatter(rest["x_um"], rest["y_um"], s=1, color="lightgrey", label="other")
ax.set_aspect("equal")
ax.invert_yaxis()
ax.legend(markerscale=6, fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")
```

`cell_type` is a CL CURIE, so legends read as `CL:0000540`. To label with names, resolve once:

```python
from polycomb.ols import get_ols_term

labels = {c: (get_ols_term(c).label if get_ols_term(c) else c) for c in top}
```

Or plot `cell_type_original`, which holds the published label — useful for checking a harmonization, but not comparable across datasets.

## Gene expression overlay

```python
registry = atlas.feature_registry("gene_expression")
gene = registry.filter((pl.col("gene_name") == "EGFR") & (~pl.col("is_control")))
gene_uid = gene["uid"][0]

adata = (
    atlas.query()
    .where(f"section_uid = '{sec}' AND has_gene_expression = true")
    .features([gene_uid], "gene_expression")
    .to_anndata()
)

x = adata[:, gene_uid].X
vals = np.asarray(x.todense()).ravel() if hasattr(x, "todense") else np.asarray(x).ravel()

fig, ax = plt.subplots(figsize=(8, 8))
s = ax.scatter(
    adata.obs["x_um"],
    adata.obs["y_um"],
    c=np.log1p(vals),
    s=2,
    cmap="rocket_r" if "rocket_r" in plt.colormaps() else "magma",
)
ax.set_aspect("equal")
ax.invert_yaxis()
ax.set_title("EGFR (log1p counts)")
fig.colorbar(s, ax=ax, fraction=0.046)
```

`.features([...])` pulls a single column instead of the whole matrix — the right way to make a marker map on a large section.

Multiple genes: pass several uids, then loop over subplots.

## Multi-section facets

```python
secs = (
    atlas.query()
    .where("tissue = 'colon'")
    .count(group_by="section_uid")
    .sort("count", descending=True)
    .head(6)
)

fig, axes = plt.subplots(2, 3, figsize=(15, 10))
for ax, s_uid in zip(axes.ravel(), secs["section_uid"], strict=False):
    d = (
        atlas.query()
        .where(f"section_uid = '{s_uid}'")
        .select(["x_um", "y_um", "n_counts"])
        .to_polars()
    )
    ax.scatter(d["x_um"], d["y_um"], s=1, c=np.log1p(d["n_counts"]), cmap="viridis")
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_title(f"{s_uid[:8]} · {len(d):,} units", fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
fig.tight_layout()
```

Each panel keeps its own axis limits — that is correct, since the frames are unrelated. Do not share axes here.

## Corpus composition

```python
by_tech = atlas.query().count(group_by=["technology", "spatial_unit"])

fig, ax = plt.subplots(figsize=(9, 4))
piv = by_tech.pivot(index="technology", on="spatial_unit", values="count").fill_null(0)
bottom = np.zeros(len(piv))
for col in [c for c in piv.columns if c != "technology"]:
    ax.bar(piv["technology"], piv[col], bottom=bottom, label=col)
    bottom += piv[col].to_numpy()
ax.set_yscale("log")
ax.set_ylabel("spatial units")
ax.legend(fontsize=8)
plt.xticks(rotation=45, ha="right")
```

Log scale is not optional: a Visium section has ~5k spots and a Xenium run can have ~10⁶ cells.

## QC across technologies

`balanced_limit` cannot take an enum column, so balance across technologies by hand:

```python
cols = ["technology", "n_counts", "n_genes", "negative_control_counts"]
qc = pl.concat(
    [
        atlas.query()
        .where(f"has_gene_expression = true AND CAST(technology AS STRING) = '{t}'")
        .select(cols)
        .limit(60_000)
        .to_polars()
        for t in atlas.query().count(group_by="technology")["technology"].to_list()
    ],
    how="vertical_relaxed",
)

fig, axes = plt.subplots(1, 2, figsize=(13, 4))
techs = qc["technology"].unique().sort().to_list()
axes[0].boxplot(
    [qc.filter(pl.col("technology") == t)["n_counts"].drop_nulls().to_numpy() for t in techs],
    labels=techs,
    showfliers=False,
)
axes[0].set_yscale("log")
axes[0].set_ylabel("counts per unit")

imaging = qc.filter(pl.col("negative_control_counts").is_not_null()).with_columns(
    (pl.col("negative_control_counts") / pl.col("n_counts")).alias("neg_frac")
)
itechs = imaging["technology"].unique().sort().to_list()
axes[1].boxplot(
    [imaging.filter(pl.col("technology") == t)["neg_frac"].to_numpy() for t in itechs],
    labels=itechs,
    showfliers=False,
)
axes[1].set_ylabel("negative-control fraction")
for ax in axes:
    ax.tick_params(axis="x", rotation=45)
fig.tight_layout()
```

Sampling per technology is what makes this comparison fair — a plain `.limit()` would draw entirely from whichever technology sits first in storage order.

## Neighbourhood enrichment

The analysis the atlas exists for: which cell types sit next to which, in physical space.

```python
from scipy.spatial import cKDTree

df = (
    atlas.query()
    .where(f"section_uid = '{sec}' AND cell_type IS NOT NULL")
    .select(["x_um", "y_um", "cell_type"])
    .to_polars()
)

coords = df.select(["x_um", "y_um"]).to_numpy()
types = df["cell_type"].to_numpy()
cats = np.unique(types)
idx = {c: i for i, c in enumerate(cats)}
codes = np.array([idx[t] for t in types])

pairs = cKDTree(coords).query_pairs(r=30.0, output_type="ndarray")  # 30 µm radius

obs_counts = np.zeros((len(cats), len(cats)))
np.add.at(obs_counts, (codes[pairs[:, 0]], codes[pairs[:, 1]]), 1)
obs_counts += obs_counts.T

# Expected under random labelling, given the same pair count
freq = np.bincount(codes, minlength=len(cats)) / len(codes)
exp_counts = np.outer(freq, freq) * obs_counts.sum()
enrich = np.log2((obs_counts + 1) / (exp_counts + 1))

fig, ax = plt.subplots(figsize=(8, 7))
im = ax.imshow(enrich, cmap="coolwarm", vmin=-2, vmax=2)
ax.set_xticks(range(len(cats)), cats, rotation=90, fontsize=7)
ax.set_yticks(range(len(cats)), cats, fontsize=7)
ax.set_title(f"neighbourhood enrichment, r=30 µm (log2 obs/exp)")
fig.colorbar(im, ax=ax, fraction=0.046)
```

Pick the radius from the assay: ~30 µm is a couple of cell diameters for segmented data. For Visium, use the spot pitch (100 µm) instead. Compare across sections only when the radius means the same thing physically in both.

`unit_size_um` on obs gives the footprint for grid-based assays and is the right thing to key the radius off when mixing platforms.

## scanpy / squidpy

Everything above is plain matplotlib so it works without extra dependencies. If scanpy is available, set the conventional keys and its plotting works directly:

```python
adata.obsm["spatial"] = adata.obs[["x_um", "y_um"]].to_numpy()
adata.var["uid"] = adata.var.index
adata.var_names = adata.var["gene_name"].fillna(adata.var["uid"]).astype(str)
adata.var_names_make_unique()

import scanpy as sc

sc.pl.embedding(adata, basis="spatial", color=["EGFR", "cell_type"])
```

Restrict to one section first — `sc.pl.embedding` has no notion of separate coordinate frames and will overplot sections on top of each other.
