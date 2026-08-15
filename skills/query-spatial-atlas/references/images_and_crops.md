# Images and crops

How `he_crop` and `morphology_crop` address pixels, and how to read and display them.

Verified against snapshot v0, where only `morphology_crop` is populated.

## The storage model

Section imagery is stored **once per section** as a `discrete_image` zarr array. An obs row does not own pixels; it owns a half-open N-D bounding box `[min_corner, max_corner)` into that array. Neighbouring cells' crops overlap and share the same underlying bytes — nothing is duplicated.

```python
class DiscreteSpatialPointer:
    zarr_group: str | None
    min_corner: list[int] | None
    max_corner: list[int] | None
```

In v0 the stored array is:

```
zarr_store/<discrete_image group>/layers/raw   shape=(34338, 42905)  uint16  chunks=(512, 512)
```

a single-channel Xenium morphology mosaic — two dimensions, no channel axis. Boxes are 128×128 and **corners are `(row, col)` = `(y, x)`**, centred on the cell's `y_px` / `x_px`:

```python
{"min_corner": [3947, 18359], "max_corner": [4075, 18487]}  # y_px≈4011, x_px≈18423
```

Consequences worth knowing:

- **Crops need not be uniform.** A box is per row; a cell near the tissue edge may be clipped. `SpatialTileBatch` is list-backed for exactly this reason. In v0 they happen to all be 128×128, so `np.stack` works — but that is a property of this ingest, not a guarantee.
- **Axes the corners don't cover are taken in full.** With a 3-D `(C, Y, X)` array and 2-element corners, every channel comes back.
- **Reads are batched per zarr group**, so pulling 500 crops from one section is one read per array, not 500.

## Two columns, one feature space

`he_crop` and `morphology_crop` both declare `feature_space="discrete_image"`. This matters at query time:

```python
.feature_spaces("discrete_image")     # activates BOTH columns
.select_fields("morphology_crop")     # activates exactly one — use this
```

`to_spatial_batch(field_name)` takes the **column** name, so it is unambiguous by itself, but the row-materialisation filter follows `select_fields` — pair them:

```python
batch = (
    atlas.query()
    .where("has_morphology_crop = true")
    .select(["uid", "x_um", "y_um", "n_counts"])
    .select_fields("morphology_crop")
    .limit(64)
    .to_spatial_batch("morphology_crop")
)
```

## What comes back

```python
batch.layers  # {"raw": [ndarray, ...]}  one array per row
batch.metadata  # polars DataFrame, aligned to those lists
len(batch)  # number of rows
batch[0:16]  # slice into a sub-batch (layers + metadata together)
```

The `discrete_image` spec allows one required layer, `raw`, and two optional ones, `semantic_masks` (bool) and `instance_masks` (uint32). **Only `raw` exists in v0** — requesting the others with `.layers("discrete_image", [...])` will fail.

**Dtype is not the stored dtype.** The reconstructor casts to the feature space's layer dtype, so v0's uint16 mosaic yields **float32** crops carrying raw intensity values (roughly 500–3500 in practice). Percentile-scale before display.

**Row order is zarr-group order, not query order.** `to_spatial_batch` concatenates per-group reads without reordering. Always take labels and coordinates from `batch.metadata`; never zip crops against a separately-fetched frame.

`batch.metadata` also carries every pointer struct plus the internal `_zg`, `_min_corner`, `_max_corner` columns. Select what you need:

```python
meta = batch.metadata.select(["uid", "x_um", "y_um", "n_counts"])
```

## Shape and channel axis

**In v0, crops are 2-D `(H, W)`.** There is no channel axis, so nothing to transpose:

```python
crop = batch.layers["raw"][0]  # (128, 128) float32
plt.imshow(norm(crop), cmap="gray")
```

For multi-channel imagery the axis order is **unresolved**. Homeobox's `discrete_image` spec declares TCZYX (channels leading), which would give `(C, Y, X)` and need `crop.transpose(1, 2, 0)` for RGB. The `he_crop` / `morphology_crop` doc comments in the schema YAML instead describe channels as *trailing*. Nothing in v0 settles it. Write shape-agnostic code until it does:

```python
def to_hwc(crop, n_channels=None):
    """Return (H, W, C) whether channels lead, trail, or are absent."""
    if crop.ndim == 2:
        return crop[..., None]
    if n_channels is not None and crop.shape[0] == n_channels:
        return np.moveaxis(crop, 0, -1)
    if crop.shape[0] <= 5 and crop.shape[-1] > 5:  # heuristic: leading channel
        return np.moveaxis(crop, 0, -1)
    return crop
```

## Naming the channels

Channel identity was never meant to live on the crop — it belongs on `SectionImageSchema`, alongside `pixel_size_um`, `height_px` / `width_px`, and `is_registered_to_expression`.

**That table does not exist in the atlas as of v0**, because no rows were ingested for it. Guard before reaching for it:

```python
if "SectionImageSchema" in atlas.db.list_tables().tables:
    imgs = (
        atlas.db.open_table("SectionImageSchema")
        .search()
        .where("section_uid = 'abc123' AND image_modality = 'morphology'")
        .to_polars()
    )
    channel_names = imgs["channel_names"][0]
```

Until it is populated, the only scale information available is `pixel_size_um` on obs — 0.2125 µm/px for the v0 Xenium section — and channel identity has to come from the source dataset. Expected values once it lands: `["R", "G", "B"]` for H&E; `["DAPI", "boundary", "interior_RNA"]` for a Xenium morphology stack.

## Display

float32 crops in raw intensity range need percentile scaling — a plain `imshow` renders them near-black.

```python
def norm(img, p=99.5):
    img = img.astype(np.float32)
    hi = np.percentile(img, p)
    return np.clip(img / hi, 0, 1) if hi > 0 else img
```

### Montage

```python
batch = (
    atlas.query()
    .where("has_morphology_crop = true")
    .select(["uid", "x_um", "y_um", "n_counts"])
    .select_fields("morphology_crop")
    .limit(12)
    .to_spatial_batch("morphology_crop")
)
crops = batch.layers["raw"]
meta = batch.metadata.select(["uid", "x_um", "y_um", "n_counts"])

fig, axes = plt.subplots(2, 6, figsize=(12, 4.5))
for ax, crop, n in zip(axes.ravel(), crops, meta["n_counts"], strict=False):
    ax.imshow(norm(crop), cmap="gray")
    ax.set_title(f"{int(n)} counts", fontsize=7)
    ax.axis("off")
fig.tight_layout()
```

### Multi-channel crop, one panel per channel

Only once multi-channel imagery is ingested; `channel_names` must come from `SectionImageSchema` or the source.

```python
hwc = to_hwc(crop, len(channel_names))
fig, axes = plt.subplots(1, hwc.shape[-1], figsize=(3 * hwc.shape[-1], 3))
for ax, name, c in zip(np.atleast_1d(axes), channel_names, range(hwc.shape[-1]), strict=True):
    ax.imshow(norm(hwc[..., c]), cmap="gray")
    ax.set_title(name, fontsize=9)
    ax.axis("off")
```

### Crops beside their position in the section

Because `batch.metadata` carries obs columns, a crop can be located in the section map without a second query:

```python
fig, (ax_map, ax_crop) = plt.subplots(1, 2, figsize=(11, 5))
section = atlas.query().where("tissue = 'colon'").select(["x_um", "y_um"]).limit(50_000).to_polars()
ax_map.scatter(section["x_um"], section["y_um"], s=1, c="lightgrey")
ax_map.scatter(meta["x_um"][:12], meta["y_um"][:12], s=30, c="crimson")
ax_map.set_aspect("equal")
ax_map.invert_yaxis()
ax_crop.imshow(norm(crops[0]), cmap="gray")
ax_crop.axis("off")
```

## Whole-section images

The atlas stores crops, not a whole-section reader. To work with a full section image, read the zarr group directly:

```python
zg = atlas.list_datasets().filter(pl.col("feature_space") == "discrete_image")["zarr_group"][0]
arr = atlas.open_zarr_group(zg)["layers"]["raw"]  # (34338, 42905) uint16, lazily sliceable
thumb = arr[::32, ::32]  # decimated overview
```

Listing arrays with `group.array_keys()` can emit spurious `ZarrUserWarning: Object at ... is not recognized` lines and return nothing — index by name instead. Full-resolution section images are large; always decimate or slice.

## Overlaying expression on imagery

Obs `x_px` / `y_px` are in the image frame, so expression scatters onto a decimated overview after dividing the pixel coordinates by the same decimation factor. This is only trustworthy once `SectionImageSchema.is_registered_to_expression` confirms the image frame is aligned to the expression frame — which cannot be checked until that table exists.
