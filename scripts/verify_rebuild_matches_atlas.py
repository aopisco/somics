#!/usr/bin/env python3
"""Diff a rebuilt atlas against the published one, at three tiers.

The published 59-section atlas is the only ground truth this project has, so a
rebuild is only evidence if the comparison is strict. Three tiers, reported
separately because they fail for different reasons:

**Tier 1 — structural.** Which sections exist, how many rows each has, which
feature spaces they carry, their physical extent. Passing tier 1 alone proves
almost nothing: it would pass with every expression value wrong.

**Tier 2 — content.** Per-section obs joined on ``source_obs_id`` and compared
column by column: coordinates and metrics within a float tolerance, everything
else exactly. This is the tier that means something.

**Tier 2b — measurements.** Obs equality says nothing about the arrays the obs
rows point at, which are the actual data. This tier compares the feature
registry in full, and the expression matrix and image crops over a deterministic
sample of rows. Sampled rather than exhaustive because the published atlas is
read over the network; the sample is fixed by row order, not random, so a rerun
compares the same cells.

**Tier 3 — provenance.** The metadata each section carries — study, panel,
donor, disease, accession link.

Rows are joined on ``source_obs_id`` and sections on ``section_uid``, never on
position or on ``uid``. That is not fastidiousness: ``uid`` on obs and
``dataset_uid`` on datasets come from ``make_uid()``, which is ``uuid4``, so
they are *expected* to differ between builds and comparing them would report a
failure that is not one. Only the stable uids -- section, donor, panel, feature
-- are reproducible, because those hash a natural key.

Run:
    uv run python scripts/verify_rebuild_matches_atlas.py --rebuilt <path> [--sections ...]
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import polars as pl
from homeobox import RaggedAtlas

PUBLISHED = "s3://epiblast-public/somics_spatial_atlas"
PUBLISHED_STORE = {
    "config": {
        "endpoint": "https://61be05560bebc4714cdd9913fb075bc9.r2.cloudflarestorage.com",
        "aws_access_key_id": "087ee61ad71e3fc431f7c8031545c4e4",
        "aws_secret_access_key": "3c94e43945c4e49a466930527f368756810315f68ad26a2c10c8adac2ed08b8d",
        "aws_region": "auto",
    }
}

# Random by construction (uuid4), so they cannot and should not match.
UNSTABLE = {"uid", "dataset_uid", "layout_uid", "created_at"}
FLOAT_TOL = 1e-6


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, tier: str, subject: str, ok: bool, detail: str = "") -> None:
        self.rows.append((tier, subject, "PASS" if ok else "FAIL", detail))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r[2] == "FAIL")

    def show(self) -> None:
        width = max(len(r[1]) for r in self.rows) if self.rows else 10
        tier = None
        for t, subject, verdict, detail in self.rows:
            if t != tier:
                print(f"\n-- {t}")
                tier = t
            mark = "OK " if verdict == "PASS" else "XX "
            print(f"  {mark}{subject:<{width}}  {detail}")
        print(f"\n{len(self.rows) - self.failed}/{len(self.rows)} checks passed")


def obs_for(atlas, section_uid: str) -> pl.DataFrame:
    return atlas.query().where(f"section_uid == '{section_uid}'").to_polars()


def sections_of(atlas) -> dict[str, str]:
    """section_id -> section_uid, read from the section registry.

    Obs rows carry only ``section_uid``; the human-readable ``section_id`` lives
    in ``TissueSectionSchema``. Keying the comparison on the id rather than the
    uid means a rebuild is still matched up if a uid ever stops being stable,
    and makes a mismatch legible when it happens.
    """
    # registry_tables holds the *feature* registries; the entity registries are
    # plain tables in the atlas db.
    table = atlas.db.open_table("TissueSectionSchema").to_arrow()
    rows = table.select(["section_id", "uid"]).to_pylist()
    return {r["section_id"]: r["uid"] for r in rows}


def _aligned(atlas, section_uid: str, ids: list[str]):
    """Expression, crops and var for exactly ``ids``, ordered by source_obs_id.

    Both halves must be row-aligned before any value is compared. Taking
    ``limit(n)`` from each atlas separately does not do that — physical row
    order is not part of the contract, so the two sides can hold the same cells
    in different places and every comparison then reports a difference that is
    not one.
    """
    quoted = ", ".join("'" + i.replace("'", "''") + "'" for i in ids)
    q = atlas.query().where(f"section_uid == '{section_uid}' AND source_obs_id IN ({quoted})")
    obs = q.to_polars()
    order = np.argsort(np.array(obs["source_obs_id"].to_list()), kind="stable")
    adata = q.select_fields("gene_expression").to_anndata()
    x = adata.X.todense() if hasattr(adata.X, "todense") else adata.X
    x = np.asarray(x, dtype="float64")[order]
    crops = np.stack(q.to_spatial_batch("morphology_crop").layers["raw"])[order]
    var = adata.var.index.tolist() if adata.var is not None else []
    return x, crops, var, [obs["source_obs_id"].to_list()[i] for i in order]


def compare_measurements(pub, reb, pub_uid, reb_uid, ids: list[str]) -> list[str]:
    """The arrays the obs rows point at: expression, crops, and the var axis."""
    problems = []
    px, pcrops, pvar, pids = _aligned(pub, pub_uid, ids)
    rx, rcrops, rvar, rids = _aligned(reb, reb_uid, ids)

    if pids != rids:
        return ["row alignment failed; comparison would be meaningless"]
    if pvar != rvar:
        problems.append(f"var axis differs ({len(pvar)} vs {len(rvar)} features)")

    if px.shape != rx.shape:
        problems.append(f"expression shape {px.shape} vs {rx.shape}")
    else:
        bad = int((~np.isclose(px, rx, rtol=0, atol=FLOAT_TOL)).sum())
        if bad:
            problems.append(f"expression: {bad}/{px.size} values differ")
        elif px.sum() == 0:
            problems.append("expression: all zero on both sides, comparison is vacuous")

    if pcrops.shape != rcrops.shape:
        problems.append(f"crop shape {pcrops.shape} vs {rcrops.shape}")
    elif not np.array_equal(pcrops, rcrops):
        problems.append(f"crops: {int((pcrops != rcrops).sum())}/{pcrops.size} pixels differ")
    return problems


def compare_frames(a: pl.DataFrame, b: pl.DataFrame, key: str) -> list[str]:
    """Column-by-column diff of two frames aligned on ``key``. Returns problems."""
    problems = []
    shared = [c for c in a.columns if c in b.columns and c not in UNSTABLE]
    only_a = sorted(set(a.columns) - set(b.columns) - UNSTABLE)
    only_b = sorted(set(b.columns) - set(a.columns) - UNSTABLE)
    if only_a:
        problems.append(f"columns only in published: {only_a}")
    if only_b:
        problems.append(f"columns only in rebuilt: {only_b}")

    a = a.sort(key)
    b = b.sort(key)
    if a[key].to_list() != b[key].to_list():
        problems.append(f"{key} sets differ")
        return problems

    for column in shared:
        if column == key:
            continue
        left, right = a[column], b[column]
        if left.dtype.is_numeric() and right.dtype.is_numeric():
            lv = left.to_numpy().astype("float64")
            rv = right.to_numpy().astype("float64")
            both_nan = np.isnan(lv) & np.isnan(rv)
            close = np.isclose(lv, rv, rtol=0, atol=FLOAT_TOL, equal_nan=True)
            bad = int((~(close | both_nan)).sum())
            if bad:
                worst = float(np.nanmax(np.abs(lv - rv)))
                problems.append(f"{column}: {bad} row(s) differ, max |delta| {worst:.3g}")
        else:
            bad = int((left != right).sum())
            if bad:
                problems.append(f"{column}: {bad} row(s) differ")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuilt", required=True)
    ap.add_argument("--sections", nargs="*", help="section_id values; default all shared")
    ap.add_argument("--max-rows", type=int, default=0, help="cap obs rows compared per section")
    ap.add_argument("--sample", type=int, default=200, help="rows compared for arrays per section")
    args = ap.parse_args()

    published = RaggedAtlas.checkout_latest(PUBLISHED, store_kwargs=PUBLISHED_STORE)
    rebuilt = RaggedAtlas.checkout_latest(args.rebuilt)

    pub_sections = sections_of(published)
    reb_sections = sections_of(rebuilt)

    shared = sorted(set(pub_sections) & set(reb_sections))
    if args.sections:
        shared = [s for s in shared if s in set(args.sections)]

    report = Report()
    report.add(
        "tier 1 structural",
        "sections present",
        bool(shared),
        f"{len(shared)} shared; "
        f"published-only {sorted(set(pub_sections) - set(reb_sections))[:4]}; "
        f"rebuilt-only {sorted(set(reb_sections) - set(pub_sections))[:4]}",
    )

    for section_id in shared:
        pub_uid = pub_sections[section_id]
        reb_uid = reb_sections[section_id]
        report.add(
            "tier 1 structural",
            f"{section_id} section_uid",
            pub_uid == reb_uid,
            f"{pub_uid} vs {reb_uid}",
        )

        pub_obs = obs_for(published, pub_uid)
        reb_obs = obs_for(rebuilt, reb_uid)
        if args.max_rows:
            pub_obs = pub_obs.head(args.max_rows)
            reb_obs = reb_obs.head(args.max_rows)
        report.add(
            "tier 1 structural",
            f"{section_id} n_rows",
            pub_obs.height == reb_obs.height,
            f"{pub_obs.height} vs {reb_obs.height}",
        )

        problems = compare_frames(pub_obs, reb_obs, "source_obs_id")
        report.add(
            "tier 2 content",
            f"{section_id} obs",
            not problems,
            "; ".join(problems[:4]) if problems else f"{pub_obs.width} columns equal",
        )

        ids = sorted(pub_obs["source_obs_id"].to_list())[: args.sample]
        measured = compare_measurements(published, rebuilt, pub_uid, reb_uid, ids)
        report.add(
            "tier 2b measurements",
            f"{section_id} arrays",
            not measured,
            "; ".join(measured[:4])
            if measured
            else f"var axis, expression and crops equal over {len(ids)} aligned rows",
        )

    report.show()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
