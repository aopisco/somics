"""Raster helpers for the precomputed dataset pages.

Everything here turns numpy arrays into PNG bytes. There is deliberately no
plotting library: the pages want a *data layer* — points on a transparent-free
dark ground, no axes, no ticks, no title — because axes, colorbars, scale bars
and legends are drawn in HTML on top, where they stay crisp at any zoom and
pick up the page's own type and colour tokens.

Two kinds of raster are produced:

`rasterize_points`  one pixel-splat per spatial unit, coloured by a continuous
                    or categorical per-unit value. This is the section map.
`encode_crop`       one image crop, contrast-stretched into 8-bit.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

# Ground colour behind the points. Near-black with a blue cast: the maps read as
# microscopy, and the page's light chrome frames them.
MAP_BACKGROUND = (11, 15, 26)

# Colour ramps as anchor stops, interpolated to 256 entries at import. These
# approximate viridis and magma from a handful of control points rather than
# carrying the full tables — visually indistinguishable at 256 steps, and it
# keeps this module dependency-free.
_RAMPS: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    "viridis": [
        (0.000, (68, 1, 84)),
        (0.125, (72, 36, 117)),
        (0.250, (65, 68, 135)),
        (0.375, (52, 97, 141)),
        (0.500, (41, 125, 142)),
        (0.625, (32, 152, 138)),
        (0.750, (68, 178, 121)),
        (0.875, (140, 204, 71)),
        (1.000, (253, 231, 37)),
    ],
    "magma": [
        (0.000, (0, 0, 4)),
        (0.250, (81, 18, 124)),
        (0.500, (183, 55, 121)),
        (0.750, (252, 137, 97)),
        (1.000, (252, 253, 191)),
    ],
}

# Categorical colours for annotation maps (cortical layers, and anything else
# that arrives as a small closed vocabulary). Chosen to stay separable on the
# dark ground and to survive the common forms of colour blindness.
CATEGORICAL_COLORS = [
    (79, 158, 255),
    (255, 176, 59),
    (94, 214, 168),
    (240, 108, 128),
    (186, 148, 255),
    (255, 231, 106),
    (108, 220, 232),
    (214, 138, 92),
    (168, 200, 120),
    (233, 130, 200),
]


def _build_lut(name: str) -> np.ndarray:
    stops = _RAMPS[name]
    positions = np.array([p for p, _ in stops])
    colors = np.array([c for _, c in stops], dtype=float)
    t = np.linspace(0.0, 1.0, 256)
    lut = np.empty((256, 3), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.interp(t, positions, colors[:, channel]).round().astype(np.uint8)
    return lut


LUTS = {name: _build_lut(name) for name in _RAMPS}


def lut_css_stops(name: str, n: int = 12) -> list[str]:
    """A handful of `rgb()` strings, for rebuilding the ramp as a CSS gradient."""
    lut = LUTS[name]
    picks = np.linspace(0, 255, n).round().astype(int)
    return [f"rgb({lut[i][0]},{lut[i][1]},{lut[i][2]})" for i in picks]


def _disc_offsets(radius: int) -> np.ndarray:
    """Pixel offsets filling a disc, as an (n, 2) array of (dy, dx)."""
    if radius <= 0:
        return np.zeros((1, 2), dtype=np.int64)
    span = np.arange(-radius, radius + 1)
    dy, dx = np.meshgrid(span, span, indexing="ij")
    inside = (dy**2 + dx**2) <= radius**2
    return np.stack([dy[inside], dx[inside]], axis=1).astype(np.int64)


def point_radius(n_points: int, width: int, height: int) -> int:
    """How fat to draw one unit so the tissue reads as tissue.

    Visium puts ~4k spots on a section and Xenium ~600k cells; drawn at the same
    size one is a scatter of specks and the other is a solid block. Scaling the
    splat to the mean spacing between units makes both look like the sample.
    """
    if n_points <= 0:
        return 0
    spacing = np.sqrt((width * height) / n_points)
    return int(np.clip(round(0.6 * spacing), 0, 7))


def section_extent(x_um: np.ndarray, y_um: np.ndarray) -> list[float]:
    """The micron window a set of points occupies, with a 2% margin."""
    x_min, x_max = float(np.min(x_um)), float(np.max(x_um))
    y_min, y_max = float(np.min(y_um)), float(np.max(y_um))
    pad_x = 0.02 * max(x_max - x_min, 1e-6)
    pad_y = 0.02 * max(y_max - y_min, 1e-6)
    return [x_min - pad_x, y_min - pad_y, x_max + pad_x, y_max + pad_y]


def _plot_geometry(
    x_um: np.ndarray, y_um: np.ndarray, width: int, extent: list[float] | None
) -> tuple[int, int, list[float], float]:
    """Pixel canvas and the micron window it covers.

    An explicit `extent` pins every layer of a section to one frame. Without it
    each layer would be framed by its own points, and a layer drawn from fewer
    units would silently sit at a different scale from the rest.
    """
    extent = list(extent) if extent is not None else section_extent(x_um, y_um)
    window_x = extent[2] - extent[0]
    window_y = extent[3] - extent[1]
    height = int(np.clip(round(width * window_y / window_x), 64, 4 * width))
    return width, height, extent, window_x / width


def rasterize_points(
    x_um: np.ndarray,
    y_um: np.ndarray,
    values: np.ndarray,
    *,
    width: int = 820,
    ramp: str = "viridis",
    categorical: bool = False,
    vmin: float | None = None,
    vmax: float | None = None,
    extent: list[float] | None = None,
) -> tuple[bytes, dict]:
    """Splat one point per unit, coloured by `values`.

    Continuous values are stretched between `vmin` and the 99th percentile, so a
    handful of very bright units cannot flatten the rest of the section into one
    colour. Overlapping splats keep the *larger* value rather than the last one
    drawn, which stops dense regions from being decided by row order.

    Categorical values are integer codes indexing `CATEGORICAL_COLORS`, with -1
    meaning unannotated (drawn as dim grey rather than dropped, so a partly
    annotated section still shows its full outline).

    Returns
    -------
    tuple[bytes, dict]
        PNG bytes, and geometry metadata: pixel size, micron `extent`
        [x0, y0, x1, y1], and the value range actually used for the stretch.
    """
    width, height, extent, um_per_px = _plot_geometry(x_um, y_um, width, extent)

    # Physical y grows downward in every image frame this atlas ingests from, so
    # row index tracks y directly and the section is not mirrored.
    px = ((x_um - extent[0]) / (extent[2] - extent[0]) * (width - 1)).astype(np.int64)
    py = ((y_um - extent[1]) / (extent[3] - extent[1]) * (height - 1)).astype(np.int64)

    radius = point_radius(x_um.size, width, height)
    offsets = _disc_offsets(radius)

    canvas = np.zeros((height, width), dtype=np.float32)
    painted = np.zeros((height, width), dtype=bool)

    if categorical:
        codes = values.astype(np.int64)
        # Draw unannotated first so annotated units win any overlap.
        order = np.argsort(codes, kind="stable")
        px, py, codes = px[order], py[order], codes[order]
        for dy, dx in offsets:
            yy, xx = py + dy, px + dx
            keep = (yy >= 0) & (yy < height) & (xx >= 0) & (xx < width)
            canvas[yy[keep], xx[keep]] = codes[keep] + 1.0
            painted[yy[keep], xx[keep]] = True
        rgb = np.zeros((height, width, 3), dtype=np.uint8)
        rgb[:] = MAP_BACKGROUND
        codes_grid = canvas.astype(np.int64) - 1
        unannotated = painted & (codes_grid < 0)
        rgb[unannotated] = (72, 78, 92)
        for index, color in enumerate(CATEGORICAL_COLORS):
            rgb[painted & (codes_grid == index)] = color
        value_range = [0.0, float(len(CATEGORICAL_COLORS))]
    else:
        finite = np.nan_to_num(values.astype(np.float32), nan=0.0)
        low = float(np.min(finite)) if vmin is None else float(vmin)
        high = float(np.percentile(finite, 99)) if vmax is None else float(vmax)
        if high <= low:
            high = low + 1.0
        for dy, dx in offsets:
            yy, xx = py + dy, px + dx
            keep = (yy >= 0) & (yy < height) & (xx >= 0) & (xx < width)
            np.maximum.at(canvas, (yy[keep], xx[keep]), finite[keep])
            painted[yy[keep], xx[keep]] = True
        scaled = np.clip((canvas - low) / (high - low), 0.0, 1.0)
        indices = (scaled * 255).astype(np.uint8)
        rgb = LUTS[ramp][indices]
        rgb[~painted] = MAP_BACKGROUND
        value_range = [low, high]

    return _png(rgb), {
        "width": width,
        "height": height,
        "extent": extent,
        "umPerPixel": um_per_px,
        "valueRange": value_range,
        "pointRadius": radius,
    }


def encode_crop(crop: np.ndarray, *, preserve_color: bool) -> bytes:
    """Contrast-stretch one raw crop into an 8-bit PNG.

    Crops come back as float32 in raw detector range — a Visium H&E crop sits
    near 0-255, a Xenium DAPI crop in the hundreds to thousands — so without a
    stretch most of them render black.

    `preserve_color` picks how a 3-channel crop is stretched. H&E is one optical
    image whose hue carries the stain, so its channels are stretched together;
    an immunofluorescence stack is three independent markers that each need
    their own range, so its channels are stretched separately.
    """
    plane = np.asarray(crop, dtype=np.float32)
    while plane.ndim > 3:
        plane = plane[0]

    if plane.ndim == 3 and plane.shape[-1] in (3, 4):
        plane = plane[..., :3]
        if preserve_color:
            plane = _stretch(plane)
        else:
            plane = np.stack([_stretch(plane[..., c]) for c in range(3)], axis=-1)
        image = Image.fromarray((plane * 255).astype(np.uint8), mode="RGB")
    else:
        while plane.ndim > 2:
            plane = plane[..., 0] if plane.shape[-1] < plane.shape[0] else plane[0]
        image = Image.fromarray((_stretch(plane) * 255).astype(np.uint8), mode="L")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _stretch(plane: np.ndarray, percentiles: tuple[float, float] = (1.0, 99.5)) -> np.ndarray:
    low, high = np.percentile(plane, percentiles)
    if high <= low:
        high = low + 1.0
    return np.clip((plane - low) / (high - low), 0.0, 1.0)


def _png(rgb: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
