#!/usr/bin/env python3
"""Harvest 10x Genomics' dataset catalogue and merge the spatial platforms in.

The catalogue at 10xgenomics.com/datasets sits behind a Vercel bot challenge, so
plain HTTP gets a 429 challenge page from every host and path, sitemap included.
A real browser passes it, and the page then calls
``/api/search?document=dataset`` — so this drives Chromium to clear the
challenge and pages that API from inside the browser context.

Only the spatial platforms are merged: **Xenium, Visium and Atera**. Chromium is
single-cell, and the 59 records with no platform are De Novo Assembly and
Genome & Exome. Atera is 10x's newest in situ platform and currently has two
preview datasets.

The search API carries no download links — those live on each dataset page — so
``--merge`` writes the landing page and leaves ``download_url`` blank rather
than inventing one. ``--links`` fills them in a second pass.

Run:
    uv run --with playwright python scripts/harvest_10x_catalog.py --fetch --out cat.json
    uv run python scripts/harvest_10x_catalog.py --merge cat.json [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
from collections import Counter

SEARCH = (
    "https://www.10xgenomics.com/api/search?document=dataset&search=&sort=publishedAt+DESC&offset="
)
LANDING = "https://www.10xgenomics.com/datasets/"
SPATIAL = {"Xenium", "Visium", "Atera"}


async def fetch(out_path: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.goto(
            "https://www.10xgenomics.com/datasets", wait_until="domcontentloaded", timeout=120_000
        )
        await page.wait_for_timeout(3000)
        seen: dict[str, dict] = {}
        offset = 0
        while True:
            data = await page.evaluate("u => fetch(u).then(r => r.json())", SEARCH + str(offset))
            hits = data.get("hits") or data.get("results") or data.get("items") or []
            if not hits:
                break
            for h in hits:
                seen[h.get("slug") or h.get("path")] = h
            print(f"  offset {offset}: +{len(hits)} (total {len(seen)})", flush=True)
            offset += len(hits)
            await page.wait_for_timeout(400)
        await browser.close()
    json.dump(list(seen.values()), open(out_path, "w"))
    print(f"saved {len(seen)} records to {out_path}")


BUNDLE_SUFFIXES = (
    "_xe_outs.zip",
    "_outs.zip",
    "_binned_outputs.tar.gz",
    "_filtered_feature_bc_matrix.tar.gz",
)
CDN_RE = re.compile(r"https://cf\.10xgenomics\.com/samples/[^\"'\\ <>]+")


def _head(url: str) -> tuple[object, int]:
    import urllib.request

    ua = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=ua, method="HEAD")
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, int(resp.headers.get("Content-Length") or 0)
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
        return getattr(exc, "code", type(exc).__name__), 0


def bundles_from_links(links: list[str]) -> list[tuple[str, int]]:
    """Verified bundle URLs for a page's CDN links.

    Pages render inconsistently: some list every file including the outs bundle,
    some list only ancillary files (gene_panel.json, the H&E image), and Visium
    HD pages list nothing but the Loupe installer. Where the bundle is absent it
    is still *derivable* — any ancillary link exposes the sample prefix, and the
    bundle sits beside it under a known suffix.

    Derived URLs are probed before being returned. Nothing unverified is written;
    guessing a CDN path from a title is how a pancreas section ends up labelled
    breast.
    """
    found: dict[str, int] = {}
    prefixes = set()
    for link in links:
        if any(link.endswith(s) for s in BUNDLE_SUFFIXES):
            status, size = _head(link)
            if status == 200:
                found[link] = size
        # ".../<Sample>/<Sample>_something.ext" -> ".../<Sample>/<Sample>"
        m = re.match(r"(https://cf\.10xgenomics\.com/samples/[^/]+/[^/]+/([^/]+)/\2)[._]", link)
        if m:
            prefixes.add(m.group(1))
    for prefix in sorted(prefixes):
        if any(u.startswith(prefix) for u in found):
            continue
        for suffix in BUNDLE_SUFFIXES:
            status, size = _head(prefix + suffix)
            if status == 200:
                found[prefix + suffix] = size
                break
    return sorted(found.items(), key=lambda kv: -kv[1])


async def resolve_links(rows_out: str, limit: int | None) -> None:
    """Visit each catalogue row's landing page and record verified bundle URLs."""
    from playwright.async_api import async_playwright

    registry = list(csv.DictReader(open("data/datasets.csv")))
    todo = [
        r
        for r in registry
        if r["dataset_id"].startswith("tenx_")
        and not (r.get("download_url") or "").strip()
        and "10xgenomics.com/datasets/" in (r.get("data_access_link") or "")
    ][: limit or None]
    print(f"resolving links for {len(todo)} row(s)")

    results: dict[str, dict] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await (await browser.new_context()).new_page()
        for i, r in enumerate(todo, 1):
            url = r["data_access_link"]
            try:
                await page.goto(url, wait_until="networkidle", timeout=90_000)
                await page.wait_for_timeout(1200)
                html = await page.content()
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(todo)}] {r['dataset_id']}: page error {type(exc).__name__}")
                continue
            links = sorted(set(CDN_RE.findall(html)))
            bundles = bundles_from_links(links)
            results[r["dataset_id"]] = {
                "links_seen": len(links),
                "bundles": [{"url": u, "bytes": n} for u, n in bundles],
            }
            top = bundles[0][0].split("/samples/")[-1][:58] if bundles else "-"
            print(
                f"  [{i}/{len(todo)}] {r['dataset_id'][:34]:<34} {len(bundles)} bundle(s)  {top}",
                flush=True,
            )
            json.dump(results, open(rows_out, "w"), indent=1)
        await browser.close()
    print(
        f"\nwrote {rows_out}: {sum(1 for v in results.values() if v['bundles'])} "
        f"of {len(results)} rows got a verified bundle"
    )


def apply_links(results_path: str, apply: bool) -> None:
    results = json.load(open(results_path))
    rows = list(csv.DictReader(open("data/datasets.csv")))
    cols = list(rows[0])
    filled = 0
    for r in rows:
        res = results.get(r["dataset_id"])
        if not res or not res["bundles"] or (r.get("download_url") or "").strip():
            continue
        best = res["bundles"][0]
        r["download_url"] = best["url"]
        extra = len(res["bundles"]) - 1
        note = f"bundle verified {best['bytes'] / 1e9:.1f} GB"
        if extra:
            note += f"; {extra} further section bundle(s) on the same page"
        r["notes"] = re.sub(
            r"download_url not set: the catalogue API carries no file links",
            note,
            r.get("notes", ""),
        )
        filled += 1
    print(f"rows to fill: {filled}")
    if not apply:
        print("dry run — pass --apply to write")
        return
    tmp = "data/datasets.csv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, "data/datasets.csv")
    print(f"wrote data/datasets.csv ({filled} download_url set)")


def platform_of(record: dict) -> str:
    """The platform as the registry names it, not as the facet does.

    The facet calls Visium HD "Visium"; the two are different instruments with
    different resolution, and the registry already distinguishes them.
    """
    base = record.get("platformNameStr") or ""
    text = f"{record.get('title', '')} {record.get('productName', '')}"
    if base == "Visium" and re.search(r"visium\s*hd", text, re.I):
        return "Visium HD"
    return base


def slug_id(slug: str, taken: set[str]) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")[:44]
    candidate = f"tenx_{stem}"
    n = 2
    while candidate in taken:
        candidate = f"tenx_{stem}_{n}"
        n += 1
    return candidate


def merge(catalog_path: str, apply: bool) -> None:
    catalog = json.load(open(catalog_path))
    wanted = [h for h in catalog if (h.get("platformNameStr") or "") in SPATIAL]
    rows = list(csv.DictReader(open("data/datasets.csv")))
    cols = list(rows[0])

    # A slug appearing anywhere in a row's links, notes or name means we already
    # hold that dataset under some other id; adding it again would double-count.
    blob = " || ".join(
        " ".join(
            (r.get(k) or "") for k in ("data_access_link", "download_url", "notes", "dataset_name")
        )
        for r in rows
    ).lower()
    taken = {r["dataset_id"] for r in rows}

    added = []
    for h in wanted:
        slug = (h.get("slug") or "").lower()
        if not slug or slug in blob:
            continue
        platform = platform_of(h)
        row = {c: "" for c in cols}
        row.update(
            {
                "dataset_id": slug_id(slug, taken),
                "dataset_name": h.get("title", "")[:220],
                "platform": platform,
                "modality": "spatial transcriptomics",
                "is_spatial": "yes",
                "species": ", ".join(h.get("species") or []),
                "tissue": ", ".join(h.get("anatomicalEntities") or []),
                "disease": ", ".join(h.get("diseaseStateNames") or []),
                "data_access_link": LANDING + slug,
                "original_publication": "10x Genomics Datasets",
                "first_published_by_model_paper": "no",
                "notes": "; ".join(
                    x
                    for x in (
                        "from the 10x dataset catalogue",
                        f"product: {h.get('productName')}" if h.get("productName") else "",
                        f"pipeline: {h.get('softwareName')} {h.get('pipeline')}"
                        if h.get("pipeline")
                        else "",
                        f"preservation: {', '.join(h.get('preservationMethods') or [])}"
                        if h.get("preservationMethods")
                        else "",
                        "download_url not set: the catalogue API carries no file links",
                    )
                    if x
                ),
            }
        )
        taken.add(row["dataset_id"])
        added.append(row)

    print(f"catalogue spatial records: {len(wanted)}")
    print(f"already referenced:        {len(wanted) - len(added)}")
    print(f"new rows:                  {len(added)}")
    for k, v in Counter(r["platform"] for r in added).most_common():
        print(f"   {v:4}  {k}")
    if not apply:
        print("\ndry run — pass --apply to write")
        return
    tmp = "data/datasets.csv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows + added)
    os.replace(tmp, "data/datasets.csv")
    print(f"\nwrote data/datasets.csv (+{len(added)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", default="10x_catalog.json")
    ap.add_argument("--merge", metavar="CATALOG_JSON")
    ap.add_argument("--links", metavar="RESULTS_JSON", help="resolve bundle URLs into this file")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply-links", metavar="RESULTS_JSON")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.fetch:
        asyncio.run(fetch(args.out))
    if args.merge:
        merge(args.merge, args.apply)
    if args.links:
        asyncio.run(resolve_links(args.links, args.limit))
    if args.apply_links:
        apply_links(args.apply_links, args.apply)


if __name__ == "__main__":
    main()
