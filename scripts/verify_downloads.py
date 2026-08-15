#!/usr/bin/env python3
"""Verify each data_access_link in data/literature_datasets.csv and record the result.

Adds/updates a `data_downloadable` column:
  yes (<evidence>)        the linked resource exists and is fetchable
  no (<reason>)           the link is broken, non-specific, controlled-access,
                          or not a resolvable link
  unverified (<reason>)   the site blocks automated checks (e.g. 10x/Cloudflare
                          403) or answered ambiguously; verify manually
  no link                 the row has no data_access_link

Checks are per unique link and fanned out over a thread pool. Zenodo links are
verified through the records API (file count), GEO/ArrayExpress/ENA accessions
through their respective APIs, everything else with HEAD falling back to a
1-byte ranged GET.

Usage: python3 scripts/verify_downloads.py [csv_path]
"""

import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CSV = sys.argv[1] if len(sys.argv) > 1 else "data/literature_datasets.csv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; somics-link-verifier)"}
TIMEOUT = 25

ZENODO_RE = re.compile(r"(?:zenodo\.org/(?:api/)?records?/|10\.5281/zenodo\.)(\d+)")


def http_status(url, method="HEAD", headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return None, type(e).__name__


def check_zenodo(record_id):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"https://zenodo.org/api/records/{record_id}", headers=UA),
            timeout=TIMEOUT,
        ) as r:
            d = json.load(r)
        files = d.get("files", [])
        if files:
            return f"yes (zenodo record, {len(files)} files)"
        if d.get("metadata", {}).get("access_right") in ("restricted", "closed"):
            return "no (zenodo record is restricted access)"
        return "no (zenodo record has no files)"
    except urllib.error.HTTPError as e:
        return f"no (zenodo record: http {e.code})"
    except Exception as e:
        return f"no (zenodo unreachable: {type(e).__name__})"


def check_geo(acc):
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gds&term={acc}[ACCN]&retmode=json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r:
            n = int(json.load(r)["esearchresult"]["count"])
        return f"yes (GEO record {acc})" if n else f"no (GEO accession {acc} not found)"
    except Exception as e:
        return f"no (GEO lookup failed: {type(e).__name__})"


def check_url(url):
    status = err = None
    for method in ("HEAD", "GET"):
        headers = {"Range": "bytes=0-0"} if method == "GET" else {}
        for _attempt in range(2):
            status, err = http_status(url, method, headers)
            if err not in ("TimeoutError", "URLError"):
                break
            time.sleep(2)
        if status in (200, 206):
            return f"yes (http {status})"
        if status in (301, 302, 303, 307, 308):
            return "yes (redirects to live resource)"
        if status in (401, 403):
            return f"unverified (site blocks automated checks: http {status})"
        if status in (404, 410):
            return f"no (http {status})"
        if status == 202:
            return "unverified (server answered http 202)"
        # 405/501 (HEAD not allowed) or transient error: try GET
    return f"unverified (unreachable during check: {err or f'http {status}'})"


def classify(link):
    try:
        return _classify(link)
    except Exception as e:
        return f"no (check failed: {type(e).__name__})"


def _classify(link):
    link = link.strip().rstrip("|").replace("<br>", "")
    # repair PDF-mangled prefixes like "httpsnanostring.com" / "https//x.com"
    link = re.sub(r"^(https?)(:?//?)?(?=[a-z0-9])", r"\1://", link)
    m = ZENODO_RE.search(link)
    if m:
        return check_zenodo(m.group(1))
    if re.fullmatch(r"GS[EM]\d+", link):
        return check_geo(link)
    if re.fullmatch(r"E-MTAB-\d+", link):
        s, err = http_status(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{link}", "GET")
        if s == 200:
            return f"yes (ArrayExpress {link})"
        return f"no (ArrayExpress {link}: {err or f'http {s}'})"
    if re.fullmatch(r"PRJ[NED][A-Z]\d+", link):
        s, err = http_status(f"https://www.ebi.ac.uk/ena/browser/api/xml/{link}", "GET")
        return f"yes (ENA {link})" if s == 200 else f"no (ENA {link}: {err or f'http {s}'})"
    if re.fullmatch(r"EGAS\d+", link) or re.fullmatch(r"phs\d+(\.v\d+)*", link):
        return "no (controlled access archive: application required)"
    if re.fullmatch(r"SCP\d+", link):
        return check_url(f"https://singlecell.broadinstitute.org/single_cell/study/{link}")
    if re.fullmatch(r"PXD\d+", link) or (m := re.search(r"\b(PXD\d+)\b", link)):
        pxd = link if link.startswith("PXD") else m.group(1)
        s, err = http_status(f"https://www.ebi.ac.uk/pride/ws/archive/v2/projects/{pxd}", "GET")
        return f"yes (PRIDE {pxd})" if s == 200 else f"no (PRIDE {pxd}: {err or f'http {s}'})"
    if m := re.search(r"\b(S-(?:BIAD|EPMC)\w+)\b", link):
        s, err = http_status(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{m.group(1)}", "GET")
        if s == 200:
            return f"yes (BioStudies {m.group(1)})"
        return f"no (BioStudies {m.group(1)}: {err or f'http {s}'})"
    if re.fullmatch(r"HRA\d+", link):
        return check_url(f"https://ngdc.cncb.ac.cn/gsa-human/browse/{link}")
    if re.fullmatch(r"CNP\d+", link):
        return check_url(f"https://db.cngb.org/search/project/{link}")
    if re.fullmatch(r"10\.\d{4,}/\S+", link):
        return check_url(f"https://doi.org/{link}")
    if m := re.match(r"GEO:\s*(GS[EM]\d+)", link):
        return check_geo(m.group(1))
    # comma-separated accession lists: verify the first one
    if "," in link and (m := re.match(r"\s*(GS[EM]\d+|PRJ[NED][A-Z]\d+|E-MTAB-\d+)", link)):
        return _classify(m.group(1)).replace(")", ", first of several listed)", 1)
    if link.lower() in ("null", "na", "none", ""):
        return "no link"
    if link.startswith("http"):
        path = re.sub(r"^https?://[^/]+", "", link).strip("/")
        if not path:
            return "no (link is a site root, not a dataset)"
        return check_url(link)
    if link.startswith("www."):
        return check_url(f"https://{link}")
    return "no (free-text description, not a resolvable link)"


def main():
    rows = list(csv.DictReader(open(CSV)))
    links = sorted({r["data_access_link"] for r in rows if r["data_access_link"]})
    print(f"{len(rows)} rows, {len(links)} unique links to verify")

    results = {}
    geo_links = [lk for lk in links if re.fullmatch(r"GS[EM]\d+", lk)]
    other_links = [lk for lk in links if lk not in geo_links]

    # NCBI eutils: max ~3 req/s without an API key, so GEO runs sequentially
    for lk in geo_links:
        results[lk] = classify(lk)
        time.sleep(0.35)

    with ThreadPoolExecutor(max_workers=12) as ex:
        for lk, res in zip(other_links, ex.map(classify, other_links), strict=True):
            results[lk] = res

    for r in rows:
        link = r["data_access_link"]
        r["data_downloadable"] = results[link] if link else "no link"

    fieldnames = list(rows[0].keys())
    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    tally = {}
    for r in rows:
        key = r["data_downloadable"].split(" (")[0]
        tally[key] = tally.get(key, 0) + 1
    print("summary:", tally)


if __name__ == "__main__":
    main()
