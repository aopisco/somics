"""The precomputed viewer index (scripts/build_viewer_cache.py) replaces the obs scans."""

import json

import numpy as np
import polars as pl
import pyarrow.parquet as pq
import pytest

from somics.viewer.atlas_source import AtlasConfig, AtlasSource, SampleNotFound, store_kwargs_for


class ExplodingAtlas:
    """Any attribute access means the source went to the atlas when it should not have."""

    def __getattr__(self, name):
        raise AssertionError(f"atlas touched via {name!r}")


@pytest.fixture
def indexed(tmp_path):
    (tmp_path / "coords").mkdir()
    samples = [{"section_uid": "s1", "section_id": "S1", "n_cells": 3, "has_he_crop": False, "has_morphology_crop": False}]
    (tmp_path / "samples.json").write_text(json.dumps(samples))
    frame = pl.DataFrame(
        {
            "uid": ["a", "b", "c"],
            "x_um": [0.0, 10.0, 20.0],
            "y_um": [0.0, 5.0, 10.0],
            "n_counts": [3.0, 5.0, 7.0],
            "n_genes": [1, 2, 3],
            "cell_area_um2": [40.0, 50.0, 60.0],
        }
    )
    pq.write_table(frame.to_arrow(), tmp_path / "coords" / "s1.parquet")
    src = AtlasSource(AtlasConfig(atlas_dir=str(tmp_path), store_kwargs={}, index_dir=str(tmp_path)))
    src._atlas = ExplodingAtlas()
    return src


def test_samples_come_from_the_index_without_an_atlas_scan(indexed):
    assert [s["section_uid"] for s in indexed.samples()] == ["s1"]


def test_points_come_from_the_per_section_parquet(indexed):
    payload, meta = indexed.point_cloud("s1", max_points=10_000)
    assert meta["n_cells"] == 3 and meta["n_points"] == 3
    assert meta["extent_um"] == [0.0, 0.0, 20.0, 10.0]
    x = np.frombuffer(payload[:12], dtype=np.float32)
    assert x.tolist() == [-1.0, 0.0, 1.0]


def test_cell_uids_come_from_the_same_parquet(indexed):
    assert indexed._section_cell_uids("s1") == ["a", "b", "c"]


def test_unknown_section_is_not_found(indexed):
    with pytest.raises(SampleNotFound):
        indexed.point_cloud("nope", max_points=100)


def test_store_selection(monkeypatch):
    monkeypatch.delenv("SOMICS_ATLAS_STORE", raising=False)
    assert store_kwargs_for("s3://epiblast-public/somics_spatial_atlas")["config"]["aws_region"] == "auto"
    assert store_kwargs_for("s3://somics-dev/ingest/x/atlas/y") == {"config": {"aws_region": "us-east-1"}}
    assert store_kwargs_for("/tmp/atlas") is None
    monkeypatch.setenv("SOMICS_ATLAS_STORE", "r2")
    assert "aws_access_key_id" in store_kwargs_for("s3://somics-dev/anything")["config"]
