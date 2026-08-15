"""Read-side wrapper over the spatial atlas, shaped for the viewer's zoom levels.

The viewer asks four questions, each answered by one method here: what samples exist
(`samples`), what one sample is (`sample`), where its cells are (`point_cloud`), and
what the tissue looks like there (`crops`). Gene painting (`gene_values`) is the odd
one out and is treated as a slow background job.

Everything is cached in memory because the atlas is a fixed snapshot — nothing it
returns can change while the server runs. Measured against snapshot v0 (587,115 cells,
one section) over Cloudflare R2:

    snapshot checkout                     4.2 s
    sample index scan (9 columns)         2.4 s
    per-section coordinate scan           2.5 s
    16 morphology crops in a window       2.2 s
    one gene across all cells            49.3 s   <- disk-cached, never on a hot path

Every obs read here is a full-table scan filtered in polars, never a SQL predicate on
`section_uid`: that predicate measured 21-27 s against v0 versus 2.5 s for the scan,
because the column carries no index. The trade is right at v0 scale and will need
revisiting when a scan costs minutes; `_section_index` and `_section_coords` are the
only two places that assumption lives.
"""

import base64
import io
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image

from somics.viewer.anatomy import resolve_tissue

logger = logging.getLogger(__name__)

DEFAULT_ATLAS_DIR = "s3://epiblast-public/somics_spatial_atlas"

# Read-only credentials, published deliberately in the project README.
DEFAULT_STORE_KWARGS = {
    "config": {
        "endpoint": "https://61be05560bebc4714cdd9913fb075bc9.r2.cloudflarestorage.com",
        "aws_access_key_id": "087ee61ad71e3fc431f7c8031545c4e4",
        "aws_secret_access_key": "3c94e43945c4e49a466930527f368756810315f68ad26a2c10c8adac2ed08b8d",
        "aws_region": "auto",
    }
}

# Columns the sample index needs. Kept minimal because this is a full-table scan.
_INDEX_COLUMNS = (
    "section_uid",
    "dataset_uid",
    "technology",
    "spatial_unit",
    "assay",
    "organism",
    "tissue",
    "disease",
    "x_um",
    "y_um",
    "has_gene_expression",
    "has_morphology_crop",
    "has_he_crop",
)

_COORD_COLUMNS = ("section_uid", "x_um", "y_um", "n_counts", "n_genes", "cell_area_um2")

# Decimation is deterministic so a reload shows the same cells in the same colours.
_DECIMATION_SEED = 20260815

# Intensity percentiles for stretching a raw uint16 crop into a viewable 8-bit tile.
_CROP_PERCENTILES = (1.0, 99.5)


class SampleNotFound(KeyError):
    """Raised when a section_uid is not in the atlas."""


class GeneNotFound(KeyError):
    """Raised when a gene name is not in the section's panel."""


@dataclass
class AtlasConfig:
    atlas_dir: str = DEFAULT_ATLAS_DIR
    store_kwargs: dict = field(default_factory=lambda: DEFAULT_STORE_KWARGS)
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "somics-viewer")

    @classmethod
    def from_env(cls) -> "AtlasConfig":
        """Read overrides from SOMICS_ATLAS_DIR and SOMICS_VIEWER_CACHE."""
        config = cls()
        if atlas_dir := os.environ.get("SOMICS_ATLAS_DIR"):
            config.atlas_dir = atlas_dir
            # A local path needs no object-store credentials.
            if not atlas_dir.startswith("s3://"):
                config.store_kwargs = {}
        if cache_dir := os.environ.get("SOMICS_VIEWER_CACHE"):
            config.cache_dir = Path(cache_dir)
        return config


class AtlasSource:
    """Thread-safe, memoized reader for one atlas snapshot."""

    def __init__(self, config: AtlasConfig | None = None):
        self.config = config or AtlasConfig()
        self._lock = threading.Lock()
        self._atlas = None
        self._index: pl.DataFrame | None = None
        self._samples: list[dict] | None = None
        self._coords: dict[str, pl.DataFrame] = {}
        self._cell_uids: dict[str, list[str]] = {}

    # --- atlas handle -------------------------------------------------------

    @property
    def atlas(self):
        """The checked-out snapshot, opened on first use."""
        with self._lock:
            if self._atlas is None:
                import homeobox as hox

                logger.info("opening atlas %s", self.config.atlas_dir)
                self._atlas = hox.RaggedAtlas.checkout_latest(
                    self.config.atlas_dir, store_kwargs=self.config.store_kwargs or None
                )
            return self._atlas

    def _table(self, name: str) -> pl.DataFrame:
        """A whole foreign-key table, or an empty frame when it was never created."""
        if name not in self.atlas.db.list_tables().tables:
            return pl.DataFrame()
        return self.atlas.db.open_table(name).search().to_polars()

    # --- sample index -------------------------------------------------------

    def _section_index(self) -> pl.DataFrame:
        """One row per section: cell count, modality flags, coordinate extent.

        A full scan of `_INDEX_COLUMNS`, cached for the process lifetime.
        """
        with self._lock:
            if self._index is not None:
                return self._index
        frame = self.atlas.query().select(list(_INDEX_COLUMNS)).to_polars()
        index = (
            frame.with_columns(
                pl.col("technology").cast(pl.Utf8),
                pl.col("spatial_unit").cast(pl.Utf8),
            )
            .group_by("section_uid")
            .agg(
                pl.len().alias("n_cells"),
                pl.col("dataset_uid").first(),
                pl.col("technology").first(),
                pl.col("spatial_unit").first(),
                pl.col("assay").first(),
                pl.col("organism").first(),
                pl.col("tissue").first(),
                pl.col("disease").first(),
                pl.col("x_um").min().alias("x_min_um"),
                pl.col("x_um").max().alias("x_max_um"),
                pl.col("y_um").min().alias("y_min_um"),
                pl.col("y_um").max().alias("y_max_um"),
                pl.col("has_gene_expression").any(),
                pl.col("has_morphology_crop").any(),
                pl.col("has_he_crop").any(),
            )
        )
        with self._lock:
            self._index = index
        return index

    def samples(self) -> list[dict]:
        """Every section in the atlas, joined to its donor, panel, and dataset."""
        with self._lock:
            if self._samples is not None:
                return self._samples

        index = self._section_index()
        sections = self._table("TissueSectionSchema")
        donors = self._table("DonorSchema")
        panels = self._table("PanelSchema")
        datasets = self.atlas.list_datasets()

        section_rows = _rows_by_uid(sections, "uid")
        donor_rows = _rows_by_uid(donors, "uid")
        panel_rows = _rows_by_uid(panels, "uid")

        # One dataset row per feature space; collapse to one record per dataset.
        dataset_rows: dict[str, dict] = {}
        feature_spaces: dict[str, list[str]] = {}
        for row in datasets.to_dicts():
            dataset_rows.setdefault(row["dataset_uid"], row)
            feature_spaces.setdefault(row["dataset_uid"], []).append(row["feature_space"])

        samples = []
        for row in index.sort("n_cells", descending=True).to_dicts():
            section = section_rows.get(row["section_uid"], {})
            dataset = dataset_rows.get(row["dataset_uid"], {})
            donor = donor_rows.get(section.get("donor_uid"), {})
            panel = panel_rows.get(dataset.get("panel_uid"), {})
            tissue = row["tissue"] or section.get("tissue")
            samples.append(
                {
                    "section_uid": row["section_uid"],
                    "section_id": section.get("section_id") or row["section_uid"][:8],
                    "dataset_uid": row["dataset_uid"],
                    "node_id": resolve_tissue(tissue),
                    "tissue": tissue,
                    "organism": row["organism"],
                    "species": "human" if row["organism"] == "Homo sapiens" else "rat",
                    "disease": row["disease"] or section.get("disease"),
                    "disease_state": section.get("disease_state"),
                    "preservation": section.get("preservation"),
                    "technology": row["technology"],
                    "assay": row["assay"],
                    "spatial_unit": row["spatial_unit"],
                    "n_cells": row["n_cells"],
                    "extent_um": [
                        row["x_min_um"],
                        row["y_min_um"],
                        row["x_max_um"],
                        row["y_max_um"],
                    ],
                    "has_gene_expression": bool(row["has_gene_expression"]),
                    "has_morphology_crop": bool(row["has_morphology_crop"]),
                    "has_he_crop": bool(row["has_he_crop"]),
                    "feature_spaces": sorted(feature_spaces.get(row["dataset_uid"], [])),
                    "study_name": dataset.get("study_name"),
                    "sample_name": dataset.get("sample_name"),
                    "dataset_description": dataset.get("dataset_description"),
                    "accession_database": dataset.get("accession_database"),
                    "accession_id": dataset.get("accession_id"),
                    "data_access_link": dataset.get("data_access_link"),
                    "donor": _clean(donor, drop=("uid", "description")),
                    "panel": _clean(panel, drop=("uid",)),
                }
            )

        with self._lock:
            self._samples = samples
        return samples

    def sample(self, section_uid: str) -> dict:
        """One sample record."""
        for sample in self.samples():
            if sample["section_uid"] == section_uid:
                return sample
        raise SampleNotFound(section_uid)

    # --- cell coordinates ---------------------------------------------------

    def _section_coords(self, section_uid: str) -> pl.DataFrame:
        """Every cell in one section, from a single cached scan of the whole atlas.

        Deliberately scans and partitions in one pass rather than filtering in SQL.
        A `where("section_uid = '...'")` predicate on this plain string column measured
        21-27 s against snapshot v0, versus 2.5 s for the same unfiltered scan plus a
        polars filter — LanceDB has no index on it, so the predicate costs an order of
        magnitude more than reading the column and comparing locally.

        The whole-atlas partition is the scaling ceiling here: 587k cells is 23 MB of
        float64, so v0 fits easily, but a hundred-million-cell atlas will not.
        """
        self.sample(section_uid)  # raises SampleNotFound before we hit the network
        with self._lock:
            cached = self._coords.get(section_uid)
        if cached is not None:
            return cached

        frame = self.atlas.query().select(list(_COORD_COLUMNS)).to_polars()
        partitions = {
            str(key[0]): part.drop("section_uid")
            for key, part in frame.partition_by("section_uid", as_dict=True).items()
        }
        with self._lock:
            self._coords.update(partitions)
        if section_uid not in partitions:
            raise SampleNotFound(section_uid)
        return partitions[section_uid]

    def _section_cell_uids(self, section_uid: str) -> list[str]:
        """Cell uids for one section, in the same order as `_section_coords`.

        Only gene painting needs these, and that path already costs tens of seconds, so
        the extra string-column scan is paid lazily rather than on every point request.
        """
        with self._lock:
            cached = self._cell_uids.get(section_uid)
        if cached is not None:
            return cached
        frame = self.atlas.query().select(["section_uid", "uid"]).to_polars()
        partitions = {
            str(key[0]): part["uid"].to_list()
            for key, part in frame.partition_by("section_uid", as_dict=True).items()
        }
        with self._lock:
            self._cell_uids.update(partitions)
        if section_uid not in partitions:
            raise SampleNotFound(section_uid)
        return partitions[section_uid]

    def point_cloud(self, section_uid: str, max_points: int) -> tuple[bytes, dict]:
        """Decimated cell positions as three float32 blocks: x, y, then n_counts.

        Positions are normalized to [-1, 1] on the longer axis with aspect preserved,
        so the frontend can draw without knowing micron scale; the returned metadata
        carries the micron extent for the round trip back to crop coordinates.

        Returns
        -------
        tuple[bytes, dict]
            Packed little-endian float32 payload, and metadata with `n_points`,
            `n_cells`, `extent_um`, `scale_um`, and `count_range`.
        """
        frame = self._section_coords(section_uid)
        keep = _decimation_order(frame.height, max_points)

        x_um = frame["x_um"].to_numpy()[keep]
        y_um = frame["y_um"].to_numpy()[keep]
        counts = np.nan_to_num(frame["n_counts"].to_numpy()[keep]).astype(np.float32)

        extent = [
            float(frame["x_um"].min()),
            float(frame["y_um"].min()),
            float(frame["x_um"].max()),
            float(frame["y_um"].max()),
        ]
        center_x = 0.5 * (extent[0] + extent[2])
        center_y = 0.5 * (extent[1] + extent[3])
        half_span = 0.5 * max(extent[2] - extent[0], extent[3] - extent[1]) or 1.0

        x = ((x_um - center_x) / half_span).astype(np.float32)
        y = ((y_um - center_y) / half_span).astype(np.float32)

        payload = x.tobytes() + y.tobytes() + counts.tobytes()
        meta = {
            "n_points": int(x.size),
            "n_cells": frame.height,
            "extent_um": extent,
            "scale_um": half_span,
            "count_range": [float(counts.min()), float(np.percentile(counts, 99))],
        }
        return payload, meta

    # --- imagery ------------------------------------------------------------

    def crops(
        self,
        section_uid: str,
        x_um: float,
        y_um: float,
        radius_um: float,
        limit: int,
    ) -> list[dict]:
        """Morphology crops near a point, as base64 PNGs with their micron footprints.

        Returns an empty list when the section carries no morphology imagery.
        """
        sample = self.sample(section_uid)
        if not sample["has_morphology_crop"]:
            return []

        predicate = (
            f"section_uid = '{section_uid}' AND has_morphology_crop = true "
            f"AND x_um > {x_um - radius_um} AND x_um < {x_um + radius_um} "
            f"AND y_um > {y_um - radius_um} AND y_um < {y_um + radius_um}"
        )
        batch = (
            self.atlas.query()
            .where(predicate)
            .select(["uid", "x_um", "y_um", "pixel_size_um"])
            .select_fields("morphology_crop")
            .limit(limit)
            .to_spatial_batch("morphology_crop")
        )

        # to_spatial_batch reorders rows; batch.metadata is the aligned source of truth.
        meta = batch.metadata
        tiles = []
        for i, crop in enumerate(batch.layers["raw"]):
            pixel_size = float(meta["pixel_size_um"][i])
            tiles.append(
                {
                    "uid": meta["uid"][i],
                    "x_um": float(meta["x_um"][i]),
                    "y_um": float(meta["y_um"][i]),
                    "width_um": crop.shape[-1] * pixel_size,
                    "height_um": crop.shape[-2] * pixel_size,
                    "png": _encode_crop(crop),
                }
            )
        return tiles

    # --- gene painting ------------------------------------------------------

    def genes(self, section_uid: str) -> list[str]:
        """Non-control gene names measured in this section's panel."""
        self.sample(section_uid)
        registry = self.atlas.feature_registry("gene_expression")
        genes = registry.filter(~pl.col("is_control") & pl.col("gene_name").is_not_null())
        return sorted(genes["gene_name"].unique().to_list())

    def gene_values(self, section_uid: str, gene: str, max_points: int) -> tuple[bytes, dict]:
        """Per-cell counts for one gene, aligned to `point_cloud`'s decimation.

        Reads the whole gene column (~49 s over R2 for 587k cells) the first time and
        writes it under the cache dir, so repeat requests and restarts are instant.
        """
        values = self._gene_column(section_uid, gene)
        keep = _decimation_order(values.size, max_points)
        selected = values[keep].astype(np.float32)
        return selected.tobytes(), {
            "gene": gene,
            "n_points": int(selected.size),
            "value_range": [
                float(selected.min()),
                float(np.percentile(selected, 99)) if selected.size else 0.0,
            ],
            "max_observed": float(values.max()) if values.size else 0.0,
        }

    def _gene_column(self, section_uid: str, gene: str) -> np.ndarray:
        """Counts for one gene over every cell in the section, in coordinate order."""
        cache_path = self.config.cache_dir / f"{section_uid}.{gene}.npy"
        if cache_path.exists():
            return np.load(cache_path)

        registry = self.atlas.feature_registry("gene_expression")
        matches = registry.filter(pl.col("gene_name") == gene)
        if not matches.height:
            raise GeneNotFound(gene)

        adata = (
            self.atlas.query()
            .where(f"section_uid = '{section_uid}' AND has_gene_expression = true")
            .features(matches["uid"].to_list(), "gene_expression")
            .to_anndata()
        )
        counts = np.asarray(adata.X.todense()).ravel().astype(np.float32)

        # to_anndata drops pointer-null rows and may reorder, so realign on uid.
        order = {uid: i for i, uid in enumerate(adata.obs_names)}
        cell_uids = self._section_cell_uids(section_uid)
        aligned = np.zeros(len(cell_uids), dtype=np.float32)
        for i, uid in enumerate(cell_uids):
            position = order.get(uid)
            if position is not None:
                aligned[i] = counts[position]

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, aligned)
        return aligned


def _decimation_order(n_rows: int, max_points: int) -> np.ndarray:
    """Indices of a deterministic uniform subsample, sorted so draw order is stable."""
    if n_rows <= max_points:
        return np.arange(n_rows)
    rng = np.random.default_rng(_DECIMATION_SEED)
    return np.sort(rng.choice(n_rows, size=max_points, replace=False))


def _encode_crop(crop: np.ndarray) -> str:
    """Percentile-stretch a raw crop to 8-bit and return a base64 PNG.

    Crops read back as float32 in raw uint16 intensity range; without the stretch they
    render black.
    """
    plane = np.asarray(crop)
    while plane.ndim > 2:
        plane = plane[0]
    low, high = np.percentile(plane, _CROP_PERCENTILES)
    if high <= low:
        high = low + 1.0
    scaled = np.clip((plane - low) / (high - low), 0.0, 1.0)
    image = Image.fromarray((scaled * 255).astype(np.uint8), mode="L")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _rows_by_uid(frame: pl.DataFrame, key: str) -> dict[str, dict]:
    if frame.is_empty() or key not in frame.columns:
        return {}
    return {row[key]: row for row in frame.to_dicts()}


def _clean(row: dict, drop: tuple[str, ...]) -> dict:
    """Drop join keys and null fields so the panel renders only what exists."""
    return {k: v for k, v in row.items() if k not in drop and v is not None}
