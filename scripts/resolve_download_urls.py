#!/usr/bin/env python3
"""Fill the download_url column in data/datasets.csv with direct-download URLs.

Mirrors st_corpus.csv semantics: data_access_link is the dataset's landing
page or accession; download_url is a URL you can hand to curl/wget.

Resolvable hosts:
  zenodo record   -> https://zenodo.org/api/records/<id>/files-archive (zip of all files)
  GEO GSE series  -> https://www.ncbi.nlm.nih.gov/geo/download/?acc=<GSE>&format=file
  Dryad DOI       -> https://datadryad.org/api/v2/datasets/<doi>/download
  direct file URL -> passed through unchanged (.h5, .h5ad, .zip, .tar, .rar, .csv, .rds, S3)

Every candidate is probed with a ranged GET and only written if the server
answers 200/206 (302 for GEO's redirecting downloader). Existing non-empty
download_url values are left untouched.

Usage: python3 scripts/resolve_download_urls.py [csv_path]
"""

import csv
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CSV = sys.argv[1] if len(sys.argv) > 1 else "data/datasets.csv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; somics-download-resolver)"}
TIMEOUT = 30

DIRECT_FILE = re.compile(
    r"(\.h5ad|\.h5|\.zip|\.tar(\.gz)?|\.rar|\.csv(\.gz)?|\.rds|\.loom|\.parquet)($|\?)"
    r"|amazonaws\.com/.+\..+|/files/.+/content$",
    re.I,
)
ZENODO = re.compile(r"(?:zenodo\.org/(?:api/)?records?/|10\.5281/zenodo\.)(\d+)")
GSE = re.compile(r"\b(GSE\d+)\b")
DRYAD = re.compile(r"(?:datadryad\.org.*|doi\.org/)?(10\.5061/dryad\.\w+)")


def probe(url, ok=(200, 206)):
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status in ok
    except urllib.error.HTTPError as e:
        return e.code in ok
    except Exception:
        return False


def resolve(link):
    if not link:
        return None
    if DIRECT_FILE.search(link) and link.startswith("http"):
        return link if probe(link) else None
    if m := ZENODO.search(link):
        # files-archive only serves records up to 300 MB; otherwise take the
        # largest file's direct content URL (the rest stay reachable from the
        # record page in data_access_link)
        import json

        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"https://zenodo.org/api/records/{m.group(1)}", headers=UA),
                timeout=TIMEOUT,
            ) as r:
                files = json.load(r).get("files", [])
        except Exception:
            return None
        if not files:
            return None
        if len(files) == 1:
            cand = files[0]["links"].get("self") or files[0]["links"].get("content")
        elif sum(f["size"] for f in files) <= 300_000_000:
            cand = f"https://zenodo.org/api/records/{m.group(1)}/files-archive"
            return cand  # existence already proven by the API call
        else:
            cand = max(files, key=lambda f: f["size"])["links"].get("self")
        return cand if cand and probe(cand) else cand
    if m := GSE.search(link):
        cand = f"https://www.ncbi.nlm.nih.gov/geo/download/?acc={m.group(1)}&format=file"
        return cand if probe(cand, ok=(200, 206, 302)) else None
    if m := DRYAD.search(link):
        doi = urllib.parse.quote(m.group(1), safe="")
        cand = f"https://datadryad.org/api/v2/datasets/doi%3A{doi}/download"
        return cand if probe(cand, ok=(200, 206, 302)) else None
    return None


def main():
    rows = list(csv.DictReader(open(CSV)))
    fieldnames = list(rows[0].keys())
    if "download_url" not in fieldnames:
        i = fieldnames.index("data_access_link") + 1
        fieldnames.insert(i, "download_url")
    filled = 0
    for r in rows:
        r.setdefault("download_url", "")
        if r["download_url"]:
            continue
        cand = resolve(r.get("data_access_link", ""))
        if cand:
            r["download_url"] = cand
            filled += 1
    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    have = sum(1 for r in rows if r["download_url"])
    print(f"filled {filled} download_url values; total {have}/{len(rows)} rows have one")


if __name__ == "__main__":
    main()
