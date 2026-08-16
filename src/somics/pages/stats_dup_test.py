"""A repeated feature name must not collapse onto one column.

A whole-transcriptome panel carries the same gene symbol on more than one
Ensembl id — 44 such symbols in this atlas — so ranking has to hand back the
column it scored, not a name the caller looks up again.
"""

import numpy as np
import scipy.sparse as sp

from somics.pages.stats import spatial_structure


def test_ranked_index_points_at_the_scored_column():
    rng = np.random.default_rng(3)
    n = 900
    x = rng.uniform(0, 100, n)
    y = rng.uniform(0, 100, n)

    flat = rng.poisson(5, n).astype(float)
    localized = np.where(y > 70, 12.0, 0.0)
    # Both columns are called SAME; only the second one is structured.
    matrix = sp.csr_matrix(np.stack([flat, localized], axis=1))

    ranked = spatial_structure(matrix, ["SAME", "SAME"], x, y, top_n=1)

    assert ranked[0]["name"] == "SAME"
    assert ranked[0]["index"] == 1

    # Following the index recovers the localized column; a name lookup over the
    # duplicate list would have returned whichever one it saw last.
    column = np.asarray(matrix[:, ranked[0]["index"]].todense()).ravel()
    assert np.array_equal(column, localized)
