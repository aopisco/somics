"""Second-pass resolver: turn accessions and landing pages into fetchable URLs.

`resolve_download_urls.py` handles the easy shapes (a URL that already points at
a file, a Zenodo record, a Dryad DOI). This one goes after the archives where a
listing has to be read first, which is most of what is left:

  GEO        the bulk `download/?acc=…&format=file` endpoint 404s for any series
             without a RAW bundle, so list the FTP supplementary directory
             instead and take what is actually there
  ENA/AE     BioStudies and ENA expose per-accession file listings as JSON
  PRIDE      the archive API lists files per project
  figshare   /api/articles/<id>/files
  Mendeley   /public-api/datasets/<id>/files
  10x        the dataset page embeds cf.10xgenomics.com links to the outs bundle
  GitHub     a repo alone is not data, but a release asset or a tracked file is

Every candidate is fetched before it is written, and hosts disagree about user
agents — Zenodo 403s a browser agent while Dropbox needs one — so each resolver
says which it wants.

Run:
    uv run --with boto3 python scripts/resolve_download_urls_v2.py --dry-run
    uv run --with boto3 python scripts/resolve_download_urls_v2.py --apply
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
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)
TIMEOUT = 40
# Prefer a bundle over a single file, and data over documentation.
GOOD_EXT = (
    ".tar",
    ".tar.gz",
    ".zip",
    ".h5ad",
    ".h5",
    ".rds",
    ".loom",
    ".mtx.gz",
    ".csv.gz",
    ".tsv.gz",
    ".txt.gz",
    ".gz",
    ".ome.tiff",
    ".parquet",
)


def get(url, ua=BROWSER, as_json=False):
    h = {"User-Agent": ua} if ua else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT) as r:
            body = r.read()
        return json.loads(body) if as_json else body.decode("utf-8", "replace")
    except Exception:
        return None


def head_ok(url, ua=BROWSER):
    h = {"User-Agent": ua} if ua else {}
    h["Range"] = "bytes=0-0"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT) as r:
            return r.status in (200, 206)
    except urllib.error.HTTPError as e:
        return e.code in (200, 206)
    except Exception:
        return False


def pick(cands):
    """Prefer a recognised data extension, then the longest name (usually the bundle)."""
    if not cands:
        return None
    good = [c for c in cands if c.lower().endswith(GOOD_EXT)]
    return sorted(good or cands, key=len)[-1]


def geo(acc):
    """GEO's bulk endpoint 404s without a RAW bundle; read the FTP listing instead."""
    kind = "series" if acc.upper().startswith("GSE") else "samples"
    stub = acc[:-3] + "nnn"
    base = f"https://ftp.ncbi.nlm.nih.gov/geo/{kind}/{stub}/{acc}/suppl/"
    html = get(base)
    if not html:
        return None
    files = [f for f in re.findall(r'href="([^"?/][^"]*)"', html) if not f.startswith("/")]
    f = pick(files)
    return base + urllib.parse.quote(f) if f else None


def biostudies(acc):
    """BioStudies nests files unevenly — subsections can be dicts or lists — so
    walk the whole document and collect anything that looks like a file entry."""
    meta = get(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{acc}", as_json=True)
    if not isinstance(meta, dict):
        return None
    ftp = (meta.get("ftpLink") or "").rstrip("/")
    names = []

    def walk(node):
        if isinstance(node, dict):
            if "path" in node and isinstance(node["path"], str):
                names.append(node["path"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(meta)
    f = pick(names)
    return f"{ftp}/Files/{urllib.parse.quote(f)}" if ftp and f else (ftp or None)


def pride(acc):
    js = get(
        f"https://www.ebi.ac.uk/pride/ws/archive/v2/files/byProject?accession={acc}", as_json=True
    )
    if not isinstance(js, list):
        return None
    urls = [
        loc.get("value")
        for f in js
        for loc in (f.get("publicFileLocations") or [])
        if isinstance(loc, dict) and str(loc.get("value", "")).startswith("http")
    ]
    return pick(urls)


def figshare(link):
    m = re.search(r"figshare\.com/.*?(\d{6,})", link) or re.search(r"figshare\.(\d{6,})", link)
    if not m:
        return None
    js = get(f"https://api.figshare.com/v2/articles/{m.group(1)}/files", as_json=True)
    if not isinstance(js, list):
        return None
    return pick([f["download_url"] for f in js if f.get("download_url")])


def mendeley(link):
    m = re.search(r"(?:datasets/|10\.17632/)([a-z0-9]+)", link, re.I)
    if not m:
        return None
    js = get(
        f"https://data.mendeley.com/public-api/datasets/{m.group(1)}/files?folder_id=root",
        as_json=True,
    )
    if not isinstance(js, list):
        return None
    return pick(
        [f.get("content_details", {}).get("download_url") for f in js if f.get("content_details")]
    )


def tenx(link):
    html = get(link)
    if not html:
        return None
    urls = re.findall(r'https://cf\.10xgenomics\.com/[^\s"\'<>]+', html)
    return pick([u for u in urls if not u.endswith((".png", ".jpg", ".pdf"))])


def github(link):
    m = re.search(r"github\.com/([^/]+)/([^/#?]+)", link)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).replace(".git", "")
    js = get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", as_json=True)
    if isinstance(js, dict):
        assets = [a["browser_download_url"] for a in (js.get("assets") or [])]
        if assets:
            return pick(assets)
    return None


def resolve(link):
    s = link.strip()
    if re.fullmatch(r"GS[EM]\d+", s, re.I):
        return geo(s.upper()), "geo"
    m = re.search(r"acc=(GS[EM]\d+)", s, re.I)
    if m:
        return geo(m.group(1).upper()), "geo"
    if re.fullmatch(r"(E-MTAB-\d+|E-GEOD-\d+|S-BIAD\d+)", s, re.I):
        return biostudies(s.upper()), "biostudies"
    if re.fullmatch(r"PXD\d+", s, re.I):
        return pride(s.upper()), "pride"
    if "figshare" in s:
        return figshare(s), "figshare"
    if "mendeley" in s or "10.17632" in s:
        return mendeley(s), "mendeley"
    if "10xgenomics.com" in s and "/datasets/" in s:
        return tenx(s), "10x"
    if "github.com" in s:
        return github(s), "github release"
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(REPO / "data" / "datasets.csv")))
    pool = [r for r in rows if r["data_access_link"].strip() and not r["download_url"].strip()]
    if args.limit:
        pool = pool[: args.limit]
    print(f"{len(pool)} datasets with an access link but no download URL")

    found, kinds = {}, Counter()

    def work(r):
        url, how = resolve(r["data_access_link"])
        if url and head_ok(url, ua=None if "zenodo" in url else BROWSER):
            return r["dataset_id"], url, how
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(work, pool), 1):
            if res:
                did, url, how = res
                found[did] = url
                kinds[how] += 1
                print(f"  [{how:12}] {did[:34]:36} {url[:74]}", flush=True)
            if i % 100 == 0:
                print(f"  ... {i}/{len(pool)} tried, {len(found)} resolved", flush=True)

    print(f"\nresolved {len(found)}/{len(pool)}   {dict(kinds)}")
    if args.apply and found:
        for r in rows:
            if r["dataset_id"] in found:
                r["download_url"] = found[r["dataset_id"]]
        with open(REPO / "data" / "datasets.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        total = sum(1 for r in rows if r["download_url"].strip())
        print(f"written; {total} datasets now have a download URL")


if __name__ == "__main__":
    main()
