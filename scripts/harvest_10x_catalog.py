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
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.fetch:
        asyncio.run(fetch(args.out))
    if args.merge:
        merge(args.merge, args.apply)


if __name__ == "__main__":
    main()
