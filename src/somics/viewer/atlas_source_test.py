"""Crop-kind selection and encoding, exercised without touching R2.

The suite has no network, so what is testable here is the choice: which pointer field
a section's imagery is read from, what each tile is labelled, and that a colour crop
survives encoding unaltered. The live atlas is verified by hand with curl.
"""

import base64
import io

import numpy as np
import polars as pl
import pytest
from PIL import Image

from somics.viewer.atlas_source import (
    AtlasSource,
    SampleNotFound,
    _crop_pixel_shape,
    _encode_crop,
    _split_limit,
)

HE_SECTION = "sec-brain-1"
MORPHOLOGY_SECTION = "sec-colon-1"
BOTH_SECTION = "sec-both-1"
BARE_SECTION = "sec-bare-1"

PIXEL_SIZE_UM = 0.5


class FakeQuery:
    """Records the one query `crops` builds, and answers it with synthetic crops."""

    def __init__(self, atlas: "FakeAtlas"):
        self.atlas = atlas
        self.call: dict = {}

    def where(self, predicate):
        self.call["predicate"] = predicate
        return self

    def select(self, columns):
        self.call["columns"] = columns
        return self

    def select_fields(self, field):
        self.call["field"] = field
        return self

    def limit(self, n):
        self.call["limit"] = n
        return self

    def to_spatial_batch(self, field):
        self.call["batch_field"] = field
        self.atlas.calls.append(self.call)
        n = min(self.call["limit"], self.atlas.n_available)
        # H&E is stored as colour, morphology as a single greyscale plane.
        if field == "he_crop":
            crops = [np.full((8, 6, 3), 128.0, dtype=np.float32) for _ in range(n)]
        else:
            crops = [np.full((8, 6), 500.0, dtype=np.float32) for _ in range(n)]
        metadata = pl.DataFrame(
            {
                "uid": [f"{field}-{i}" for i in range(n)],
                "x_um": [float(i) for i in range(n)],
                "y_um": [float(10 + i) for i in range(n)],
                "pixel_size_um": [PIXEL_SIZE_UM] * n,
            }
        )
        return FakeBatch({"raw": crops}, metadata)


class FakeBatch:
    def __init__(self, layers, metadata):
        self.layers = layers
        self.metadata = metadata


class FakeAtlas:
    def __init__(self, n_available: int = 4):
        self.calls: list[dict] = []
        self.n_available = n_available

    def query(self):
        return FakeQuery(self)


def _sample(section_uid: str, *, he: bool, morphology: bool) -> dict:
    return {
        "section_uid": section_uid,
        "has_he_crop": he,
        "has_morphology_crop": morphology,
    }


@pytest.fixture
def source():
    """An AtlasSource with its sample index and atlas handle pre-seeded."""
    src = AtlasSource()
    src._samples = [
        _sample(HE_SECTION, he=True, morphology=False),
        _sample(MORPHOLOGY_SECTION, he=False, morphology=True),
        _sample(BOTH_SECTION, he=True, morphology=True),
        _sample(BARE_SECTION, he=False, morphology=False),
    ]
    src._atlas = FakeAtlas()
    return src


# --- which field gets read ----------------------------------------------------


def test_morphology_only_section_reads_morphology(source):
    tiles = source.crops(MORPHOLOGY_SECTION, 100.0, 200.0, 50.0, 4)
    assert [call["field"] for call in source._atlas.calls] == ["morphology_crop"]
    assert {tile["kind"] for tile in tiles} == {"morphology"}


def test_he_only_section_reads_he(source):
    tiles = source.crops(HE_SECTION, 100.0, 200.0, 50.0, 4)
    assert [call["field"] for call in source._atlas.calls] == ["he_crop"]
    assert {tile["kind"] for tile in tiles} == {"he"}
    # Regression: this section used to return nothing because only morphology was read.
    assert tiles


def test_section_with_both_serves_both(source):
    tiles = source.crops(BOTH_SECTION, 100.0, 200.0, 50.0, 4)
    assert [call["field"] for call in source._atlas.calls] == ["he_crop", "morphology_crop"]
    assert {tile["kind"] for tile in tiles} == {"he", "morphology"}


def test_section_with_neither_returns_empty_without_querying(source):
    assert source.crops(BARE_SECTION, 100.0, 200.0, 50.0, 4) == []
    assert source._atlas.calls == []


def test_unknown_section_raises_before_any_read(source):
    with pytest.raises(SampleNotFound):
        source.crops("nope", 100.0, 200.0, 50.0, 4)
    assert source._atlas.calls == []


# --- what each tile says it is ------------------------------------------------


@pytest.mark.parametrize(
    ("section", "kind", "label"),
    [(HE_SECTION, "he", "H&E"), (MORPHOLOGY_SECTION, "morphology", "Morphology")],
)
def test_every_tile_carries_its_kind_and_label(source, section, kind, label):
    tiles = source.crops(section, 100.0, 200.0, 50.0, 4)
    assert tiles
    for tile in tiles:
        assert tile["kind"] == kind
        assert tile["label"] == label
        assert set(tile) == {
            "uid",
            "x_um",
            "y_um",
            "width_um",
            "height_um",
            "kind",
            "label",
            "png",
        }


def test_tile_footprint_ignores_the_colour_axis(source):
    """An (8, 6, 3) H&E tile is 6 pixels wide, not 3."""
    he = source.crops(HE_SECTION, 100.0, 200.0, 50.0, 1)[0]
    morphology = source.crops(MORPHOLOGY_SECTION, 100.0, 200.0, 50.0, 1)[0]
    for tile in (he, morphology):
        assert tile["width_um"] == pytest.approx(6 * PIXEL_SIZE_UM)
        assert tile["height_um"] == pytest.approx(8 * PIXEL_SIZE_UM)


# --- the query it builds ------------------------------------------------------


def test_predicate_filters_on_the_kind_flag_and_the_window(source):
    source.crops(HE_SECTION, 100.0, 200.0, 50.0, 4)
    predicate = source._atlas.calls[0]["predicate"]
    assert f"section_uid = '{HE_SECTION}'" in predicate
    assert "has_he_crop = true" in predicate
    assert "x_um > 50.0" in predicate and "x_um < 150.0" in predicate
    assert "y_um > 150.0" in predicate and "y_um < 250.0" in predicate


def test_limit_is_a_budget_over_all_kinds(source):
    source.crops(BOTH_SECTION, 100.0, 200.0, 50.0, 7)
    assert [call["limit"] for call in source._atlas.calls] == [4, 3]


def test_a_kind_whose_share_rounds_to_zero_is_not_queried(source):
    """A zero limit means "no limit" to LanceDB, so it must never be sent."""
    tiles = source.crops(BOTH_SECTION, 100.0, 200.0, 50.0, 1)
    assert [call["limit"] for call in source._atlas.calls] == [1]
    assert len(tiles) == 1


@pytest.mark.parametrize(
    ("limit", "n_kinds", "expected"),
    [(24, 1, [24]), (24, 2, [12, 12]), (7, 2, [4, 3]), (1, 2, [1, 0])],
)
def test_split_limit(limit, n_kinds, expected):
    assert _split_limit(limit, n_kinds) == expected


# --- encoding -----------------------------------------------------------------


def _decode(png: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(png)))


def test_colour_crop_keeps_its_colour_unaltered():
    """Constraint 2: H&E stain colour is the measurement, so it is not stretched."""
    crop = np.zeros((2, 2, 3), dtype=np.float32)
    crop[..., 0] = 10.0
    crop[..., 1] = 200.0
    crop[..., 2] = 255.0
    image = _decode(_encode_crop(crop))
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (10, 200, 255)


def test_greyscale_crop_is_stretched_into_view():
    """Raw detector counts have no display range; without the stretch they read black."""
    crop = np.linspace(8.0, 3030.0, 64, dtype=np.float32).reshape(8, 8)
    image = _decode(_encode_crop(crop))
    assert image.mode == "L"
    plane = np.asarray(image)
    assert plane.min() == 0 and plane.max() == 255


def test_colour_crop_in_uint16_range_uses_the_uint16_container():
    crop = np.full((2, 2, 3), 65_535.0, dtype=np.float32)
    assert _decode(_encode_crop(crop)).getpixel((0, 0)) == (255, 255, 255)


def test_leading_singleton_axes_are_dropped():
    crop = np.full((1, 1, 4, 5, 3), 128.0, dtype=np.float32)
    assert _decode(_encode_crop(crop)).size == (5, 4)


@pytest.mark.parametrize(
    ("shape", "expected"),
    [((8, 6), (8, 6)), ((8, 6, 3), (8, 6)), ((8, 6, 4), (8, 6)), ((2, 8, 6), (8, 6))],
)
def test_crop_pixel_shape(shape, expected):
    assert _crop_pixel_shape(np.zeros(shape, dtype=np.float32)) == expected
