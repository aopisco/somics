"""Tests for the page raster helpers."""

import io

import numpy as np
import pytest
from PIL import Image

from somics.pages.render import (
    MAP_BACKGROUND,
    encode_crop,
    lut_css_stops,
    point_radius,
    rasterize_points,
    section_extent,
)


def _open(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)))


def test_rasterize_maps_extent_to_the_canvas():
    x = np.array([0.0, 100.0, 0.0, 100.0])
    y = np.array([0.0, 0.0, 50.0, 50.0])
    png, meta = rasterize_points(x, y, np.ones(4), width=200)

    # The 2% margin means the extent is wider than the points themselves, and
    # the canvas keeps the aspect of that window.
    assert meta["extent"][0] < 0 < meta["extent"][2]
    assert meta["width"] == 200
    assert meta["height"] == round(200 * (meta["extent"][3] - meta["extent"][1]) /
                                   (meta["extent"][2] - meta["extent"][0]))


def test_unpainted_pixels_keep_the_background():
    # Two far-apart points on a wide canvas leave most of it empty.
    png, _ = rasterize_points(
        np.array([0.0, 1000.0]), np.array([0.0, 1000.0]), np.array([1.0, 1.0]), width=100
    )
    image = _open(png)
    assert tuple(image[50, 50]) == MAP_BACKGROUND


def test_y_is_not_flipped():
    """Row index has to track y, or every section renders mirrored."""
    x = np.array([10.0, 10.0])
    y = np.array([0.0, 100.0])
    png, meta = rasterize_points(x, y, np.array([0.0, 1.0]), width=60, vmin=0.0, vmax=1.0)
    image = _open(png)
    top = image[: meta["height"] // 2].astype(int).sum()
    bottom = image[meta["height"] // 2 :].astype(int).sum()
    # The high value sits at large y, so the brighter half is the bottom one.
    assert bottom > top


def test_overlapping_points_keep_the_larger_value():
    # Identical coordinates: the dim point must not overwrite the bright one.
    x = np.array([5.0, 5.0, 0.0, 10.0])
    y = np.array([5.0, 5.0, 0.0, 10.0])
    bright_first, _ = rasterize_points(x, y, np.array([9.0, 0.0, 0.0, 0.0]), width=40)
    dim_first, _ = rasterize_points(x, y, np.array([0.0, 9.0, 0.0, 0.0]), width=40)
    assert bright_first == dim_first


def test_categorical_uses_distinct_colors_and_marks_unannotated():
    x = np.array([0.0, 10.0, 20.0])
    y = np.array([0.0, 0.0, 0.0])
    png, _ = rasterize_points(x, y, np.array([0, 1, -1]), width=120, categorical=True)
    image = _open(png)
    colors = {tuple(c) for c in image.reshape(-1, 3)}
    # Two category colours, the unannotated grey, and the ground.
    assert MAP_BACKGROUND in colors
    assert (72, 78, 92) in colors
    assert len(colors) >= 4


def test_point_radius_grows_as_units_thin_out():
    dense = point_radius(600_000, 800, 600)
    sparse = point_radius(4_000, 800, 600)
    assert sparse > dense
    assert point_radius(0, 800, 600) == 0


def test_lut_stops_are_css_colors():
    stops = lut_css_stops("viridis", n=5)
    assert len(stops) == 5
    assert all(s.startswith("rgb(") for s in stops)


def test_encode_crop_stretches_a_flat_looking_uint16_plane():
    # Raw detector range: without a stretch this renders as near-black.
    plane = np.linspace(600, 2400, 64 * 64).reshape(64, 64).astype(np.float32)
    image = _open(encode_crop(plane, preserve_color=False))
    assert image.ndim == 2
    assert image.min() == 0 and image.max() == 255


def test_encode_crop_keeps_hue_when_asked():
    """H&E channels stretch together; an IF stack stretches per channel."""
    crop = np.zeros((32, 32, 3), dtype=np.float32)
    crop[..., 0] = np.linspace(0, 200, 32 * 32).reshape(32, 32)
    crop[..., 1] = np.linspace(0, 100, 32 * 32).reshape(32, 32)
    crop[..., 2] = np.linspace(0, 50, 32 * 32).reshape(32, 32)

    together = _open(encode_crop(crop, preserve_color=True))
    apart = _open(encode_crop(crop, preserve_color=False))

    # Stretched together the channels stay ordered R > G > B; stretched apart
    # they each fill the range and the ordering is destroyed.
    assert together[-1, -1, 0] > together[-1, -1, 2]
    assert apart[-1, -1, 0] == apart[-1, -1, 2]


def test_explicit_extent_pins_every_layer_to_one_frame():
    """Layers drawn from different unit counts must share a frame.

    A gene read from every unit and a metric read from a subsample cover
    different point clouds; framed independently they would render at different
    scales and the tissue would jump when switching layers.
    """
    extent = [0.0, 0.0, 100.0, 50.0]
    all_units = rasterize_points(
        np.array([1.0, 99.0]), np.array([1.0, 49.0]), np.array([1.0, 2.0]),
        width=200, extent=extent,
    )[1]
    # A tighter cloud, which would otherwise be framed to its own bounds.
    subset = rasterize_points(
        np.array([40.0, 60.0]), np.array([20.0, 30.0]), np.array([1.0, 2.0]),
        width=200, extent=extent,
    )[1]

    assert all_units["extent"] == extent == subset["extent"]
    assert all_units["height"] == subset["height"]
    assert all_units["umPerPixel"] == subset["umPerPixel"]


def test_section_extent_pads_by_two_percent():
    extent = section_extent(np.array([0.0, 100.0]), np.array([0.0, 200.0]))
    assert extent == pytest.approx([-2.0, -4.0, 102.0, 204.0])
