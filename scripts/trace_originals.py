#!/usr/bin/env python3
"""Merge original-publication trace results into the curated dataset tables.

Consumes one or more Paperclip map exports produced by the trace prompt in
.claude/skills/harvest-datasets/SKILL.md (each paper's datasets with the cited
ORIGINAL publication expanded from its reference list), dedupes claims into
canonical datasets, resolves cited originals to DOIs via Crossref, and merges
the result into data/datasets.csv and data/model_dataset_usage.csv. Existing
rows are kept; new datasets are matched into them by original-publication DOI.

Inputs:
  --trace FILE     map export (repeatable; `paperclip results m_X --save FILE`)
  --meta FILE      JSONL of analyzing-paper metadata: one object per paper with
                   id, title, doi, year (build with `paperclip cat
                   /papers/<id>/meta.json` per paper)
  --cache FILE     Crossref response cache (JSON; created if absent)

Usage:
  python3 scripts/trace_originals.py --trace tr1.txt --trace tr2.txt \\
      --meta meta.jsonl --cache crossref_cache.json
"""

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "somics/0.1 (mailto:aoliveirapisco@chanzuckerberg.com)"}

DS_COLS = [
    "dataset_id",
    "dataset_name",
    "platform",
    "modality",
    "species",
    "tissue",
    "disease",
    "n_samples",
    "data_access_link",
    "download_url",
    "data_downloadable",
    "original_publication",
    "original_publication_link",
    "original_publication_year",
    "first_published_by_model_paper",
    "notes",
]
USE_COLS = [
    "model",
    "model_paper_title",
    "model_paper_link",
    "dataset_id",
    "usage",
    "alias_in_model_paper",
]

PROT = re.compile(r"codex|imc|mibi|cycif|4i|celldive|orion|ibex|imaging mass|phenocycler", re.I)
TRAN = re.compile(
    r"visium|xenium|merfish|cosmx|seqfish|slide|stereo|geomx|starmap|osmfish"
    r"|dbit|hdst|tomo|iss|\bst\b",
    re.I,
)


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def slug(s, n=28):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:n]


def plat_family(p):
    p = norm(p)
    for a in [
        "visium hd",
        "visium",
        "xenium",
        "merfish",
        "cosmx",
        "seqfish",
        "starmap",
        "stereo",
        "slide seq",
        "slideseq",
        "osmfish",
        "geomx",
        "dbit",
        "hdst",
        "codex",
        "imc",
        "mibi",
        "cycif",
        "tomo",
        "iss",
        "st",
    ]:
        if a.replace(" ", "") in p.replace(" ", ""):
            return a.replace(" ", "")
    return slug(p, 12) or "unknown"


def parse_trace(paths):
    claims = []
    ok = failed = 0
    for path in paths:
        txt = open(path).read()
        blocks = re.split(r"\n--- \[\d+\] \[(\w+)\] (.*?) ---\n", txt)
        for i in range(1, len(blocks) - 2, 3):
            status, body = blocks[i], blocks[i + 2]
            m = re.search(r"doc_id: (\S+)", body)
            jm = re.search(r"(\{.*\})", body, re.S)
            if status != "success" or not m or not jm:
                failed += 1
                continue
            try:
                dsets = json.loads(jm.group(1)).get("datasets", [])
            except Exception:
                failed += 1
                continue
            ok += 1
            for d in dsets:
                claims.append((m.group(1), d))
    print(f"parsed {ok} papers, {failed} failed blocks, {len(claims)} dataset claims")
    return claims


def crossref_doi(title, cache):
    tkey = norm(title)[:60]
    if not tkey:
        return None
    if tkey not in cache:
        q = urllib.parse.quote(title[:180])
        url = f"https://api.crossref.org/works?query.bibliographic={q}&rows=2"
        hit = None
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                items = json.load(r)["message"]["items"]
            for it in items:
                cand = norm((it.get("title") or [""])[0])
                if cand[:45] == tkey[:45] or (len(tkey) > 20 and tkey[:30] in cand):
                    hit = {
                        "doi": it["DOI"],
                        "year": (it.get("published", {}).get("date-parts") or [[None]])[0][0],
                    }
                    break
        except Exception:
            hit = None
        cache[tkey] = hit
        time.sleep(0.25)
    return cache[tkey]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="append", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--cache", default="crossref_cache.json")
    ap.add_argument("--datasets", default="data/datasets.csv")
    ap.add_argument("--usage", default="data/model_dataset_usage.csv")
    args = ap.parse_args()

    meta = {json.loads(line)["id"]: json.loads(line) for line in open(args.meta)}
    claims = parse_trace(args.trace)
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    # canonicalize by (original publication, platform family)
    canon, usage_claims = {}, []
    for doc_id, d in claims:
        pm = meta.get(doc_id, {})
        if d.get("original_is_this_paper"):
            okey = ("SELF", doc_id)
            opub, oyear, first_by = pm.get("title"), pm.get("year"), "yes"
            olink = f"https://doi.org/{pm['doi']}" if pm.get("doi") else ""
        else:
            a = norm(d.get("original_first_author") or "").split(" ")[0]
            t = norm(d.get("original_title") or "")[:40]
            if not (a and t):
                continue
            okey = (a, d.get("original_year"), t)
            jr = d.get("original_journal") or ""
            opub = (
                f"{(d.get('original_first_author') or '').strip()} et al., "
                f"{d.get('original_title')}"
                + (f", {jr}" if jr else "")
                + (f" ({d.get('original_year')})" if d.get("original_year") else "")
            )
            oyear, first_by, olink = d.get("original_year"), "no", ""
        key = (okey, plat_family(d.get("platform")))
        c = canon.setdefault(
            key,
            {
                "okey": okey,
                "dataset_name": d.get("name"),
                "platform": d.get("platform"),
                "species": d.get("species"),
                "tissue": d.get("tissue"),
                "original_publication": opub,
                "original_publication_link": olink,
                "original_publication_year": oyear,
                "first_published_by_model_paper": first_by,
                "original_title_raw": d.get("original_title"),
                "links": set(),
            },
        )
        if d.get("accession_or_link"):
            c["links"].add(d["accession_or_link"].strip())
        if d.get("name") and len(d["name"]) > len(c["dataset_name"] or ""):
            c["dataset_name"] = d["name"]
        usage_claims.append((doc_id, key, d.get("name") or ""))
    print(f"canonical datasets: {len(canon)}")

    # Crossref resolution for cited originals
    n = 0
    for c in canon.values():
        if c["first_published_by_model_paper"] == "no" and not c["original_publication_link"]:
            hit = crossref_doi(c["original_title_raw"] or "", cache)
            n += 1
            if n % 50 == 0:
                print(f"  crossref {n}...")
                cache_path.write_text(json.dumps(cache))
            if hit:
                c["original_publication_link"] = f"https://doi.org/{hit['doi']}"
                if hit.get("year") and not c["original_publication_year"]:
                    c["original_publication_year"] = hit["year"]
    cache_path.write_text(json.dumps(cache))

    # merge into existing tables (dedupe by original-publication DOI)
    existing = list(csv.DictReader(open(args.datasets)))
    existing_use = list(csv.DictReader(open(args.usage)))
    by_doi, ids = {}, set()
    for r in existing:
        ids.add(r["dataset_id"])
        m = re.search(r"10\.\S+", r["original_publication_link"] or "")
        if m:
            by_doi[m.group(0).lower().rstrip("/")] = r["dataset_id"]

    new_ds, key_to_id = [], {}
    for key, c in canon.items():
        m = re.search(r"10\.\S+", c["original_publication_link"] or "")
        if m and m.group(0).lower().rstrip("/") in by_doi:
            key_to_id[key] = by_doi[m.group(0).lower().rstrip("/")]
            continue
        author = c["okey"][0] if c["okey"][0] != "SELF" else slug(c["dataset_name"], 16)
        year = c["original_publication_year"] or ""
        base = slug(f"{author}{year}_{plat_family(c['platform'])}", 48)
        did, k = base, 2
        while did in ids:
            did, k = f"{base}_{k}", k + 1
        ids.add(did)
        key_to_id[key] = did
        p = c["platform"] or ""
        links = sorted(c["links"])
        resolved = c["original_publication_link"] or c["first_published_by_model_paper"] == "yes"
        new_ds.append(
            dict(
                zip(
                    DS_COLS,
                    [
                        did,
                        c["dataset_name"],
                        c["platform"],
                        "spatial proteomics"
                        if PROT.search(p)
                        else "spatial transcriptomics"
                        if TRAN.search(p)
                        else "",
                        c["species"],
                        c["tissue"],
                        "",
                        "",
                        links[0] if links else "",
                        "",
                        "",
                        c["original_publication"],
                        c["original_publication_link"],
                        c["original_publication_year"],
                        c["first_published_by_model_paper"],
                        "; ".join(
                            (["also: " + ", ".join(links[1:])] if len(links) > 1 else [])
                            + (
                                []
                                if resolved
                                else ["original publication link unresolved (Crossref miss)"]
                            )
                        ),
                    ],
                    strict=True,
                )
            )
        )

    def model_name(doc_id):
        t = meta.get(doc_id, {}).get("title") or doc_id
        m = re.match(r"([A-Za-z0-9+\-]{2,25}):", t)
        return m.group(1) if m else doc_id

    seen = {(u["model_paper_link"], u["dataset_id"]) for u in existing_use}
    new_use = []
    for doc_id, key, alias in usage_claims:
        did = key_to_id.get(key)
        if not did:
            continue
        pm = meta.get(doc_id, {})
        plink = f"https://doi.org/{pm['doi']}" if pm.get("doi") else doc_id
        if (plink, did) in seen:
            continue
        seen.add((plink, did))
        new_use.append(
            dict(
                zip(
                    USE_COLS,
                    [
                        model_name(doc_id),
                        pm.get("title") or doc_id,
                        plink,
                        did,
                        "analysis/benchmark",
                        alias,
                    ],
                    strict=True,
                )
            )
        )

    with open(args.datasets, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DS_COLS)
        w.writeheader()
        w.writerows(existing + new_ds)
    with open(args.usage, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=USE_COLS)
        w.writeheader()
        w.writerows(existing_use + new_use)
    print(
        f"added {len(new_ds)} datasets ({len(canon) - len(new_ds)} matched existing), "
        f"{len(new_use)} usage rows"
    )


if __name__ == "__main__":
    main()
