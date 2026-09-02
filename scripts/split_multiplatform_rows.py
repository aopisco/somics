#!/usr/bin/env python3
"""Split registry rows that name several platforms into one row per platform.

A dataset row describes one measurement of one tissue on one platform. Rows like
``Visium, Visium HD, Xenium, and MERFISH`` are really four datasets sharing a
publication, and leaving them merged breaks everything downstream: platform
counts under-report, a builder cannot be chosen, and the row cannot carry four
different download URLs.

Conservative by construction. A row is only split when **every** part is a
platform the registry already uses on its own — a 302-name vocabulary built from
the data rather than hardcoded. Anything else is listed for a human and left
alone, because the alternative is inventing platforms out of punctuation:
``LC-MS/MS`` is one technique, ``Xenium 5K + custom panel`` is one platform and a
qualifier, and ``VisiumHD / 10X Genomics`` is a platform and its vendor.

Usage rows in model_dataset_usage.csv are duplicated alongside, so a model that
used the merged row is recorded as using each split row.

Run:
    python scripts/split_multiplatform_rows.py [--apply]
"""

from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter

# Separator characters that appear inside a single platform name.
ATOMIC = [
    r"cut&?tag",
    r"rna and protein",
    r"gene and protein",
    r"h&e",
    r"\bspg\b",
    r"co-?profiling",
    r"dna-barcoded",
    r"lc-ms/ms",
    r"ms/ms",
    r"single.cell rna",
]
# Vendors and modalities are not platforms, however often they appear alone.
NOT_A_PLATFORM = {
    "10xgenomics",
    "10x",
    "nanostring",
    "vizgen",
    "akoya",
    "bruker",
    "illumina",
    "spatialtranscriptomics",
    "spatialproteomics",
    "spatialomics",
    "customPanel".lower(),
}
SEP = re.compile(r"\s*(?:,|/|\+|\band\b)\s*", re.I)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def strip_parens(text: str) -> str:
    return re.sub(r"\([^)]*\)", "", text or "")


def slug(text: str, n: int = 20) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:n]


def vocabulary(rows: list[dict]) -> set[str]:
    known = Counter()
    for r in rows:
        p = strip_parens(r.get("platform") or "").strip()
        if p and not SEP.search(p) and not any(re.search(a, p, re.I) for a in ATOMIC):
            known[norm(p)] += 1
    return {k for k in known if k not in NOT_A_PLATFORM}


def parts_of(platform: str, vocab: set[str]) -> list[str] | None:
    text = strip_parens(platform)
    if any(re.search(a, text, re.I) for a in ATOMIC):
        return None
    parts = [p.strip() for p in SEP.split(text) if p.strip()]
    if len(parts) < 2:
        return None
    return parts if all(norm(p) in vocab for p in parts) else None


def main() -> None:
    apply = "--apply" in sys.argv
    rows = list(csv.DictReader(open("data/datasets.csv")))
    cols = list(rows[0])
    usage = list(csv.DictReader(open("data/model_dataset_usage.csv")))
    ucols = list(usage[0])
    vocab = vocabulary(rows)
    taken = {r["dataset_id"] for r in rows}

    out: list[dict] = []
    new_usage: list[dict] = []
    review: list[tuple[str, str]] = []
    split_count = added = 0

    for r in rows:
        parts = parts_of(r.get("platform") or "", vocab)
        if not parts:
            text = strip_parens(r.get("platform") or "")
            pieces = [x for x in SEP.split(text) if x.strip()]
            if len(pieces) > 1 and not any(re.search(a, text, re.I) for a in ATOMIC):
                review.append((r["dataset_id"], r["platform"]))
            out.append(r)
            continue

        split_count += 1
        base = r["dataset_id"]
        # The original id keeps the first platform; the rest get their own.
        trailing = slug(parts[0])
        stem = base[: -len(trailing) - 1] if base.endswith("_" + trailing) else base
        for i, platform in enumerate(parts):
            row = dict(r)
            row["platform"] = platform
            if i == 0:
                row["dataset_id"] = base
            else:
                candidate = f"{stem}_{slug(platform)}"
                k = 2
                while candidate in taken:
                    candidate = f"{stem}_{slug(platform)}_{k}"
                    k += 1
                taken.add(candidate)
                row["dataset_id"] = candidate
                added += 1
            note = (
                f"split from a row listing {len(parts)} platforms "
                f"({r['platform']}); one row per platform"
            )
            row["notes"] = f"{r['notes']}; {note}" if r.get("notes") else note
            out.append(row)
            if i:
                for u in usage:
                    if u["dataset_id"] == base:
                        nu = dict(u)
                        nu["dataset_id"] = row["dataset_id"]
                        new_usage.append(nu)

    print(f"rows split: {split_count}   new rows: {added}   new usage rows: {len(new_usage)}")
    print(f"left for a human (a part is not a known platform): {len(review)}")
    for did, plat in review[:8]:
        print(f"   {did[:32]:<32} {plat[:52]}")
    if not apply:
        print("\ndry run — pass --apply to write")
        return
    for path, cs, data in (
        ("data/datasets.csv", cols, out),
        ("data/model_dataset_usage.csv", ucols, usage + new_usage),
    ):
        tmp = path + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cs, restval="")
            w.writeheader()
            w.writerows(data)
        os.replace(tmp, path)
    print(
        f"\nwrote data/datasets.csv ({len(out)} rows) and "
        f"data/model_dataset_usage.csv ({len(usage) + len(new_usage)} rows)"
    )


if __name__ == "__main__":
    main()
