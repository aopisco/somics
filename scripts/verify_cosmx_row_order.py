"""Check that every CosMx obs artifact is in the row order of its DATA file.

Ingestion places emitted matrix row *i* at the obs position of
``SpatialObs_<feature_space>.uid[i]``, so if that artifact is out of order the
whole dataset is silently transposed onto the wrong cells. Nothing downstream
would catch it: the counts would still be counts and the coordinates still
coordinates, just belonging to different cells.

The check is against the source files rather than against the package's own
derived tables — the vendor's metadata CSV defines the row order, so its
``fov``/``cell_ID`` sequence is recomputed here and compared with the uid
sequence the artifact holds.

Run:
    python scripts/verify_cosmx_row_order.py
"""

from __future__ import annotations

import os
import sys

import lancedb
import pandas as pd

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/cosmx_nsclc_ffpe"
SAMPLES = [
    "Lung5_Rep1",
    "Lung5_Rep2",
    "Lung5_Rep3",
    "Lung6",
    "Lung9_Rep1",
    "Lung9_Rep2",
    "Lung12",
    "Lung13",
]
FEATURE_SPACES = ("gene_expression", "protein_abundance")


def expected_source_ids(sample: str) -> list[str]:
    meta = pd.read_csv(
        os.path.join(PACKAGE_ROOT, sample, f"{sample}_metadata_file.csv"),
        usecols=["fov", "cell_ID"],
    )
    return [f"{sample}_F{f:03d}_{c}" for f, c in zip(meta.fov, meta.cell_ID, strict=True)]


def check(sample: str) -> list[str]:
    problems: list[str] = []
    db = lancedb.connect(os.path.join(PACKAGE_ROOT, sample, "lance_db"))
    obs = db.open_table("SpatialObs").to_arrow()
    uid_to_source = dict(
        zip(obs.column("uid").to_pylist(), obs.column("source_obs_id").to_pylist(), strict=True)
    )
    expected = expected_source_ids(sample)

    for feature_space in FEATURE_SPACES:
        artifact = db.open_table(f"SpatialObs_{feature_space}").to_arrow()
        uids = artifact.column("uid").to_pylist()
        if len(uids) != len(expected):
            problems.append(
                f"{sample}/{feature_space}: artifact has {len(uids)} row(s), "
                f"the source has {len(expected)}"
            )
            continue
        actual = [uid_to_source.get(uid) for uid in uids]
        if actual != expected:
            first = next(i for i, (a, b) in enumerate(zip(actual, expected, strict=True)) if a != b)
            problems.append(
                f"{sample}/{feature_space}: row {first} is {actual[first]!r}, "
                f"the source file has {expected[first]!r}"
            )
    return problems


def main() -> None:
    problems: list[str] = []
    for sample in SAMPLES:
        found = check(sample)
        print(f"{sample}: {'ok' if not found else 'MISALIGNED'}")
        problems.extend(found)
    if problems:
        print("\nrow order does not match the DATA files:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        sys.exit(1)
    print("\nevery obs artifact is in DATA row order")


if __name__ == "__main__":
    main()
