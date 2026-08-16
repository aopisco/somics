"""Precompute one browsable page per dataset in the corpus index.

The corpus builder's "Open viewer" button used to open the 3D atlas viewer,
which needs its own build and a live atlas connection. This script replaces that
with a page per dataset that is finished before anyone clicks: every image is
already rendered, every number already reduced, and the controls only ever swap
between things on disk. Nothing at view time talks to R2, LanceDB, or a Python
process — the API just serves files.

What each page gets:

  * a section map of every spatial unit in physical coordinates, recoloured by
    unit metrics (counts, genes, area, negative-control counts), by annotation
    where the source provided it, and by the genes and proteins whose
    expression is most spatially structured;
  * a gallery of image crops sampled across the section, positioned back onto
    that map so a crop can be located in the tissue;
  * expression and protein summaries — abundance, detection, sparsity, and the
    distribution of counts and genes per unit;
  * the QC verdicts and provenance already carried by the corpus index.

Cost and fidelity. Unit-metric maps use every unit. Feature maps and feature
statistics use a subsample (`--subsample`, 60k by default) because reading one
gene across a whole Xenium section costs ~50 s against R2 while 60k units of the
*full* panel costs ~5 s. Every panel that rests on the subsample says so and
prints the count it was computed from.

Grouping. One page is built per card in the corpus index. Cards built with
`--group-by study` hold several datasets; the page then covers the largest and
lists its siblings, since sections from different captures share no coordinate
frame and cannot be drawn on one map.

Run:
    uv run python scripts/build_corpus_index.py     # first — this reads its output
    uv run python scripts/build_dataset_pages.py
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import homeobox as hox
import numpy as np
import polars as pl

from somics.pages.render import (
    CATEGORICAL_COLORS,
    encode_crop,
    lut_css_stops,
    rasterize_points,
    section_extent,
)
from somics.pages.stats import feature_summary, histogram, spatial_structure
from somics.viewer.atlas_source import DEFAULT_ATLAS_DIR, DEFAULT_STORE_KWARGS
from somics.viewer.paths import CORPUS_INDEX, DATASET_PAGES

TEMPLATE = Path(__file__).resolve().parents[1] / "src" / "somics" / "pages" / "template.html"

# Obs columns every page draws from. One scan of these covers the whole corpus.
OBS_COLUMNS = [
    "dataset_uid",
    "section_uid",
    "x_um",
    "y_um",
    "x_px",
    "y_px",
    "n_counts",
    "n_genes",
    "cell_area_um2",
    "nucleus_area_um2",
    "negative_control_counts",
    "unassigned_counts",
    "anatomical_region",
    "technology",
    "spatial_unit",
    "unit_size_um",
    "pixel_size_um",
    "has_gene_expression",
    "has_protein_abundance",
    "has_he_crop",
    "has_morphology_crop",
]

# Per-unit metrics offered as map colourings, in the order the control shows
# them. `unit` is the axis label; a metric absent from a dataset is skipped.
UNIT_METRICS = [
    ("n_counts", "Counts", "counts per unit"),
    ("n_genes", "Genes", "genes detected per unit"),
    ("cell_area_um2", "Cell area", "µm²"),
    ("nucleus_area_um2", "Nucleus area", "µm²"),
    ("negative_control_counts", "Neg. control", "negative-control counts"),
]

MODALITY_LABEL = {"he": "H&E", "immunofluorescence": "Immunofluorescence"}


def json_safe(value: Any) -> Any:
    """Replace NaN and infinities with null, recursively.

    Columns the atlas leaves unmeasured come back as NaN rather than null — a
    CODEX core has no transcript counts at all — and `json.dumps` writes bare
    `NaN`, which is not JSON. `JSON.parse` then rejects the whole payload and
    the page renders blank, so this runs before every dump and `allow_nan=False`
    keeps it from silently regressing.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    return value


def write_html(out_dir: Path, payload: dict) -> None:
    """Render the template around one page's payload.

    Kept separate from building the payload so `--html-only` can restyle every
    page from the `page.json` already on disk — the template is the part worth
    iterating on, and re-reading the atlas to change a margin is absurd.
    """
    # The payload rides inside a <script> block, so any "</" in a value — a gene
    # name, a description — would close the tag early. Escaping the slash is
    # invisible to JSON.parse.
    embedded = json.dumps(json_safe(payload), allow_nan=False).replace("</", "<\\/")
    (out_dir / "index.html").write_text(TEMPLATE.read_text().replace("__PAYLOAD__", embedded))


def write_manifest(out_root: Path, atlas_dir: str, corpus_generated_at: str | None) -> int:
    """Index every page present on disk, not just the ones this run rebuilt.

    Scanning rather than reporting is what makes `--only` safe: rebuilding a
    single dataset would otherwise leave a manifest naming only that dataset,
    and the corpus builder would grey out the other twenty viewer buttons.
    """
    pages = {}
    for page_json in sorted(out_root.glob("*/page.json")):
        payload = json.loads(page_json.read_text())
        pages[payload["id"]] = payload["slug"]
    (out_root / "manifest.json").write_text(
        json.dumps(
            {
                "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                "atlasDir": atlas_dir,
                "corpusGeneratedAt": corpus_generated_at,
                "pages": pages,
            },
            indent=2,
        )
        + "\n"
    )
    return len(pages)


def rewrite_html(out_root: Path) -> int:
    """Re-render every page's HTML from its saved payload. No atlas access."""
    count = 0
    for page_json in sorted(out_root.glob("*/page.json")):
        write_html(page_json.parent, json.loads(page_json.read_text()))
        count += 1
    return count


def slugify(value: str) -> str:
    """A filesystem- and URL-safe directory name for one card."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or "dataset"


def load_obs(atlas: hox.RaggedAtlas) -> pl.DataFrame:
    """Every obs row, with the enum columns cast to plain strings."""
    obs = atlas.query().select(OBS_COLUMNS).to_polars()
    enums = ["technology", "spatial_unit"]
    return obs.with_columns([pl.col(c).cast(pl.Utf8) for c in enums])


# Where each feature space keeps its display name, best first. Resolution is
# per feature, not per column: a protein panel names most targets by antibody
# (`target_name` = "PanCK") while only some carry a gene symbol, so picking one
# column for the whole axis would leave the rest showing registry uids.
NAME_COLUMNS = {
    "gene_expression": ("gene_name", "feature_id", "feature_key"),
    "protein_abundance": ("target_name", "protein_name", "gene_name", "protein_key"),
    "image_features": ("feature_name",),
}


def feature_names(var: Any, candidates: tuple[str, ...]) -> list[str]:
    """Display names for a feature axis, falling back to the registry uid."""
    columns = [var[name].astype("object").tolist() for name in candidates if name in var.columns]
    names = []
    for position, uid in enumerate(var.index):
        label = None
        for column in columns:
            value = column[position]
            if value is not None and str(value).strip() not in ("", "nan", "None"):
                label = str(value)
                break
        names.append(label or str(uid))
    return names


def read_features(
    atlas: hox.RaggedAtlas,
    dataset_uid: str,
    section_uid: str,
    feature_space: str,
    flag: str,
    limit: int,
) -> tuple[np.ndarray, np.ndarray, Any, list[str]] | None:
    """A subsample of one feature space, with the coordinates of each unit.

    Returns (x_um, y_um, matrix, feature_names) or None when the read yields
    nothing — a dataset can carry the presence flag while the pointer column is
    empty, and a page should degrade to "no data" rather than fail the build.
    """
    query = (
        atlas.query()
        .where(f"dataset_uid = '{dataset_uid}' AND section_uid = '{section_uid}' AND {flag} = true")
        .select(["x_um", "y_um"])
        .limit(limit)
        .select_fields(feature_space)
    )
    adata = query.to_anndata()
    if adata.n_obs == 0 or adata.n_vars == 0:
        return None

    names = feature_names(adata.var, NAME_COLUMNS.get(feature_space, ("feature_key",)))
    is_control = (
        adata.var["is_control"].to_numpy().astype(bool)
        if "is_control" in adata.var.columns
        else np.zeros(adata.n_vars, dtype=bool)
    )
    keep = ~is_control
    return {
        "x_um": adata.obs["x_um"].to_numpy().astype(float),
        "y_um": adata.obs["y_um"].to_numpy().astype(float),
        "matrix": adata.X[:, keep],
        "names": [n for n, k in zip(names, keep, strict=True) if k],
        # Registry uids, so a ranked feature can be re-read across every row.
        "uids": [str(u) for u, k in zip(adata.var.index, keep, strict=True) if k],
    }


def read_feature_columns(
    atlas: hox.RaggedAtlas,
    dataset_uid: str,
    section_uid: str,
    feature_space: str,
    flag: str,
    uids: list[str],
) -> dict | None:
    """A few named features across *every* unit of a section.

    The subsample that ranks features cannot also draw them. Rows are stored in
    spatial order, so the first N of a large section trace a lattice through it,
    and a map drawn from them shows that lattice rather than the tissue. Reading
    the handful of chosen columns over all rows costs ~30 s on the largest
    section here and produces the real thing.
    """
    adata = (
        atlas.query()
        .where(f"dataset_uid = '{dataset_uid}' AND section_uid = '{section_uid}' AND {flag} = true")
        .select(["x_um", "y_um"])
        .features(uids, feature_space)
        .to_anndata()
    )
    if adata.n_obs == 0 or adata.n_vars == 0:
        return None
    return {
        "x_um": adata.obs["x_um"].to_numpy().astype(float),
        "y_um": adata.obs["y_um"].to_numpy().astype(float),
        "matrix": adata.X,
        # `.features()` does not promise the order it was asked for.
        "column_of": {str(u): i for i, u in enumerate(adata.var.index)},
    }


def read_crops(
    atlas: hox.RaggedAtlas,
    dataset_uid: str,
    section_uid: str,
    field: str,
    flag: str,
    *,
    n_rows: int,
    positions: int = 10,
    per_position: int = 6,
) -> list[dict]:
    """Image crops sampled from across the section, with their positions.

    Rows are stored in spatial order on the imaging platforms, so the first N
    rows of a section are all one corner of it — a plain `.limit()` gallery
    covers about 1% of a Xenium slide. Reading a few rows from each of several
    offsets spread through the row range samples the whole section instead, and
    costs well under a second per extra offset.
    """
    tiles: list[dict] = []
    offsets = np.linspace(0, max(0, n_rows - per_position), positions).astype(int)
    for offset in dict.fromkeys(int(o) for o in offsets):
        batch = (
            atlas.query()
            .where(
                f"dataset_uid = '{dataset_uid}' AND section_uid = '{section_uid}' AND {flag} = true"
            )
            .select(["uid", "x_um", "y_um", "pixel_size_um", "n_counts"])
            .offset(offset)
            .limit(per_position)
            .select_fields(field)
            .to_spatial_batch(field)
        )
        tiles.extend(_crop_tiles(batch, field))
    return tiles


def _crop_tiles(batch: Any, field: str) -> list[dict]:
    meta = batch.metadata
    tiles = []
    for index, crop in enumerate(batch.layers["raw"]):
        array = np.asarray(crop)
        pixel_size = meta["pixel_size_um"][index]
        counts = meta["n_counts"][index]
        pointer = meta[field][index] if field in meta.columns else None
        tiles.append(
            {
                "array": array,
                "zarrGroup": (pointer or {}).get("zarr_group"),
                "xUm": float(meta["x_um"][index]),
                "yUm": float(meta["y_um"][index]),
                # Left as-is when unmeasured rather than coerced to 0 — a CODEX
                # core has no counts, and "0 counts" would read as a real value.
                "nCounts": None if counts is None else float(counts),
                "pixelSizeUm": float(pixel_size) if pixel_size is not None else None,
                "shape": list(array.shape),
            }
        )
    return tiles


def build_section_image(
    atlas: hox.RaggedAtlas,
    zarr_group: str,
    section: pl.DataFrame,
    out_dir: Path,
    *,
    preserve_color: bool,
    target_px: int = 1300,
) -> dict | None:
    """The whole section image, downsampled and framed to match the point map.

    The crop gallery shows ~70 µm of tissue at a time, which is the wrong scale
    for answering "what is this sample?". The stored section image answers it
    directly, but at 1.4 gigapixels for a Xenium slide it cannot be read whole —
    so this takes a strided read, which the chunked store serves in seconds.

    Framing. Obs carry both micron and pixel positions, and the section images
    in this atlas are registered to the expression frame, so the same fractional
    margin applied to the pixel bounding box selects the same physical window
    the point map draws. The window is clamped to the stored image and its true
    micron extent returned, so the page can position it against the map rather
    than assuming the two line up.
    """
    if "x_px" not in section.columns:
        return None
    x_px = section["x_px"].to_numpy().astype(float)
    y_px = section["y_px"].to_numpy().astype(float)
    x_um = section["x_um"].to_numpy().astype(float)
    y_um = section["y_um"].to_numpy().astype(float)
    if not np.isfinite(x_px).any() or not np.isfinite(y_px).any():
        return None

    array = atlas.open_zarr_group(zarr_group)["layers"]["raw"]
    height_px, width_px = array.shape[0], array.shape[1]

    def window(values: np.ndarray, limit: int) -> tuple[int, int]:
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
        pad = 0.02 * max(high - low, 1.0)
        return (
            int(max(0, np.floor(low - pad))),
            int(min(limit, np.ceil(high + pad))),
        )

    x0, x1 = window(x_px, width_px)
    y0, y1 = window(y_px, height_px)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None

    step = max(1, int(round(max(x1 - x0, y1 - y0) / target_px)))
    started = time.time()
    plane = np.asarray(array[y0:y1:step, x0:x1:step])
    elapsed = time.time() - started

    name = "map_section_image.png"
    (out_dir / name).write_bytes(encode_crop(plane, preserve_color=preserve_color))

    # px -> um is affine and both frames come from the same rows, so a least
    # squares fit recovers it exactly; the window corners then give the micron
    # extent the page frames the image with.
    (ax, bx) = np.polyfit(x_px, x_um, 1)
    (ay, by) = np.polyfit(y_px, y_um, 1)
    extent = [ax * x0 + bx, ay * y0 + by, ax * x1 + bx, ay * y1 + by]
    return {
        "file": name,
        "extent": [
            min(extent[0], extent[2]),
            min(extent[1], extent[3]),
            max(extent[0], extent[2]),
            max(extent[1], extent[3]),
        ],
        "shape": [int(plane.shape[0]), int(plane.shape[1])],
        "step": step,
        "seconds": round(elapsed, 1),
        "sourceShape": [int(height_px), int(width_px)],
    }


def spread_across(tiles: list[dict], keep: int) -> list[dict]:
    """Thin a crop list down to `keep`, spread over the section rather than clustered.

    Takes the crops in the order the scan returned them and greedily keeps the
    one furthest from everything kept so far. A gallery of neighbouring crops
    all shows the same piece of tissue; this shows the sample.
    """
    if len(tiles) <= keep:
        return tiles
    points = np.array([[t["xUm"], t["yUm"]] for t in tiles])
    chosen = [int(np.argmin(points[:, 0] + points[:, 1]))]
    distance = np.linalg.norm(points - points[chosen[0]], axis=1)
    while len(chosen) < keep:
        pick = int(np.argmax(distance))
        chosen.append(pick)
        distance = np.minimum(distance, np.linalg.norm(points - points[pick], axis=1))
    return [tiles[i] for i in sorted(chosen)]


def build_map_layers(
    obs: pl.DataFrame, out_dir: Path, extent: list[float]
) -> tuple[list[dict], dict]:
    """Unit-metric and annotation colourings of the section map."""
    x_um = obs["x_um"].to_numpy().astype(float)
    y_um = obs["y_um"].to_numpy().astype(float)
    layers: list[dict] = []
    geometry: dict | None = None

    for column, label, unit in UNIT_METRICS:
        if column not in obs.columns:
            continue
        values = obs[column].to_numpy().astype(float)
        if not np.isfinite(values).any() or np.nanmax(values) <= 0:
            continue
        png, geometry = rasterize_points(x_um, y_um, values, extent=extent)
        name = f"map_unit_{column}.png"
        (out_dir / name).write_bytes(png)
        layers.append(
            {
                "key": f"unit:{column}",
                "group": "Unit metrics",
                "label": label,
                "file": name,
                "kind": "continuous",
                "unit": unit,
                "ramp": "viridis",
                "rampStops": lut_css_stops("viridis"),
                "valueRange": geometry["valueRange"],
                "note": f"All {len(obs):,} units.",
            }
        )

    region = obs["anatomical_region"]
    if region.drop_nulls().len():
        categories = sorted(region.drop_nulls().unique().to_list())[: len(CATEGORICAL_COLORS)]
        lookup = {value: index for index, value in enumerate(categories)}
        codes = np.array([lookup.get(v, -1) for v in region.to_list()], dtype=np.int64)
        png, geometry = rasterize_points(x_um, y_um, codes, categorical=True, extent=extent)
        name = "map_annotation_region.png"
        (out_dir / name).write_bytes(png)
        annotated = int((codes >= 0).sum())
        layers.append(
            {
                "key": "annotation:anatomical_region",
                "group": "Annotation",
                "label": "Anatomical region",
                "file": name,
                "kind": "categorical",
                "categories": [
                    {"label": value, "color": "rgb({},{},{})".format(*CATEGORICAL_COLORS[index])}
                    for value, index in lookup.items()
                ],
                "note": f"{annotated:,} of {len(obs):,} units annotated.",
            }
        )

    return layers, geometry or {}


def _column(matrix: Any, index: int) -> np.ndarray:
    values = matrix[:, index]
    values = np.asarray(values.todense()).ravel() if hasattr(values, "todense") else values
    return np.asarray(values, dtype=float).ravel()


def build_feature_layers(
    read: dict,
    full: dict | None,
    out_dir: Path,
    *,
    group: str,
    prefix: str,
    ramp: str,
    top_n: int,
    n_total: int,
    extent: list[float],
) -> tuple[list[dict], dict]:
    """Map layers for the most spatially structured features, plus their summary.

    Two different reads feed this. `read` is the subsample: it ranks the
    features and produces every statistic on the page. `full` is those same
    features across every unit, and is what the maps are drawn from — see
    `read_feature_columns` for why the subsample cannot draw them.
    """
    x_um, y_um, matrix, names = read["x_um"], read["y_um"], read["matrix"], read["names"]
    summary = feature_summary(matrix, names)
    ranked = spatial_structure(matrix, names, x_um, y_um, top_n=top_n)

    csr = matrix.tocsr() if hasattr(matrix, "tocsr") else np.asarray(matrix)
    layers = []
    used: set[str] = set()
    for entry in ranked:
        drawn_from_all = False
        column = full["column_of"].get(read["uids"][entry["index"]]) if full else None
        if column is not None:
            values = _column(full["matrix"], column)
            plot_x, plot_y = full["x_um"], full["y_um"]
            drawn_from_all = True
        else:
            values = _column(csr, entry["index"])
            plot_x, plot_y = x_um, y_um

        png, geometry = rasterize_points(plot_x, plot_y, values, ramp=ramp, extent=extent)
        # Two features can share a display name; their files must not.
        stem = slugify(entry["name"])
        if stem in used:
            stem = f"{stem}-{entry['index']}"
        used.add(stem)
        name = f"map_{prefix}_{stem}.png"
        (out_dir / name).write_bytes(png)
        # A subsample that reached every unit is not a subsample.
        drawn = (
            f"All {len(plot_x):,} units."
            if drawn_from_all or len(plot_x) >= n_total
            else f"{len(plot_x):,} of {n_total:,} units."
        )
        layers.append(
            {
                "key": f"{prefix}:{entry['index']}",
                "group": group,
                "label": entry["name"],
                "file": name,
                "kind": "continuous",
                "unit": f"{entry['name']} counts per unit",
                "ramp": ramp,
                "rampStops": lut_css_stops(ramp),
                # The stretch the raster actually used, so the colourbar reads
                # in counts rather than in arbitrary units.
                "valueRange": geometry["valueRange"],
                "structureScore": entry["score"],
                "detectionPct": entry["detectionPct"],
                "note": f"{drawn} Detected in {entry['detectionPct']:.0f}% of units sampled.",
            }
        )

    summary["nUnitsTotal"] = n_total
    summary["ranked"] = ranked
    return layers, summary


def build_page(
    atlas: hox.RaggedAtlas,
    card: dict,
    obs: pl.DataFrame,
    section_images: pl.DataFrame,
    out_root: Path,
    *,
    subsample: int,
    n_crops: int,
) -> dict | None:
    """Render one dataset page. Returns its manifest entry."""
    members = card.get("sections") or []
    uids = [m["datasetUid"] for m in members] or [card["id"]]
    scoped = obs.filter(pl.col("dataset_uid").is_in(uids))
    if scoped.is_empty():
        print(f"  ! {card['title']}: no obs rows, skipped")
        return None

    # One map needs one coordinate frame, so the page covers the largest section
    # and names the rest.
    by_section = scoped.group_by("section_uid").len().sort("len", descending=True)
    section_uid = by_section["section_uid"][0]
    section = scoped.filter(pl.col("section_uid") == section_uid)
    dataset_uid = section["dataset_uid"][0]

    slug = slugify(card["id"])
    out_dir = out_root / slug
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Every point layer of a section shares this frame, so switching layers
    # never shifts the tissue under the cursor.
    extent = section_extent(
        section["x_um"].to_numpy().astype(float), section["y_um"].to_numpy().astype(float)
    )

    layers, geometry = build_map_layers(section, out_dir, extent)
    if not layers:
        print(f"  ! {card['title']}: nothing to draw, skipped")
        shutil.rmtree(out_dir)
        return None

    n_units = len(section)
    expression: dict | None = None
    protein: dict | None = None

    def full_columns(space: str, flag: str, read: dict, entries: list[dict]) -> dict | None:
        """The ranked features across every unit, when the subsample was partial."""
        if len(read["x_um"]) >= n_units or not entries:
            return None
        return read_feature_columns(
            atlas,
            dataset_uid,
            section_uid,
            space,
            flag,
            [read["uids"][e["index"]] for e in entries],
        )

    if bool(section["has_gene_expression"].any()):
        read = read_features(
            atlas, dataset_uid, section_uid, "gene_expression", "has_gene_expression", subsample
        )
        if read is not None:
            top = spatial_structure(
                read["matrix"], read["names"], read["x_um"], read["y_um"], top_n=8
            )
            gene_layers, expression = build_feature_layers(
                read,
                full_columns("gene_expression", "has_gene_expression", read, top),
                out_dir,
                group="Genes",
                prefix="gene",
                ramp="viridis",
                top_n=8,
                n_total=n_units,
                extent=extent,
            )
            layers.extend(gene_layers)

    if bool(section["has_protein_abundance"].any()):
        read = read_features(
            atlas, dataset_uid, section_uid, "protein_abundance", "has_protein_abundance", subsample
        )
        if read is not None:
            top = spatial_structure(
                read["matrix"], read["names"], read["x_um"], read["y_um"], top_n=6
            )
            protein_layers, protein = build_feature_layers(
                read,
                full_columns("protein_abundance", "has_protein_abundance", read, top),
                out_dir,
                group="Proteins",
                prefix="protein",
                ramp="magma",
                top_n=6,
                n_total=n_units,
                extent=extent,
            )
            layers.extend(protein_layers)

    crops = build_crops(atlas, dataset_uid, section_uid, section, section_images, out_dir, n_crops)

    # The tissue itself leads the map menu: it is the one layer that says what
    # the sample is before any colour scale has to be read.
    if crops and crops.get("zarrGroup"):
        image = build_section_image(
            atlas,
            crops["zarrGroup"],
            section,
            out_dir,
            preserve_color=crops["preserveColor"],
        )
        if image:
            layers.insert(
                0,
                {
                    "key": "image:section",
                    "group": "Section image",
                    "label": crops["modality"],
                    "file": image["file"],
                    "kind": "image",
                    "extent": image["extent"],
                    "note": (
                        f"{crops['modality']} of the whole section, "
                        f"{image['sourceShape'][1]:,}×{image['sourceShape'][0]:,} px "
                        f"read at 1/{image['step']}."
                    ),
                },
            )

    payload = {
        "id": card["id"],
        "slug": slug,
        "title": card["title"],
        "study": card["study"],
        "platform": card["platform"],
        "modality": card["modality"],
        "tissue": card["tissue"],
        "disease": card["disease"],
        "resolution": card["resolution"],
        "unitNoun": card["unitNoun"],
        "unitCount": card["unitCount"],
        "sectionCount": card["sectionCount"],
        "qc": card["qc"],
        "passesAllQc": card["passesAllQc"],
        "meta": card["meta"],
        "location": card.get("location"),
        "downloadUrl": card.get("downloadUrl"),
        "section": {
            "sectionUid": section_uid,
            "datasetUid": dataset_uid,
            "nUnits": n_units,
            "spatialUnit": section["spatial_unit"][0],
            "technology": section["technology"][0],
            "widthUm": geometry["extent"][2] - geometry["extent"][0],
            "heightUm": geometry["extent"][3] - geometry["extent"][1],
            "siblings": [
                {"sectionUid": row["section_uid"], "nUnits": row["len"]}
                for row in by_section.to_dicts()[1:]
            ],
        },
        "map": {
            "width": geometry["width"],
            "height": geometry["height"],
            "extent": geometry["extent"],
            "umPerPixel": geometry["umPerPixel"],
            "pointRadius": geometry["pointRadius"],
            "layers": layers,
        },
        "crops": crops,
        "expression": expression,
        "protein": protein,
        "distributions": {
            "nCounts": histogram(section["n_counts"].to_numpy().astype(float)),
            "nGenes": histogram(section["n_genes"].to_numpy().astype(float)),
            "cellArea": histogram(section["cell_area_um2"].to_numpy().astype(float)),
        },
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    payload = json_safe(payload)
    (out_dir / "page.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    write_html(out_dir, payload)

    return {
        "id": card["id"],
        "slug": slug,
        "title": card["title"],
        "nLayers": len(layers),
        "nCrops": len(crops["tiles"]) if crops else 0,
    }


def build_crops(
    atlas: hox.RaggedAtlas,
    dataset_uid: str,
    section_uid: str,
    section: pl.DataFrame,
    section_images: pl.DataFrame,
    out_dir: Path,
    n_crops: int,
) -> dict | None:
    """Render the crop gallery for whichever imagery the section carries."""
    if bool(section["has_he_crop"].any()):
        field, flag, modality = "he_crop", "has_he_crop", "he"
    elif bool(section["has_morphology_crop"].any()):
        field, flag, modality = "morphology_crop", "has_morphology_crop", "morphology"
    else:
        return None

    # Oversample so the spread pass has something to choose between.
    tiles = read_crops(
        atlas,
        dataset_uid,
        section_uid,
        field,
        flag,
        n_rows=len(section),
        positions=10,
        per_position=6,
    )
    if not tiles:
        return None
    tiles = spread_across(tiles, n_crops)

    image_row = section_images.filter(pl.col("section_uid") == section_uid)
    stored_modality = image_row["image_modality"][0] if image_row.height else None
    channels = image_row["channel_names"][0] if image_row.height else None
    preserve_color = stored_modality == "he"

    written = []
    for index, tile in enumerate(tiles):
        name = f"crop_{index:02d}.png"
        (out_dir / name).write_bytes(encode_crop(tile["array"], preserve_color=preserve_color))
        pixel_size = tile["pixelSizeUm"]
        shape = tile["shape"]
        written.append(
            {
                "file": name,
                "xUm": tile["xUm"],
                "yUm": tile["yUm"],
                "nCounts": tile["nCounts"],
                "widthUm": (shape[1] * pixel_size) if pixel_size else None,
            }
        )

    return {
        "modality": MODALITY_LABEL.get(stored_modality or "", modality.title()),
        "channelNames": list(channels) if channels is not None else None,
        "pixelSizeUm": tiles[0]["pixelSizeUm"],
        "cropShape": tiles[0]["shape"],
        "zarrGroup": tiles[0]["zarrGroup"],
        "preserveColor": preserve_color,
        "tiles": written,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default=str(DATASET_PAGES))
    parser.add_argument("--index", default=str(CORPUS_INDEX))
    parser.add_argument("--atlas", default=DEFAULT_ATLAS_DIR)
    parser.add_argument(
        "--subsample",
        type=int,
        default=60000,
        help="Units read per feature space, for feature maps and statistics.",
    )
    parser.add_argument("--crops", type=int, default=15, help="Crops per gallery.")
    parser.add_argument("--only", action="append", help="Build only these card ids or titles.")
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Re-render existing pages from their page.json, without reading the atlas.",
    )
    args = parser.parse_args()

    if args.html_only:
        out_root = Path(args.output)
        count = rewrite_html(out_root)
        existing = out_root / "manifest.json"
        previous = json.loads(existing.read_text()) if existing.is_file() else {}
        listed = write_manifest(
            out_root, previous.get("atlasDir", args.atlas), previous.get("corpusGeneratedAt")
        )
        print(f"re-rendered {count} pages from {args.output}; manifest lists {listed}")
        return

    index = json.loads(Path(args.index).read_text())
    cards = index["datasets"]
    if args.only:
        wanted = set(args.only)
        cards = [c for c in cards if c["id"] in wanted or c["title"] in wanted]
        if not cards:
            raise SystemExit(f"no cards matched {sorted(wanted)}")

    store_kwargs = DEFAULT_STORE_KWARGS if args.atlas.startswith("s3://") else None
    print(f"reading {args.atlas}")
    atlas = hox.RaggedAtlas.checkout_latest(args.atlas, store_kwargs=store_kwargs)

    started = time.time()
    obs = load_obs(atlas)
    print(f"obs scan: {len(obs):,} rows in {time.time() - started:.1f}s")

    section_images = (
        atlas.db.open_table("SectionImageSchema").search().to_polars()
        if "SectionImageSchema" in atlas.db.list_tables().tables
        else pl.DataFrame({"section_uid": [], "image_modality": [], "channel_names": []})
    )

    out_root = Path(args.output)
    out_root.mkdir(parents=True, exist_ok=True)

    entries = []
    for position, card in enumerate(cards, start=1):
        started = time.time()
        print(f"[{position}/{len(cards)}] {card['title']}")
        entry = build_page(
            atlas,
            card,
            obs,
            section_images,
            out_root,
            subsample=args.subsample,
            n_crops=args.crops,
        )
        if entry:
            entry["seconds"] = round(time.time() - started, 1)
            entries.append(entry)
            print(
                f"  -> {entry['slug']}: {entry['nLayers']} map layers, "
                f"{entry['nCrops']} crops, {entry['seconds']}s"
            )

    write_manifest(out_root, args.atlas, index.get("generatedAt"))
    print(f"\nwrote {len(entries)} pages to {out_root}")


if __name__ == "__main__":
    main()
