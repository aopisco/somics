#!/usr/bin/env python3
"""Verify the 10x Xenium outs bundles the registry points at and classify them.

Every Xenium row with a 10x-CDN ``outs`` URL (catalogue rows and the literature
rows that cite the same bundles) is HEAD-probed and annotated with what a
builder must know before opening it:

- ``xoa`` -- the Xenium Onboard Analysis version in the CDN path. 1.x bundles
  carry one ``morphology_focus.ome.tif`` (DAPI); 2.0 and later carry a
  ``morphology_focus/`` directory of channel images (one for RNA-only runs,
  four with the multimodal segmentation stains, dozens with protein
  co-detection).
- ``protein`` -- the two "In Situ Gene and Protein Expression" datasets, whose
  feature axis mixes gene and protein features; they need a second feature
  space and go in a later pass.
- re-releases of one sample under two versions (``Xenium_V1_FFPE_Human_Breast_
  IDC_With_Addon`` at 1.0.2 and 1.3.0) keep the newest, as the Visium block did.
- the three preview samples already in the atlas are skipped by name: their
  sections were ingested under hand-written ids, so the duplicate-section guard
  would not catch a second copy.

Writes ``data/tenx_xenium_files.csv``.

Run:
    python scripts/resolve_tenx_xenium_files.py [--workers 8]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import re
import urllib.request

import pandas as pd

REGISTRY = "data/datasets.csv"
OUT = "data/tenx_xenium_files.csv"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
URL_RE = re.compile(
    r"https://cf\.10xgenomics\.com/samples/xenium/(?P<xoa>[^/]+)/(?P<sample>[^/]+)/"
    r"(?P=sample)_(?:xe_)?outs\.zip$"
)
ALREADY_IN_ATLAS = {
    "Xenium_Preview_Human_Non_diseased_Lung_With_Add_on_FFPE",
    "Xenium_Preview_Human_Lung_Cancer_With_Add_on_2_FFPE",
    "Xenium_V1_hColon_Cancer_Add_on_FFPE",
}


def head(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001
        return 0


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def resolve(row: pd.Series) -> dict:
    url = str(row["download_url"])
    m = URL_RE.match(url)
    out = {
        "dataset_id": row["dataset_id"],
        "species": row["species"],
        "tissue": row["tissue"],
        "outs_url": url,
        "outs_bytes": 0,
        "xoa": m.group("xoa") if m else "",
        "sample": m.group("sample") if m else "",
        "morphology_layout": "",
        "protein": "yes"
        if "Protein" in str(row.get("notes", "")) + str(row.get("dataset_name", ""))
        else "no",
        "skip_reason": "",
    }
    if not m:
        out["skip_reason"] = f"not a recognised Xenium outs URL: {url}"
        return out
    out["morphology_layout"] = "file" if version_tuple(out["xoa"]) < (2,) else "directory"
    if out["sample"] in ALREADY_IN_ATLAS:
        out["skip_reason"] = "section already in the atlas (hackathon preview spec)"
        return out
    out["outs_bytes"] = head(url)
    if not out["outs_bytes"]:
        out["skip_reason"] = "outs bundle not on the CDN"
    return out


def dedupe(results: list[dict]) -> None:
    by_sample: dict[str, list[dict]] = {}
    for r in results:
        if not r["skip_reason"]:
            by_sample.setdefault(r["sample"], []).append(r)
    for sample, rows in by_sample.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: version_tuple(r["xoa"]), reverse=True)
        for r in rows[1:]:
            r["skip_reason"] = (
                f"re-release of 10x sample {sample} under an older Xenium Onboard Analysis; "
                f"built from {rows[0]['dataset_id']}"
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    d = pd.read_csv(REGISTRY, low_memory=False)
    rows = d[
        d.platform.str.contains("Xenium", na=False)
        & d.download_url.astype(str).str.contains("cf.10xgenomics.com/samples/xenium")
    ]
    print(f"{len(rows)} Xenium rows with a 10x-CDN outs URL")
    with cf.ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(resolve, [r for _, r in rows.iterrows()]))
    dedupe(results)
    results.sort(
        key=lambda r: (
            bool(r["skip_reason"]),
            r["protein"],
            version_tuple(r["xoa"]),
            r["dataset_id"],
        )
    )
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    ready = [r for r in results if not r["skip_reason"]]
    first = [r for r in ready if r["protein"] == "no"]
    print(
        f"wrote {OUT}: {len(ready)} buildable ({len(first)} first pass, "
        f"{len(ready) - len(first)} protein co-detection for a second pass), "
        f"{len(results) - len(ready)} skipped, "
        f"{sum(r['outs_bytes'] for r in ready) / 1e9:.0f} GB to fetch"
    )
    for r in results:
        if r["skip_reason"]:
            print(f"  skip {r['dataset_id']}: {r['skip_reason']}")
    print("by XOA:", pd.Series([r["xoa"] for r in first]).value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
