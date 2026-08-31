#!/usr/bin/env python3
"""Fold casing and vendor noise out of the platform column, keeping distinctions.

102 distinct strings in this registry contain "visium". Almost all of that is
noise -- `10x Visium`, `10X Visium`, `10× Visium`, `10 x Genomics Visium`,
`Visium platform`, `Visium Spatial Gene Expression Kit (10x Genomics)` -- over a
handful of real platforms. The column is mostly used for grouping, and that
fragmentation makes it useless for the one job it has.

What is stripped is vendor names and generic product words. What is **kept** is
anything that names a different instrument or configuration:

    Visium HD        2 um bins, not 55 um spots -- a different machine
    Visium CytAssist a different workflow
    Visium v1 / V2   different chemistry
    Visium (no probes)
    Xenium 5K        panel size, folded to Xenium by explicit decision

Conservative by construction: a rewrite only happens when the stripped form
matches a known canonical name exactly. Anything else is left alone and
reported, because a platform invented by over-eager cleanup is worse than an
ugly string.

Every rewrite records the original in `notes`. The normalised value is for
grouping; the source's own wording is evidence.

Run:
    python scripts/normalize_platform_strings.py [--apply]
"""

from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter

VENDOR = re.compile(
    r"\b(?:10\s*[x×]\s*genomics|10\s*[x×]|nanostring|vizgen|akoya|bruker|illumina)\b",
    re.I,
)
GENERIC = re.compile(
    r"\b(?:platform|assay|kits?|slides?|system|technology|instrument|"
    r"spatial\s+gene\s+expression|spatial\s+transcriptomics?)\b",
    re.I,
)
PARENS = re.compile(r"\(([^)]*)\)")

# Canonical names, keyed by their stripped-and-lowercased form. Distinctions that
# name a different instrument or chemistry get their own entry rather than being
# folded together.
CANONICAL = {
    "visium": "Visium",
    "visiumhd": "Visium HD",
    "visium hd": "Visium HD",
    "visium-hd": "Visium HD",
    "visium cytassist": "Visium CytAssist",
    "cytassist visium": "Visium CytAssist",
    "visium v1": "Visium v1",
    "visium v2": "Visium v2",
    "visium no probes": "Visium (no probes)",
    "xenium": "Xenium",
    "xenium in situ": "Xenium",
    "xenium 5k": "Xenium",
    "cosmx": "CosMx",
    "cosmx smi": "CosMx",
    "merscope": "MERSCOPE",
    "merfish": "MERFISH",
    "geomx": "GeoMx",
    "geomx dsp": "GeoMx",
    "codex": "CODEX",
    "phenocycler": "PhenoCycler",
    "phenocycler-fusion": "PhenoCycler",
    "imc": "IMC",
    "imaging mass cytometry": "IMC",
    "mibi": "MIBI",
    "mibi-tof": "MIBI",
    "cell dive": "Cell DIVE",
    "slide-seq": "Slide-seq",
    "slide-seqv2": "Slide-seqV2",
    "slideseqv2": "Slide-seqV2",
    "stereo-seq": "Stereo-seq",
    "stereoseq": "Stereo-seq",
    "seqfish": "seqFISH",
    "starmap": "STARmap",
    "dbit-seq": "DBiT-seq",
    "maldi": "MALDI",
    "maldi-msi": "MALDI",
}


def strip_noise(text: str) -> str:
    """Remove vendor names, generic product words and vendor-only parentheticals."""
    # "10×" writes the multiplication sign, which is not a word character, so a
    # \b anchored after it never matches. Fold it to "x" before anything else.
    text = (text or "").replace("\u00d7", "x")

    def keep_paren(m: re.Match) -> str:
        inner = m.group(1)
        # "(no probes)" is a configuration; "(10x Genomics)" is a vendor aside.
        return "" if VENDOR.search(inner) or not inner.strip() else f" {inner} "

    out = PARENS.sub(keep_paren, text or "")
    out = VENDOR.sub(" ", out)
    out = GENERIC.sub(" ", out)
    out = re.sub(r"[^0-9A-Za-z+&()\- ]+", " ", out)
    return re.sub(r"\s+", " ", out).strip(" -")


def canonical_for(platform: str) -> str | None:
    stripped = strip_noise(platform)
    key = re.sub(r"\s+", " ", stripped.lower()).strip()
    for candidate in (key, key.replace("-", ""), key.replace(" ", "")):
        if candidate in CANONICAL:
            return CANONICAL[candidate]
    return None


def main() -> None:
    apply = "--apply" in sys.argv
    rows = list(csv.DictReader(open("data/datasets.csv")))
    cols = list(rows[0])

    changes: list[tuple[str, str]] = []
    untouched: Counter = Counter()
    for r in rows:
        original = (r.get("platform") or "").strip()
        if not original:
            continue
        target = canonical_for(original)
        if target is None:
            untouched[original] += 1
            continue
        if target == original:
            continue
        note = f"platform recorded by the source as {original!r}; normalised for grouping"
        r["notes"] = f"{r['notes']}; {note}" if r.get("notes") else note
        r["platform"] = target
        changes.append((original, target))

    folded = Counter(changes)
    print(f"rows rewritten: {len(changes)}   distinct strings folded: {len(folded)}")
    for (was, now), n in folded.most_common(18):
        print(f"  {n:4}  {was[:52]:<52} -> {now}")
    print(
        f"\nleft alone (no canonical match): {sum(untouched.values())} rows, "
        f"{len(untouched)} distinct"
    )
    for s, n in untouched.most_common(8):
        print(f"  {n:4}  {s[:64]}")

    after = Counter(r["platform"] for r in rows if "visium" in (r.get("platform") or "").lower())
    print(f"\ndistinct 'visium' strings after: {len(after)}")
    for k, v in after.most_common():
        print(f"  {v:4}  {k}")

    if not apply:
        print("\ndry run — pass --apply to write")
        return
    tmp = "data/datasets.csv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, "data/datasets.csv")
    print(f"\nwrote data/datasets.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
