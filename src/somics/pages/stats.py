"""Per-dataset summaries of a feature matrix, computed once for the page.

The pages never query the atlas, so everything a chart needs is reduced here to
a few hundred numbers: which features carry the signal, how counts are spread
across units, and how sparse the matrix is.

Both callers pass a subsample rather than the whole dataset — see
`scripts/build_dataset_pages.py` for why — so every figure returned here is an
estimate over `n_units` units, and the page labels it as one.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp


def _as_csr(matrix) -> sp.csr_matrix:
    return matrix.tocsr() if sp.issparse(matrix) else sp.csr_matrix(np.asarray(matrix))


def feature_summary(matrix, names: list[str], *, top_n: int = 20) -> dict:
    """Mean expression, detection rate, and sparsity over a feature matrix."""
    csr = _as_csr(matrix)
    n_units, n_features = csr.shape
    if n_units == 0 or n_features == 0:
        return {"nUnits": 0, "nFeatures": n_features, "top": [], "sparsityPct": None}

    totals = np.asarray(csr.sum(axis=0)).ravel()
    means = totals / n_units
    detected = np.asarray((csr > 0).sum(axis=0)).ravel()
    detection = detected / n_units

    order = np.argsort(-means)[:top_n]
    top = [
        {
            "name": names[i],
            "mean": float(means[i]),
            "detectionPct": float(100 * detection[i]),
        }
        for i in order
    ]
    nonzero = int(csr.nnz)
    return {
        "nUnits": int(n_units),
        "nFeatures": int(n_features),
        "top": top,
        "sparsityPct": float(100 * (1 - nonzero / (n_units * n_features))),
        "medianFeaturesPerUnit": float(np.median(np.asarray((csr > 0).sum(axis=1)).ravel())),
    }


def spatial_structure(
    matrix,
    names: list[str],
    x_um: np.ndarray,
    y_um: np.ndarray,
    *,
    grid: int = 24,
    min_detection: float = 0.02,
    top_n: int = 8,
) -> list[dict]:
    """Rank features by how much of their variance is spatial rather than noise.

    Bins units onto a coarse grid over the section, then scores each feature by
    the share of its total variance that sits *between* bins. A gene expressed
    everywhere at the same rate scores near zero however abundant it is; a gene
    confined to a tumour nodule or a cortical layer scores high.

    This is what decides which genes the map offers. Ranking by mean expression
    instead would offer the housekeeping genes, whose maps are flat — abundance
    is already the bar chart's job.
    """
    csr = _as_csr(matrix)
    n_units, n_features = csr.shape
    if n_units < grid or n_features == 0:
        return []

    bin_x = np.clip(((x_um - x_um.min()) / max(np.ptp(x_um), 1e-6) * grid).astype(int), 0, grid - 1)
    bin_y = np.clip(((y_um - y_um.min()) / max(np.ptp(y_um), 1e-6) * grid).astype(int), 0, grid - 1)
    bin_id = bin_y * grid + bin_x
    n_bins = grid * grid

    counts = np.bincount(bin_id, minlength=n_bins).astype(np.float64)
    occupied = counts > 0

    # Bin means as one sparse matmul: (n_bins x n_units) @ (n_units x n_features).
    indicator = sp.csr_matrix(
        (np.ones(n_units), (bin_id, np.arange(n_units))), shape=(n_bins, n_units)
    )
    bin_totals = np.asarray((indicator @ csr).todense())
    bin_means = np.zeros_like(bin_totals)
    bin_means[occupied] = bin_totals[occupied] / counts[occupied, None]

    grand_mean = np.asarray(csr.sum(axis=0)).ravel() / n_units
    weights = counts[occupied] / counts[occupied].sum()
    between = (weights[:, None] * (bin_means[occupied] - grand_mean) ** 2).sum(axis=0)

    squares = np.asarray(csr.multiply(csr).sum(axis=0)).ravel() / n_units
    total = np.maximum(squares - grand_mean**2, 1e-12)

    detection = np.asarray((csr > 0).sum(axis=0)).ravel() / n_units
    score = np.where(detection >= min_detection, between / total, 0.0)

    order = np.argsort(-score)[:top_n]
    return [
        {
            # The column index, not just the name: a whole-transcriptome panel
            # carries the same gene symbol on more than one Ensembl id, so a
            # name is not a key into the matrix.
            "index": int(i),
            "name": names[i],
            "score": float(score[i]),
            "mean": float(grand_mean[i]),
            "detectionPct": float(100 * detection[i]),
        }
        for i in order
        if score[i] > 0
    ]


def histogram(values: np.ndarray, *, bins: int = 40, clip_percentile: float = 99.0) -> dict | None:
    """Bin counts plus the summary statistics the page prints beside them."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None

    high = float(np.percentile(finite, clip_percentile))
    low = float(finite.min())
    if high <= low:
        high = low + 1.0
    counts, edges = np.histogram(np.clip(finite, low, high), bins=bins, range=(low, high))
    return {
        "counts": [int(c) for c in counts],
        "edges": [float(e) for e in edges],
        "min": float(finite.min()),
        "max": float(finite.max()),
        "median": float(np.median(finite)),
        "mean": float(finite.mean()),
        "clippedAt": high,
        "n": int(finite.size),
    }
