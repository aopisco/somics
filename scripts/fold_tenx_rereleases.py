#!/usr/bin/env python3
"""Fold 10x Space Ranger re-releases into one registry row per sample.

10x's catalogue lists every reprocessing of a sample under a newer Space
Ranger as its own dataset, and the harvest took that at face value: 16 Visium
and Visium HD samples have two or three rows. They are one measurement of one
tissue on one platform -- one dataset by the registry's own rule -- and the
atlas's stable ``section_uid`` refused the second copy on ingest.

This keeps the row that carries the newest release (the one
``resolve_tenx_visium_files.py`` chose to build from), appends the folded
releases to its ``notes``, moves the folded rows to
``data/tenx_rereleased_rows.csv`` with a ``folded_into`` column so nothing is
lost, and writes the block's per-row verdicts:

- ``data_downloadable`` for every buildable 10x Visium/HD row, from the
  HEAD-verified file set;
- a note on each row the spec-driven builder cannot take, and the list of
  those rows in ``data/tenx_visium_rows_needing_review.csv``.

Idempotent: a second run finds no rows to fold and rewrites the same notes.

Run:
    python scripts/fold_tenx_rereleases.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import re

import pandas as pd

REGISTRY = "data/datasets.csv"
FILES = "data/tenx_visium_files.csv"
FOLDED = "data/tenx_rereleased_rows.csv"
REVIEW = "data/tenx_visium_rows_needing_review.csv"
TODAY = "2026-09-05"


def version_of(url: str) -> str:
    m = re.search(r"/spatial-exp/([\d.]+)/", str(url))
    return m.group(1) if m else "?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    d = pd.read_csv(REGISTRY, low_memory=False, dtype=str)
    files = list(csv.DictReader(open(FILES)))
    by_id = d.set_index("dataset_id")

    # 1. fold re-releases
    folded_rows = []
    for f in files:
        m = re.match(r"re-release of 10x sample (\S+) .*ingested from (\S+)", f["skip_reason"])
        if not m or f["dataset_id"] not in by_id.index:
            continue
        sample, keep = m.group(1), m.group(2)
        drop = by_id.loc[f["dataset_id"]]
        note = (
            f"also released as '{drop['dataset_name']}' (Space Ranger "
            f"{version_of(drop['download_url'])}, {drop['data_access_link']}); folded {TODAY}: "
            f"same 10x sample {sample}"
        )
        idx = d.index[d.dataset_id == keep][0]
        if note not in str(d.at[idx, "notes"]):
            d.at[idx, "notes"] = f"{d.at[idx, 'notes']}; {note}"
        row = drop.to_dict()
        row["dataset_id"] = f["dataset_id"]
        row["folded_into"] = keep
        folded_rows.append(row)
    fold_ids = {r["dataset_id"] for r in folded_rows}
    d = d[~d.dataset_id.isin(fold_ids)].copy()

    # 2. verdicts on the buildable rows
    n_verdict = 0
    for f in files:
        if f["skip_reason"] or f["dataset_id"] not in set(d.dataset_id):
            continue
        n = sum(1 for k in ("counts_url", "spatial_url", "image_url", "binned_url") if f[k])
        gb = (
            sum(int(f[k]) for k in ("counts_bytes", "spatial_bytes", "image_bytes", "binned_bytes"))
            / 1e9
        )
        idx = d.index[d.dataset_id == f["dataset_id"]][0]
        d.at[idx, "data_downloadable"] = f"yes (10x CDN, {n} files verified, {gb:.1f} GB)"
        n_verdict += 1

    # 3. rows the builder cannot take
    review = []
    for f in files:
        if not f["skip_reason"] or f["dataset_id"] in fold_ids:
            continue
        if f["dataset_id"] not in set(d.dataset_id):
            continue
        idx = d.index[d.dataset_id == f["dataset_id"]][0]
        tag = f"not buildable by the spec-driven Visium builder ({TODAY}): {f['skip_reason']}"
        if tag not in str(d.at[idx, "notes"]):
            d.at[idx, "notes"] = f"{d.at[idx, 'notes']}; {tag}"
        r = d.loc[idx]
        review.append(
            {
                "dataset_id": r["dataset_id"],
                "platform": r["platform"],
                "species": r["species"],
                "tissue": r["tissue"],
                "data_access_link": r["data_access_link"],
                "reason": f["skip_reason"],
            }
        )

    print(
        f"fold {len(folded_rows)} re-release rows; {n_verdict} downloadable verdicts; {len(review)} rows to review"
    )
    print(f"registry {len(by_id)} -> {len(d)} rows")
    if not args.apply:
        print("dry run; pass --apply to write")
        return
    # The registry is CRLF-terminated; keep it so, or every line shows as changed.
    d.to_csv(REGISTRY, index=False, lineterminator="\r\n")
    if folded_rows:
        try:
            prev = pd.read_csv(FOLDED, dtype=str)
        except FileNotFoundError:
            prev = pd.DataFrame()
        pd.concat([prev, pd.DataFrame(folded_rows)], ignore_index=True).drop_duplicates(
            "dataset_id"
        ).to_csv(FOLDED, index=False)
    pd.DataFrame(review).to_csv(REVIEW, index=False)
    print(f"wrote {REGISTRY}, {FOLDED}, {REVIEW}")


if __name__ == "__main__":
    main()
