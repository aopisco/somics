"""Attempt to recover dataset links that failed verification.

`verify_downloads.py` marks a link `no (...)` when it 404s, resolves to a bare
site root, or is free text rather than a URL. Those are three different
problems, so this tries three different things, cheapest first, and only
reports a candidate it could actually fetch:

  identifier  a free-text accession (DDBJ `DRA…`, figshare `crick.c.…`,
              PRIDE `PXD…`, GEO, ArrayExpress, a bare DOI) sent to its
              canonical resolver
  rehome      a URL whose host or path moved but whose content is known to
              live elsewhere (spatialLIBD, Dryad's URL scheme change)
  wayback     the Internet Archive's most recent snapshot, for links with no
              live equivalent

Nothing is written to the registry: this prints candidates for review, because
a recovered URL that points at the wrong data is worse than a dead one.

Run:
    uv run --with requests python scripts/recover_dead_links.py            # dead rows only
    uv run --with requests python scripts/recover_dead_links.py --all      # also 'unverified'
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Mozilla/5.0 (compatible; somics-link-recovery)"}
TIMEOUT = 25

# host/path moves we know about, applied before anything else
REHOME = [
    (
        re.compile(r"^https?://spatial\.libd\.org/spatialLIBD/?$", re.I),
        "http://research.libd.org/spatialLIBD/",
    ),
    (
        re.compile(r"^https?://datadryad\.org/dataset/(10\.5061/dryad\.\w+)$", re.I),
        r"https://datadryad.org/stash/dataset/doi:\1",
    ),
]

IDENTIFIERS = [
    (re.compile(r"^(DRA\d+)$", re.I), "https://ddbj.nig.ac.jp/resource/sra-submission/{0}"),
    (re.compile(r"^(PXD\d+)$", re.I), "https://www.ebi.ac.uk/pride/archive/projects/{0}"),
    (re.compile(r"^(GSE\d+)$", re.I), "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={0}"),
    (
        re.compile(r"^(E-MTAB-\d+)$", re.I),
        "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{0}",
    ),
    (
        re.compile(r"^(?:crick\.)?c\.(\d+)(?:\.v\d+)?$", re.I),
        "https://figshare.com/articles/dataset/{0}",
    ),
    (re.compile(r"^(?:DOI:\s*)?(10\.\d{4,}/\S+)$", re.I), "https://doi.org/{0}"),
]


def fetch_status(url):
    """Return the status of a 1-byte ranged GET, following redirects."""
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, url
    except Exception as e:
        return None, type(e).__name__


def wayback(url):
    api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers=UA), timeout=TIMEOUT) as r:
            snap = json.load(r).get("archived_snapshots", {}).get("closest")
        if snap and snap.get("available"):
            return snap["url"], snap.get("timestamp", "")[:8]
    except Exception:
        pass
    return None, None


def recover(link):
    link = link.strip()
    for pat, repl in REHOME:
        if pat.match(link):
            cand = pat.sub(repl, link)
            code, _ = fetch_status(cand)
            if code in (200, 206, 301, 302):
                return ("rehome", cand, f"http {code}")
    for pat, tmpl in IDENTIFIERS:
        m = pat.match(link)
        if m:
            cand = tmpl.format(*m.groups())
            code, final = fetch_status(cand)
            if code in (200, 206):
                return ("identifier", final, f"http {code}")
            return (None, cand, f"identifier resolved to http {code}")
    if link.lower().startswith("http"):
        snap, when = wayback(link)
        if snap:
            return ("wayback", snap, f"snapshot {when}")
    return (None, None, "no candidate")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include 'unverified' rows too")
    ap.add_argument("--sheet", default=str(REPO / "data" / "literature_datasets.csv"))
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.sheet)))
    want = ("no (",) if not args.all else ("no (", "unverified (")
    links = sorted(
        {
            r["data_access_link"].strip()
            for r in rows
            if r.get("data_downloadable", "").startswith(want) and r["data_access_link"].strip()
        }
    )
    print(f"{len(links)} unique failing links to try\n")

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for link, (how, cand, note) in zip(links, ex.map(recover, links), strict=True):
            if how:
                found.append((how, link, cand, note))
                print(f"[{how:10}] {link[:60]}\n             -> {cand[:88]}  ({note})")
    print(f"\nrecovered {len(found)}/{len(links)} ({len(links) - len(found)} still unresolved)")
    print("nothing written — review candidates before updating the registry")


if __name__ == "__main__":
    main()
