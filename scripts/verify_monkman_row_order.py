"""Check that every monkman obs artifact is in the row order of its DATA file.

Ingestion places emitted matrix row *i* at the obs position of
``SpatialObs_<feature_space>.uid[i]``, so if that artifact is out of order the
whole dataset is silently transposed onto the wrong cells. Nothing downstream
would catch it: the intensities would still be intensities and the coordinates
still coordinates, just belonging to different cells.

The check is against the package's own DATA file rather than a derived table.
``<region>_protein_intensity.csv`` has no identifying column of its own — it is
positional by construction — so the identity of row *i* is taken from the
matching row of ``<region>_cells.csv``, the source rows the matrix was cut from,
and compared with the uid sequence the artifact holds.

Run:
    python scripts/verify_monkman_row_order.py
"""

from __future__ import annotations

import json
import os
import sys

import lancedb
import pandas as pd

PACKAGE_ROOT = "/home/ubuntu/polycomb_data_packages/monkman_nsclc_codex"
STAGING_ROOT = "/home/ubuntu/datasets/monkman_nsclc_codex/staging"
FEATURE_SPACES = ("protein_abundance",)


def regions() -> list[str]:
    with open(os.path.join(STAGING_ROOT, "region_geometry.json")) as handle:
        return [entry["region"] for entry in json.load(handle)]


def expected_source_ids(region: str) -> list[str]:
    cells = pd.read_csv(
        os.path.join(PACKAGE_ROOT, region, f"{region}_cells.csv"), usecols=["Object ID"]
    )
    intensity_rows = (
        sum(1 for _ in open(os.path.join(PACKAGE_ROOT, region, f"{region}_protein_intensity.csv")))
        - 1
    )
    if intensity_rows != len(cells):
        raise ValueError(
            f"{region}: the matrix has {intensity_rows} row(s) and the source table "
            f"{len(cells)}; they are written together and must agree"
        )
    return cells["Object ID"].tolist()


def check(region: str) -> list[str]:
    problems: list[str] = []
    db = lancedb.connect(os.path.join(PACKAGE_ROOT, region, "lance_db"))
    obs = db.open_table("SpatialObs").to_arrow()
    uid_to_source = dict(
        zip(obs.column("uid").to_pylist(), obs.column("source_obs_id").to_pylist(), strict=True)
    )
    expected = expected_source_ids(region)

    for feature_space in FEATURE_SPACES:
        artifact = db.open_table(f"SpatialObs_{feature_space}").to_arrow()
        uids = artifact.column("uid").to_pylist()
        if len(uids) != len(expected):
            problems.append(
                f"{region}/{feature_space}: artifact has {len(uids)} row(s), "
                f"the source has {len(expected)}"
            )
            continue
        actual = [uid_to_source.get(uid) for uid in uids]
        if actual != expected:
            first = next(i for i, (a, b) in enumerate(zip(actual, expected, strict=True)) if a != b)
            problems.append(
                f"{region}/{feature_space}: row {first} is {actual[first]!r}, "
                f"the source file has {expected[first]!r}"
            )
    return problems


def main() -> None:
    problems: list[str] = []
    for region in regions():
        found = check(region)
        print(f"{region}: {'ok' if not found else 'MISALIGNED'}")
        problems.extend(found)
    if problems:
        print("\nrow order does not match the DATA files:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        sys.exit(1)
    print("\nevery obs artifact is in DATA row order")


if __name__ == "__main__":
    main()
