#!/usr/bin/env python3
"""Registry verdicts for the Xenium block, from ``data/tenx_xenium_files.csv``.

The Visium fold script's three moves, applied to the Xenium rows: fold the one
Space Ranger-style re-release (Breast IDC With Addon at Onboard Analysis 1.0.2
and 1.3.0) into the row carrying the newer bundle, write ``data_downloadable``
for every verified bundle, and note the rows the block does not build (the
lung preview already in the atlas under its hand-written id). CRLF preserved.

Run:
    python scripts/fold_xenium_rows.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import re

import pandas as pd

REGISTRY = "data/datasets.csv"
FILES = "data/tenx_xenium_files.csv"
FOLDED = "data/tenx_rereleased_rows.csv"
TODAY = "2026-09-07"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    d = pd.read_csv(REGISTRY, low_memory=False, dtype=str)
    files = list(csv.DictReader(open(FILES)))
    folded, verdicts, noted = [], 0, 0
    for f in files:
        if f["dataset_id"] not in set(d.dataset_id):
            continue
        idx = d.index[d.dataset_id == f["dataset_id"]][0]
        m = re.match(r"re-release of 10x sample (\S+) .*built from (\S+)", f["skip_reason"])
        if m:
            sample, keep = m.groups()
            drop = d.loc[idx]
            note = (
                f"also released as '{drop['dataset_name']}' (Xenium Onboard Analysis {f['xoa']}, "
                f"{drop['data_access_link']}); folded {TODAY}: same 10x sample {sample}"
            )
            kidx = d.index[d.dataset_id == keep][0]
            if note not in str(d.at[kidx, "notes"]):
                d.at[kidx, "notes"] = f"{d.at[kidx, 'notes']}; {note}"
            row = drop.to_dict()
            row["folded_into"] = keep
            folded.append(row)
            continue
        if f["skip_reason"]:
            tag = f"Xenium block ({TODAY}): {f['skip_reason']}"
            if tag not in str(d.at[idx, "notes"]):
                d.at[idx, "notes"] = f"{d.at[idx, 'notes']}; {tag}"
            noted += 1
            continue
        gb = int(f["outs_bytes"]) / 1e9
        d.at[idx, "data_downloadable"] = (
            f"yes (10x CDN outs bundle, {gb:.1f} GB, Onboard Analysis {f['xoa']}"
            + ("; protein co-detection, second pass" if f["protein"] == "yes" else "")
            + ")"
        )
        verdicts += 1
    d = d[~d.dataset_id.isin({r["dataset_id"] for r in folded})]
    print(f"fold {len(folded)}, {verdicts} verdicts, {noted} noted; registry -> {len(d)} rows")
    if not args.apply:
        return
    d.to_csv(REGISTRY, index=False, lineterminator="\r\n")
    if folded:
        prev = pd.read_csv(FOLDED, dtype=str)
        pd.concat([prev, pd.DataFrame(folded)], ignore_index=True).drop_duplicates(
            "dataset_id"
        ).to_csv(FOLDED, index=False)
    print("written")


if __name__ == "__main__":
    main()
