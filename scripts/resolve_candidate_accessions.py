"""Resolve every candidate accession for the ambiguous datasets, not just one.

69 datasets each cite 2-3 accessions in their original publication, with no
reliable way to tell which belongs to which. Rather than guess — or discard the
information — fetch them all: the storage is cheap next to the cost of a wrong
mapping, and having all three lets a human decide later by looking at the data.

Accessions already covered by another dataset in the registry are skipped, so
the same deposit is never staged twice.

Emits data/candidate_accession_targets.csv: one row per (dataset, accession,
resolved URL), which stage_raw_to_s3.py can then fetch into
raw/_candidates/<accession>/.

Run:
    uv run python scripts/resolve_candidate_accessions.py
"""

from __future__ import annotations

import concurrent.futures
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from resolve_download_urls_v2 import (  # noqa: E402
    BROWSER,
    biostudies,
    figshare,
    geo,
    head_ok,
    mendeley,
    pride,
)

ACC_IN = re.compile(
    r"(GS[EM]\d{4,}|E-MTAB-\d+|PRJ[NED][A-Z]\d+|PXD\d{6}|zenodo\.org/records?/\d+"
    r"|zenodo\.\d+|dryad\.[a-z0-9]+|figshare[^\s,;]*|17632/[a-z0-9]+|CNP\d+|HRA\d{6})",
    re.I,
)


def norm(a):
    m = re.search(r"zenodo\.(?:org/records?/)?(\d+)", a, re.I)
    return f"zenodo:{m.group(1)}" if m else a.upper()


def resolve_any(acc):
    """Best fetchable URL for one accession, or None."""
    a = acc.strip()
    if re.fullmatch(r"GS[EM]\d+", a, re.I):
        return geo(a.upper())
    if re.fullmatch(r"(E-MTAB-\d+|E-GEOD-\d+|S-BIAD\d+)", a, re.I):
        return biostudies(a.upper())
    if re.fullmatch(r"PXD\d+", a, re.I):
        return pride(a.upper())
    if "figshare" in a.lower():
        return figshare(a)
    if "17632" in a or "mendeley" in a.lower():
        return mendeley(a)
    m = re.search(r"zenodo\.(?:org/records?/)?(\d+)", a, re.I)
    if m:
        return f"https://zenodo.org/api/records/{m.group(1)}/files-archive"
    m = re.search(r"dryad\.([a-z0-9]+)", a, re.I)
    if m:
        return f"https://datadryad.org/stash/dataset/doi:10.5061/dryad.{m.group(1)}"
    return None


def main():
    ds = list(csv.DictReader(open(REPO / "data" / "datasets.csv")))
    have = set()
    for r in ds:
        if r["download_url"].strip():
            blob = r["download_url"] + " " + r["data_access_link"]
            have |= {norm(x) for x in ACC_IN.findall(blob)}

    targets = []
    for r in ds:
        for a in (r.get("candidate_accessions") or "").split(";"):
            a = a.strip()
            if a and norm(a) not in have:
                targets.append((r["dataset_id"], r["dataset_name"], a))

    uniq = sorted({t[2] for t in targets})
    print(f"{len(targets)} (dataset, accession) pairs · {len(uniq)} distinct accessions to resolve")

    def work(a):
        u = resolve_any(a)
        if u and head_ok(u, ua=None if "zenodo" in u else BROWSER):
            return a, u
        return a, None

    resolved = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for a, u in ex.map(work, uniq):
            if u:
                resolved[a] = u
                print(f"  ok {a:28} {u[:72]}", flush=True)

    rows = [
        {"dataset_id": d, "dataset_name": n, "accession": a, "download_url": resolved[a]}
        for d, n, a in targets
        if a in resolved
    ]
    out = REPO / "data" / "candidate_accession_targets.csv"
    with open(out, "w", newline="") as f:
        cols = ["dataset_id", "dataset_name", "accession", "download_url"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nresolved {len(resolved)}/{len(uniq)} accessions -> {len(rows)} rows in {out.name}")
    print(f"{len(uniq) - len(resolved)} could not be resolved to a fetchable URL")


if __name__ == "__main__":
    main()
