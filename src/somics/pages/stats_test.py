"""Tests for the per-dataset feature summaries."""

import numpy as np
import scipy.sparse as sp

from somics.pages.stats import feature_summary, histogram, spatial_structure


def test_feature_summary_ranks_by_mean_and_reports_sparsity():
    # Three features, deliberately ordered: B is the most abundant.
    matrix = sp.csr_matrix(np.array([[1.0, 9.0, 0.0], [1.0, 7.0, 0.0], [0.0, 8.0, 0.0]]))
    summary = feature_summary(matrix, ["A", "B", "C"], top_n=3)

    assert [row["name"] for row in summary["top"]] == ["B", "A", "C"]
    assert summary["nUnits"] == 3
    assert summary["nFeatures"] == 3
    # 5 of the 9 entries are nonzero, so 4/9 of the matrix is zeros.
    assert summary["sparsityPct"] == 400 / 9
    assert summary["top"][0]["detectionPct"] == 100.0


def test_feature_summary_handles_an_empty_matrix():
    summary = feature_summary(sp.csr_matrix((0, 4)), ["A", "B", "C", "D"])
    assert summary["nUnits"] == 0
    assert summary["top"] == []


def test_spatial_structure_prefers_the_localized_feature():
    """A gene confined to one side must outrank an abundant uniform one."""
    rng = np.random.default_rng(0)
    n = 900
    x = rng.uniform(0, 100, n)
    y = rng.uniform(0, 100, n)

    localized = np.where(x < 30, 10.0, 0.0)
    # Far more abundant, but spread evenly across the section.
    uniform = rng.poisson(30, n).astype(float)

    matrix = sp.csr_matrix(np.stack([localized, uniform], axis=1))
    ranked = spatial_structure(matrix, ["LOCAL", "UNIFORM"], x, y, top_n=2)

    assert ranked[0]["name"] == "LOCAL"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["mean"] < ranked[1]["mean"]


def test_spatial_structure_ignores_barely_detected_features():
    rng = np.random.default_rng(1)
    n = 900
    x = rng.uniform(0, 100, n)
    y = rng.uniform(0, 100, n)
    # A single nonzero unit is perfectly "structured" and entirely noise.
    rare = np.zeros(n)
    rare[0] = 50.0
    matrix = sp.csr_matrix(rare.reshape(-1, 1))

    assert spatial_structure(matrix, ["RARE"], x, y, min_detection=0.02) == []


def test_spatial_structure_needs_enough_units():
    x = np.arange(5.0)
    assert spatial_structure(sp.csr_matrix(np.ones((5, 2))), ["A", "B"], x, x) == []


def test_histogram_clips_the_axis_but_reports_the_true_range():
    values = np.concatenate([np.arange(100.0), np.array([10_000.0])])
    hist = histogram(values, bins=10)

    assert hist["n"] == 101
    assert hist["max"] == 10_000.0
    # The outlier is clipped into the top bin rather than stretching the axis.
    assert hist["clippedAt"] < 1_000
    assert sum(hist["counts"]) == 101


def test_histogram_of_nothing_is_none():
    assert histogram(np.array([np.nan, np.nan])) is None
