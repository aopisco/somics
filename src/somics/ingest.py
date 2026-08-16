"""Ingest a finalized spatial data package into the somics atlas.

This is the last step of the polycomb pipeline: `create-data-package` →
`prepare-package-for-resolution` → `schema-harmonization` → `finalize-tables` →
here. Everything the atlas needs is already on disk by now — obs rows keyed and
linked, features registered, dataset rows carrying their `zarr_group` — except
the arrays themselves, which have never been written. This module writes them.

Three feature spaces, and they are written in fundamentally different ways:

- **`gene_expression`** is a row stream. The 10x `cell_feature_matrix.h5` stores
  the panel as CSC over a (features × cells) matrix, which is bit-identical to
  CSR over (cells × features) — the same three arrays, read as the transpose —
  so it drops straight into homeobox's `AnnDataReader` with no transpose and no
  copy. Emitted row *i* is pointer row *i*. A CosMx flat expression CSV is the
  other shape this space arrives in: dense, and carrying one unassigned-
  transcript row per field of view that has no obs row to belong to.
- **`protein_abundance`** is a row stream too, but dense and narrow — the imaging
  channels measured on the same cells, from a five-channel morphology stain up to
  a 58-channel CODEX run. A dataset may carry this space and no expression at
  all, which is what an imaging proteomics assay is.
- **`discrete_image`** is not a row stream at all. The section image is written
  once, at full resolution, and every obs row addresses a crop box into it.
  That is the whole reason the atlas schema stores imagery this way rather than
  as `image_tiles`: 587k overlapping 128² crops would be ~19 GB of duplicated
  pixels, against 2.9 GB for the image itself.

  Which pointer an image fills — `he_crop` or `morphology_crop`, the schema's
  two `discrete_image` pointers — is read from the collection's
  `SectionImageSchema` table, whose `image_modality` column is the atlas's own
  record of what the pixels depict. Guessing from the array shape would be
  wrong the first time a multichannel morphology stack appears.

Run:
    python -m somics.ingest <collection_root> [--atlas PATH] [--schema PATH]
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Generator

import anndata as ad
import h5py
import lancedb
import numpy as np
import pandas as pd
import scipy.sparse as sp
import tifffile
from homeobox.atlas import create_or_open_atlas
from homeobox.ingestion import AnnDataReader, add_csc
from polycomb.ingestion import (
    LoaderContext,
    LoaderResult,
    SpatialLoaderResult,
    _resolve_schema,
    ingest_collection,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SCHEMA = os.path.join(REPO_ROOT, "schema", "spatial_transcriptomics_atlas_schema.yaml")
DEFAULT_ATLAS = "/home/ubuntu/polycomb_atlases/somics_spatial_atlas"

# Crop side, in full-resolution pixels — one constant for the whole atlas, so
# crops from any platform stack into a batch without resizing. At Xenium's
# 0.2125 µm/px it is 27.2 µm, containing a cell of the 99.9th-percentile size
# (139 px equivalent diameter) with context to spare; at Visium's ~0.571 µm/px
# it is 73 µm, containing the 55 µm capture spot (96 px) with a margin.
CROP_PX = 128

# One slab of tile rows. The Xenium morphology TIFF is tiled 1024², so a slab of
# 1024 rows is a whole row of tiles: no tile is decoded twice, and only ~90 MB
# is resident at a time regardless of how large the image is.
SLAB_ROWS = 1024

# `discrete_image` backs both `he_crop` and `morphology_crop`, so the write has
# to name one. SectionImageSchema.image_modality says which.
FIELD_BY_IMAGE_MODALITY = {
    "he": "he_crop",
    "morphology": "morphology_crop",
    "immunofluorescence": "morphology_crop",
    "dapi": "morphology_crop",
}
# Used when the collection ships no SectionImageSchema table (the Xenium package
# predates it) — that collection's only imagery is a morphology stack.
DEFAULT_IMAGE_FIELD = "morphology_crop"

# Zarr chunk/shard edge over the spatial axes. Trailing channel axes are taken
# in full so a crop read fetches whole pixels, never a partial channel set.
CHUNK_EDGE = 512
SHARD_EDGE = 2048


# ===========================================================================
# discrete_image: the section TIFF
# ===========================================================================


class TiffImageSource:
    """Streams the full-resolution level of a TIFF as horizontal slabs.

    TIFF is not one of homeobox's built-in source formats, and a pyramidal one
    cannot simply be read whole — only the base level belongs in the atlas, and
    even that is multiple gigabytes. ``tifffile`` decodes an arbitrary region,
    so each slab is read on demand and released once written.

    The image must be ``(Y, X)`` or ``(Y, X, C)``: a `DiscreteSpatialPointer`
    boxes the *leading* axes and reads trailing ones in full, which is exactly
    how the schema stores channels — named on the section image row, not
    addressed by the crop. A channels-first ``(C, Y, X)`` image would instead
    need rank-3 boxes whose first entry is a channel, so it is rejected rather
    than silently transposed.
    """

    def __init__(self, path: str, *, level: int = 0, slab_rows: int = SLAB_ROWS) -> None:
        self.path = path
        self.level = level
        self.slab_rows = slab_rows
        with tifffile.TiffFile(path) as tif:
            if not tif.series:
                raise ValueError(f"{path}: no image series")
            base = tif.series[0].levels[level]
            self.shape: tuple[int, ...] = tuple(int(d) for d in base.shape)
            self.dtype = np.dtype(base.dtype)
            self.axes = base.axes
        # tifffile names the axes: Y/X are spatial, S is an RGB sample axis and
        # C a channel axis, both of which TIFF stores after the spatial ones.
        if self.axes not in ("YX", "YXS", "YXC"):
            raise NotImplementedError(
                f"{path}: expected (Y, X) or (Y, X, channel) axes, got shape {self.shape} "
                f"with axes {self.axes!r}. Crop boxes address the leading axes, so a "
                f"channels-first image needs a box-rank convention first."
            )
        self.n_spatial_axes = 2

    @property
    def spatial_shape(self) -> tuple[int, int]:
        """The ``(height, width)`` the crop boxes address."""
        return (self.shape[0], self.shape[1])

    def grid(self, edge: int) -> tuple[int, ...]:
        """A square-ish chunk/shard grid over the spatial axes, channels in full."""
        spatial = tuple(min(edge, d) for d in self.shape[: self.n_spatial_axes])
        return spatial + tuple(self.shape[self.n_spatial_axes :])

    def layer_specs(self, layer_mapping: dict[str, str]) -> dict[str, tuple[tuple[int, ...], type]]:
        return {dest: (self.shape, self.dtype) for dest in layer_mapping.values()}

    def iter_blocks(
        self, layer_mapping: dict[str, str]
    ) -> Generator[tuple[tuple[slice, ...], dict[str, np.ndarray]]]:
        height = self.shape[0]
        for start in range(0, height, self.slab_rows):
            stop = min(start + self.slab_rows, height)
            slab = tifffile.imread(
                self.path,
                level=self.level,
                selection=(slice(start, stop), slice(None)),
            )
            yield (slice(start, stop),), {dest: slab for dest in layer_mapping.values()}


def centered_boxes(
    centers_yx: np.ndarray, *, size: int, image_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Half-open ``[min, max)`` boxes of side ``size`` around each centroid.

    Boxes at the edge of the section are slid inward rather than clipped: a
    uniform crop shape is what lets a dataloader stack a batch without padding,
    and a cell whose centroid sits 3 px from the border still wants 128 px of
    surrounding tissue — just not centered on it.
    """
    height, width = image_shape
    if size > min(height, width):
        raise ValueError(f"crop size {size} exceeds the image {image_shape}")

    corners = np.rint(centers_yx).astype(np.int64) - size // 2
    corners[:, 0] = np.clip(corners[:, 0], 0, height - size)
    corners[:, 1] = np.clip(corners[:, 1], 0, width - size)
    return corners, corners + size


def load_section_image(ctx: LoaderContext, *, field_name: str) -> SpatialLoaderResult:
    """One image per section, plus one crop box per obs row."""
    tif_paths = [p for p in ctx.data_files if p.endswith((".tif", ".tiff"))]
    if len(tif_paths) != 1:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: expected exactly one TIFF, got {tif_paths}"
        )
    source = TiffImageSource(tif_paths[0])

    if ctx.obs_table is None:
        raise ValueError(f"{ctx.dataset_name}: obs table is required to place crop boxes")
    missing = [c for c in ("y_px", "x_px") if c not in ctx.obs_table.column_names]
    if missing:
        raise ValueError(
            f"{ctx.dataset_name}: obs is missing {missing}; crop boxes are placed from the "
            f"cell centroid in full-resolution pixels"
        )
    centers = np.column_stack(
        [
            np.asarray(ctx.obs_table.column(axis).to_numpy(zero_copy_only=False), dtype=np.float64)
            for axis in ("y_px", "x_px")
        ]
    )
    if np.isnan(centers).any():
        n_null = int(np.isnan(centers).any(axis=1).sum())
        raise ValueError(
            f"{ctx.dataset_name}: {n_null} obs row(s) have no pixel coordinate; every row must "
            f"be placeable in the image, or the feature space needs an obs artifact to cover "
            f"only the rows that are"
        )

    min_corners, max_corners = centered_boxes(
        centers, size=CROP_PX, image_shape=source.spatial_shape
    )
    print(
        f"  {ctx.feature_space}: image {source.shape} {source.dtype} -> {field_name}, "
        f"{len(min_corners)} crop(s) of {CROP_PX}x{CROP_PX} px"
    )
    return SpatialLoaderResult(
        source=source,
        layer_mapping={"image": "raw"},
        min_corners=min_corners,
        max_corners=max_corners,
        chunk_shape=source.grid(CHUNK_EDGE),
        shard_shape=source.grid(SHARD_EDGE),
        field_name=field_name,
    )


def image_fields_by_dataset(collection_root: str) -> dict[str, str]:
    """Map each dataset name to the obs pointer field its image fills.

    Read from the collection's ``SectionImageSchema`` rows, which carry the
    ``dataset_uid`` whose zarr group holds the pixels and the ``image_modality``
    they depict. Collections without that table fall back to the default.
    """
    manifest_path = os.path.join(collection_root, "collection.json")
    with open(manifest_path) as handle:
        manifest = json.load(handle)
    name_by_uid = {
        entry["dataset_uid"]: name for name, entry in manifest.get("datasets", {}).items()
    }

    db_path = os.path.join(collection_root, "lance_db")
    if not os.path.isdir(db_path):
        return {}
    db = lancedb.connect(db_path)
    if "SectionImageSchema" not in db.list_tables().tables:
        return {}

    fields: dict[str, str] = {}
    for row in db.open_table("SectionImageSchema").to_arrow().to_pylist():
        dataset_name = name_by_uid.get(row.get("dataset_uid"))
        if dataset_name is None:
            continue
        modality = row.get("image_modality")
        field = FIELD_BY_IMAGE_MODALITY.get(modality)
        if field is None:
            raise ValueError(
                f"{dataset_name}: SectionImageSchema.image_modality {modality!r} maps to no "
                f"obs pointer; known modalities are {sorted(FIELD_BY_IMAGE_MODALITY)}"
            )
        previous = fields.setdefault(dataset_name, field)
        if previous != field:
            raise ValueError(
                f"{dataset_name}: two section images want different pointers "
                f"({previous}, {field}); one zarr group holds one image"
            )
    return fields


def make_image_loader(fields_by_dataset: dict[str, str]):
    """Bind the per-dataset pointer choice into a discrete_image loader."""

    def load(ctx: LoaderContext) -> SpatialLoaderResult:
        field_name = fields_by_dataset.get(ctx.dataset_name, DEFAULT_IMAGE_FIELD)
        return load_section_image(ctx, field_name=field_name)

    return load


# ===========================================================================
# gene_expression: the 10x cell-feature matrix
# ===========================================================================


def read_10x_h5_csr(path: str) -> sp.csr_matrix:
    """Read a 10x ``cell_feature_matrix.h5`` as a (cells × features) CSR matrix.

    The file holds CSC over a (features × cells) matrix. CSC of A is CSR of Aᵀ
    with the *same* ``data``/``indices``/``indptr`` arrays, so reading it as CSR
    at the transposed shape is exact and free — no transpose, no re-sort, and
    row order is the file's ``barcodes`` order, which is what the finalized obs
    artifact was built against.
    """
    with h5py.File(path, "r") as handle:
        group = handle["matrix"]
        n_features, n_cells = (int(v) for v in group["shape"][:])
        matrix = sp.csr_matrix(
            (group["data"][:], group["indices"][:], group["indptr"][:]),
            shape=(n_cells, n_features),
        )
    return matrix


def read_cosmx_expr_csr(path: str) -> tuple[sp.csr_matrix, list[str]]:
    """Read a CosMx ``exprMat_file.csv`` as a (cells × targets) CSR matrix.

    The flat file is a dense cell-by-target table with two leading key columns,
    and it carries one extra row per field of view — ``cell_ID == 0``, holding
    the transcripts segmentation assigned to no cell. Those rows have no obs row
    to belong to, so they are dropped; what remains is the vendor's cell
    metadata file row for row, which is the order the obs artifact was built in.
    """
    columns = pd.read_csv(path, nrows=0).columns.tolist()
    targets = columns[2:]
    frame = pd.read_csv(path, dtype=dict.fromkeys(columns, np.int32))
    frame = frame[frame["cell_ID"] != 0]
    return sp.csr_matrix(frame[targets].to_numpy()), targets


def var_in_matrix_order(
    ctx: LoaderContext, names: list[str], *, name_columns: tuple[str, ...]
) -> pd.DataFrame:
    """Reorder the feature registry to the matrix's column order.

    Homeobox writes registry row *j* as feature column *j*, but a registry table
    reaches ingestion in whatever physical order harmonization left it in —
    an in-place value update rewrites fragments and does not preserve row order.
    So the rows are looked up by name instead of trusted positionally.

    ``name_columns`` are tried in order for each row, which is how one lookup
    covers a panel whose control probes have no gene symbol: the first non-null
    of (gene_name, feature_id) is the name the matrix column carries.
    """
    if ctx.var_table is None:
        raise ValueError(f"{ctx.dataset_name}/{ctx.feature_space}: no feature registry table")
    var_df = ctx.var_table.to_pandas()
    missing = [column for column in name_columns if column not in var_df.columns]
    if missing:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: registry has no {missing} column(s) to "
            f"match matrix columns on; available: {list(var_df.columns)}"
        )

    keys = var_df[list(name_columns)].bfill(axis=1).iloc[:, 0]
    position = {}
    for row, key in enumerate(keys):
        if key in position:
            raise ValueError(
                f"{ctx.dataset_name}/{ctx.feature_space}: {key!r} names two registry rows; "
                f"matrix columns cannot be matched unambiguously"
            )
        position[key] = row

    absent = [name for name in names if name not in position]
    if absent:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: {len(absent)} matrix column(s) are not in "
            f"the feature registry; examples: {absent[:5]}"
        )
    if len(names) != len(var_df):
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: matrix has {len(names)} column(s) but the "
            f"registry has {len(var_df)} row(s)"
        )
    return var_df.iloc[[position[name] for name in names]].reset_index(drop=True)


def load_gene_expression(ctx: LoaderContext) -> LoaderResult:
    """10x ``cell_feature_matrix.h5`` or a CosMx flat expression CSV."""
    paths = [p for p in ctx.data_files if p.endswith((".h5", ".csv"))]
    if len(paths) != 1:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: expected exactly one .h5 or .csv, got {paths}"
        )
    path = paths[0]

    if path.endswith(".h5"):
        matrix = read_10x_h5_csr(path)
        if ctx.var_table is None:
            raise ValueError(f"{ctx.dataset_name}/{ctx.feature_space}: no feature registry table")
        if matrix.shape[1] != ctx.var_table.num_rows:
            raise ValueError(
                f"{ctx.dataset_name}: matrix has {matrix.shape[1]} feature column(s) but the "
                f"registry has {ctx.var_table.num_rows} row(s); the var table must be in matrix "
                f"column order"
            )
        var_df = ctx.var_table.to_pandas()
    else:
        matrix, targets = read_cosmx_expr_csr(path)
        var_df = var_in_matrix_order(ctx, targets, name_columns=("gene_name", "feature_id"))

    print(f"  {ctx.feature_space}: {matrix.shape[0]} x {matrix.shape[1]}, {matrix.nnz} nonzero")
    return LoaderResult(
        reader=AnnDataReader(ad.AnnData(X=matrix)),
        layer_mapping={"X": "counts"},
        n_vars=matrix.shape[1],
        var_df=var_df,
    )


# ===========================================================================
# protein_abundance: the per-cell morphology-channel intensities
# ===========================================================================


def load_protein_abundance(ctx: LoaderContext) -> LoaderResult:
    """A dense cell × target table of fluorescence intensities.

    Unlike an antibody-derived-tag count, this is the mean intensity of one
    imaging channel inside the segmented cell, which is what the feature space's
    ``counts`` layer stores. Sources that report it as a fraction — a mean over
    a pixel set is rarely an integer — are rounded when the package is built,
    not here, so that the loss is recorded once at a documented place rather
    than silently at the write. Other statistics of the same channel travel on
    the obs row's ``additional_metadata``, since a target is a target however
    many statistics are computed from it.
    """
    paths = [p for p in ctx.data_files if p.endswith(".csv")]
    if len(paths) != 1:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: expected exactly one .csv, got {paths}"
        )
    frame = pd.read_csv(paths[0])
    var_df = var_in_matrix_order(ctx, list(frame.columns), name_columns=("target_name",))
    values = frame.to_numpy(dtype=np.uint32)

    print(f"  {ctx.feature_space}: {values.shape[0]} x {values.shape[1]} dense")
    return LoaderResult(
        reader=AnnDataReader(ad.AnnData(X=values)),
        layer_mapping={"X": "counts"},
        n_vars=values.shape[1],
        var_df=var_df,
    )


def build_loaders(collection_root: str) -> dict:
    """Per-feature-space loaders for one collection."""
    return {
        "gene_expression": load_gene_expression,
        "protein_abundance": load_protein_abundance,
        "discrete_image": make_image_loader(image_fields_by_dataset(collection_root)),
    }


# ===========================================================================
# Entry point
# ===========================================================================


def sparse_zarr_groups(collection_root: str, schema, feature_space: str) -> list[str]:
    """Zarr group paths written for ``feature_space``, read from the finalized dataset tables."""
    groups: list[str] = []
    for name in os.listdir(collection_root):
        db_path = os.path.join(collection_root, name, "lance_db")
        if not os.path.isdir(db_path):
            continue
        db = lancedb.connect(db_path)
        if schema.dataset_class not in db.list_tables().tables:
            continue
        for row in db.open_table(schema.dataset_class).to_arrow().to_pylist():
            if row["feature_space"] == feature_space and row.get("zarr_group"):
                groups.append(row["zarr_group"])
    return groups


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection_root", help="Finalized data package (holds collection.json)")
    parser.add_argument("--atlas", default=DEFAULT_ATLAS, help="Atlas path; created if absent")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, help="Atlas schema YAML IR")
    parser.add_argument(
        "--no-csc",
        action="store_true",
        help="Skip the feature-oriented (CSC) copy of the expression matrix",
    )
    parser.add_argument("--no-snapshot", action="store_true", help="Leave the atlas unsnapshotted")
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help=(
            "Skip the post-write optimize(). It is what assigns global_index to newly "
            "registered features, so snapshot() will refuse to run without it; it also "
            "compacts the obs table, which has once produced a fragment whose struct null "
            "buffer disagreed with its row count and made every filtered read of the crop "
            "pointers fail. Check with scripts/rewrite_obs_fragments.py --check-only after."
        ),
    )
    args = parser.parse_args(argv)
    collection_root = os.path.abspath(args.collection_root)
    schema_path = os.path.abspath(args.schema)
    atlas_path = os.path.abspath(args.atlas)

    report = ingest_collection(
        collection_root=collection_root,
        schema_path=schema_path,
        atlas_path=atlas_path,
        loaders=build_loaders(collection_root),
    )

    # ingest_collection deliberately stops at the write. Reopening the atlas
    # here — through the same schema resolution it used, so the classes match
    # exactly — covers the post-write steps: global_index assignment, the
    # optional feature-major copy, and the version record.
    schema = _resolve_schema(schema_path)
    atlas = create_or_open_atlas(
        atlas_path,
        obs_schemas={schema.obs_class: schema.obs_cls},
        dataset_table_name=schema.dataset_class,
        dataset_schema=schema.dataset_cls,
        registry_schemas=schema.feature_space_registry(),
    )

    if not args.no_csc:
        # A 541-gene panel is queried by gene far more often than by cell, and
        # the CSC copy is what makes a feature-filtered scan cheap.
        field = schema.pointer_for("gene_expression").field_name
        groups = sparse_zarr_groups(collection_root, schema, "gene_expression")
        for group in groups:
            print(f"writing feature-oriented copy for {group}")
            add_csc(atlas, group, field, obs_table_name=schema.obs_class)

    if not args.no_optimize:
        atlas.optimize()
    if not args.no_snapshot:
        version = atlas.snapshot()
        print(f"atlas snapshot v{version}")
    print(report)


if __name__ == "__main__":
    main()
