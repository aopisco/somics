"""Scan original publications — body *and* supplements — for data accessions.

933 curated datasets have an original publication but no direct download URL.
The accession is often present in the paper, just not where the first
extraction pass looked: in a supplementary table, a data-availability
paragraph, or a figure legend. Paperclip keeps supplements alongside the body
text, so both are searchable.

Two stages, so the expensive one is only paid once:

  resolve   DOI -> paperclip document id, cached to disk
  scan      grep the body and every supplement for accession patterns

Output is a CSV of candidates for review. Nothing is written to the registry:
an accession found somewhere in a paper is not necessarily the accession for
*this* dataset, and papers routinely cite a dozen.

Run:
    uv run python scripts/scan_publications_for_data.py --resolve   # stage 1
    uv run python scripts/scan_publications_for_data.py --scan      # stage 2
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "data" / ".doi_to_paperclip.json"
OUT = REPO / "data" / "publication_data_link_candidates.csv"

DOI_RE = re.compile(r"10\.\d{4,}/\S+")
DOC_RE = re.compile(
    r"(PMC\d+|bio_[a-f0-9]+|arx_[0-9.]+|med_[a-f0-9]+"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-1014-[0-9a-f]{4}-[0-9a-f]{12})"
)

# Accession shapes worth surfacing. Deliberately specific: a bare URL is noise,
# an accession is a claim we can act on.
ACCESSION = (
    r"(GSE\d{4,}|GSM\d{5,}|E-MTAB-\d+|E-GEOD-\d+|PRJ[NED][A-Z]\d+|SRP\d+|ERP\d+"
    r"|EGA[SD]\d+|phs\d{6}|DRA\d{6}|CNP\d+|HRA\d{6}|OEP\d+|PXD\d{6}|S-BIAD\d+"
    r"|zenodo\.org/records?/\d+|10\.5281/zenodo\.\d+|10\.5061/dryad\.[a-z0-9]+"
    r"|figshare\.com/[^\s\"')]+|10\.6084/m9\.figshare\.[\d.]+"
    r"|synapse\.org/[^\s\"')]+|singlecell\.broadinstitute\.org/[^\s\"')]+"
    r"|cellxgene\.cziscience\.com/[^\s\"')]+|data\.mendeley\.com/[^\s\"')]+"
    r"|10\.17632/[^\s\"')]+|humantumoratlas\.org/[^\s\"')]+)"
)


def sh(args, timeout=120):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def load_targets():
    """DOI -> the dataset rows that need a link."""
    out = defaultdict(list)
    for r in csv.DictReader(open(REPO / "data" / "datasets.csv")):
        if r["download_url"].strip():
            continue
        m = DOI_RE.search(r["original_publication_link"] or "")
        if m:
            out[m.group(0).rstrip("/").lower()].append(r)
    return out


def resolve(dois, cache):
    todo = [d for d in dois if d not in cache]
    print(f"{len(cache)} cached, {len(todo)} DOIs to resolve")
    for i, doi in enumerate(todo, 1):
        m = DOC_RE.search(sh(["paperclip", "lookup", "doi", doi]))
        cache[doi] = m.group(1) if m else None
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} resolved", flush=True)
            CACHE.write_text(json.dumps(cache, indent=0))
    CACHE.write_text(json.dumps(cache, indent=0))
    found = sum(1 for v in cache.values() if v)
    print(f"resolved {found}/{len(cache)} DOIs to paperclip documents")


def scan(targets, cache):
    rows = []
    docs = {d: v for d, v in cache.items() if v and d in targets}
    print(f"scanning {len(docs)} papers (body + supplements)")
    for i, (doi, doc) in enumerate(sorted(docs.items()), 1):
        base = f"/papers/{doc}"
        hits = set(
            re.findall(
                ACCESSION,
                sh(["paperclip", "grep", "-oE", ACCESSION, f"{base}/content.lines"]),
                re.I,
            )
        )
        listing = sh(["paperclip", "ls", f"{base}/supplements/"])
        supp_files = [f for f in listing.split() if f.endswith(".lines")]
        supp_hits = set()
        for f in supp_files[:6]:  # a few papers carry dozens
            supp_hits |= set(
                re.findall(
                    ACCESSION,
                    sh(["paperclip", "grep", "-oE", ACCESSION, f"{base}/supplements/{f}"]),
                    re.I,
                )
            )
        if i % 20 == 0:
            print(f"  {i}/{len(docs)} scanned", flush=True)
        for acc in sorted(hits | supp_hits):
            where = "supplement" if acc in supp_hits and acc not in hits else "body"
            for r in targets[doi]:
                rows.append(
                    {
                        "dataset_id": r["dataset_id"],
                        "dataset_name": r["dataset_name"],
                        "platform": r["platform"],
                        "tissue": r["tissue"],
                        "candidate_accession": acc,
                        "found_in": where,
                        "n_candidates_in_paper": len(hits | supp_hits),
                        "source_doi": doi,
                        "paperclip_doc": doc,
                        "current_access_link": r["data_access_link"],
                    }
                )
    cols = list(rows[0]) if rows else []
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    ds_with = len({r["dataset_id"] for r in rows})
    sole = len({r["dataset_id"] for r in rows if r["n_candidates_in_paper"] == 1})
    print(f"\n{len(rows)} candidate rows · {ds_with} datasets have at least one candidate")
    print(f"{sole} of those have exactly one candidate in their paper (highest confidence)")
    print(f"wrote {OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    targets = load_targets()
    dois = sorted(targets)
    if args.limit:
        dois = dois[: args.limit]
        targets = {d: targets[d] for d in dois}
    print(f"{sum(len(v) for v in targets.values())} datasets across {len(dois)} publications")

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if args.resolve or not args.scan:
        resolve(dois, cache)
    if args.scan:
        scan(targets, cache)


if __name__ == "__main__":
    main()
