"""Ingest a finalized spatial data package into the somics atlas.

This is the last step of the polycomb pipeline: `create-data-package` →
`prepare-package-for-resolution` → `schema-harmonization` → `finalize-tables` →
here. Everything the atlas needs is already on disk by now — obs rows keyed and
linked, features registered, dataset rows carrying their `zarr_group` — except
the arrays themselves, which have never been written. This module writes them.

Two feature spaces, and they are written in fundamentally different ways:

- **`gene_expression`** is a row stream. The 10x `cell_feature_matrix.h5` stores
  the panel as CSC over a (features × cells) matrix, which is bit-identical to
  CSR over (cells × features) — the same three arrays, read as the transpose —
  so it drops straight into homeobox's `AnnDataReader` with no transpose and no
  copy. Emitted row *i* is pointer row *i*.
- **`discrete_image`** is not a row stream at all. The morphology image is
  written once, at full resolution, and every cell addresses a crop box into it.
  That is the whole reason the atlas schema stores imagery this way rather than
  as `image_tiles`: 587k overlapping 128² crops would be ~19 GB of duplicated
  pixels, against 2.9 GB for the image itself.

Run:
    python -m somics.ingest <collection_root> [--atlas PATH] [--schema PATH]
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Generator

import anndata as ad
import h5py
import lancedb
import numpy as np
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

# Crop side, in full-resolution pixels. At Xenium's 0.2125 µm/px this is 27.2 µm,
# which contains a cell of the 99.9th-percentile size (139 px equivalent
# diameter) with context to spare, while staying a power of two for batching.
CROP_PX = 128

# One slab of tile rows. The Xenium morphology TIFF is tiled 1024², so a slab of
# 1024 rows is a whole row of tiles: no tile is decoded twice, and only ~90 MB
# is resident at a time regardless of how large the image is.
SLAB_ROWS = 1024

# Obs pointer field the morphology image fills. `discrete_image` backs both
# `he_crop` and `morphology_crop`, so the write has to name one.
MORPHOLOGY_FIELD = "morphology_crop"


# ===========================================================================
# discrete_image: the morphology OME-TIFF
# ===========================================================================


class OmeTiffImageSource:
    """Streams the full-resolution level of an OME-TIFF as horizontal slabs.

    OME-TIFF is not one of homeobox's built-in source formats, and a pyramidal
    one cannot simply be read whole — only the base level belongs in the atlas,
    and even that is multiple gigabytes. ``tifffile`` decodes an arbitrary
    region, so each slab is read on demand and released once written.
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
        if len(self.shape) != 2:
            # A DiscreteSpatialPointer's corners address the *leading* axes, so
            # a (C, Y, X) image would need rank-3 boxes whose first entry is a
            # channel — a decision about how this atlas addresses multi-channel
            # morphology, not something to guess per dataset.
            raise NotImplementedError(
                f"{path}: expected a 2-D (Y, X) image, got shape {self.shape} with axes "
                f"{self.axes!r}. Multi-channel imagery needs a box-rank convention first."
            )

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


def load_morphology(ctx: LoaderContext) -> SpatialLoaderResult:
    """One image per section, plus one crop box per cell."""
    tif_paths = [p for p in ctx.data_files if p.endswith((".ome.tif", ".ome.tiff"))]
    if len(tif_paths) != 1:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: expected exactly one OME-TIFF, "
            f"got {tif_paths}"
        )
    source = OmeTiffImageSource(tif_paths[0])

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
        centers, size=CROP_PX, image_shape=(source.shape[0], source.shape[1])
    )
    print(
        f"  {ctx.feature_space}: image {source.shape} {source.dtype}, "
        f"{len(min_corners)} crop(s) of {CROP_PX}x{CROP_PX} px"
    )
    return SpatialLoaderResult(
        source=source,
        layer_mapping={"image": "raw"},
        min_corners=min_corners,
        max_corners=max_corners,
        # The schema declares two discrete_image pointers, he_crop and
        # morphology_crop, and the manifest's feature space cannot tell them
        # apart. This dataset ships no H&E, so he_crop stays null.
        field_name=MORPHOLOGY_FIELD,
    )


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


def load_gene_expression(ctx: LoaderContext) -> LoaderResult:
    h5_paths = [p for p in ctx.data_files if p.endswith(".h5")]
    if len(h5_paths) != 1:
        raise ValueError(
            f"{ctx.dataset_name}/{ctx.feature_space}: expected exactly one .h5, got {h5_paths}"
        )
    matrix = read_10x_h5_csr(h5_paths[0])
    if ctx.var_table is None:
        raise ValueError(f"{ctx.dataset_name}/{ctx.feature_space}: no feature registry table")
    if matrix.shape[1] != ctx.var_table.num_rows:
        raise ValueError(
            f"{ctx.dataset_name}: matrix has {matrix.shape[1]} feature column(s) but the "
            f"registry has {ctx.var_table.num_rows} row(s); the var table must be in matrix "
            f"column order"
        )
    print(f"  {ctx.feature_space}: {matrix.shape[0]} x {matrix.shape[1]}, {matrix.nnz} nonzero")
    return LoaderResult(
        reader=AnnDataReader(ad.AnnData(X=matrix)),
        layer_mapping={"X": "counts"},
        n_vars=matrix.shape[1],
        var_df=ctx.var_table.to_pandas(),
    )


LOADERS = {
    "gene_expression": load_gene_expression,
    "discrete_image": load_morphology,
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
    args = parser.parse_args(argv)
    collection_root = os.path.abspath(args.collection_root)
    schema_path = os.path.abspath(args.schema)
    atlas_path = os.path.abspath(args.atlas)

    report = ingest_collection(
        collection_root=collection_root,
        schema_path=schema_path,
        atlas_path=atlas_path,
        loaders=LOADERS,
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

    atlas.optimize()
    if not args.no_snapshot:
        version = atlas.snapshot()
        print(f"atlas snapshot v{version}")
    print(report)


if __name__ == "__main__":
    main()
