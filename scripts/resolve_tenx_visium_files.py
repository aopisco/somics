#!/usr/bin/env python3
"""Find every file the Visium builder needs for each 10x-catalogue Visium row.

The registry keeps one verified bundle per dataset: the MEX tarball for Visium
and ``binned_outputs.tar.gz`` for Visium HD. Neither is what the builder reads.
It wants the filtered counts h5, the ``spatial/`` directory and the
full-resolution image that the spot coordinates live in -- and on the 10x CDN
those sit beside the recorded bundle under a small set of suffixes that
changed across Space Ranger releases:

- ``_filtered_feature_bc_matrix.h5``  counts (Visium; HD carries them per bin
  size inside ``_binned_outputs.tar.gz``)
- ``_spatial.tar.gz``                 tissue positions + scale factors
- ``_tissue_image.btf|.tif|.tiff``    the microscope image, which is the
  full-resolution frame whenever it exists (CytAssist runs)
- ``_image.tif``                      the only image on pre-CytAssist releases;
  on CytAssist releases it is the low-resolution instrument image and is *not*
  the coordinate frame
- ``_image.jpg``                      three 1.3.0 FFPE releases publish the
  full-resolution image only as a JPEG

Each candidate is HEAD-probed and recorded with its size, so what is written
here is what exists, not what the naming convention predicts. Rows that cannot
be built by the spec-driven builder are kept with a ``skip_reason`` rather than
dropped, so the table is the complete accounting of the block.

Writes ``data/tenx_visium_files.csv``.

Run:
    python scripts/resolve_tenx_visium_files.py [--workers 8]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import re
import urllib.request

import pandas as pd

REGISTRY = "data/datasets.csv"
OUT = "data/tenx_visium_files.csv"
# 10x's Cloudflare front rejects bare agents; a browser UA is required.
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
PREFIX_RE = re.compile(
    r"(https://cf\.10xgenomics\.com/samples/spatial-exp/[^/]+/[^/]+/[^/]+?)"
    r"_(?:filtered_feature_bc_matrix|binned_outputs)\.tar\.gz$"
)
# Three 1.3.0 FFPE releases ship the full-resolution image only as a JPEG; the
# builder converts it to a tiled TIFF, so it is accepted last.
IMAGE_SUFFIXES = (
    "_tissue_image.btf",
    "_tissue_image.tif",
    "_tissue_image.tiff",
    "_image.tif",
    "_image.jpg",
)


def head(url: str) -> int:
    """Content-Length, or 0 if the object is absent."""
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return int(resp.headers.get("Content-Length") or 0) if resp.status == 200 else 0
    except Exception:  # noqa: BLE001 - a missing sibling is the expected outcome
        return 0


def skip_reason(row: pd.Series) -> str | None:
    """Why the spec-driven builder cannot take this row as-is."""
    species = str(row["species"])
    tissue = str(row["tissue"])
    did = row["dataset_id"]
    if pd.isna(row["download_url"]):
        return "no verified bundle URL (see notes)"
    if "," in species:
        return "two species on one capture area (xenograft); organism is per-row"
    if species in ("Arabidopsis thaliana", "Soybean"):
        return "plant tissue; the donor/tissue schema is animal-oriented"
    if (
        "," in tissue
        or "microarray" in did
        or did.startswith("tenx_visium_hd_cytassist_11mm_human_ta")
    ):
        return "several tissues on one capture area; tissue is per-section"
    if "aggregate" in did:
        return "Space Ranger aggr output: several sections in one matrix; needs per-library split"
    return None


def resolve(row: pd.Series) -> dict:
    out = {
        "dataset_id": row["dataset_id"],
        "platform": row["platform"],
        "cdn_prefix": "",
        "sample": "",
        "counts_url": "",
        "counts_bytes": 0,
        "spatial_url": "",
        "spatial_bytes": 0,
        "image_url": "",
        "image_bytes": 0,
        "binned_url": "",
        "binned_bytes": 0,
        "skip_reason": skip_reason(row) or "",
    }
    if out["skip_reason"]:
        return out
    m = PREFIX_RE.match(str(row["download_url"]))
    if not m:
        out["skip_reason"] = f"download_url is not a recognised CDN bundle: {row['download_url']}"
        return out
    prefix = m.group(1)
    out["cdn_prefix"] = prefix
    out["sample"] = prefix.rsplit("/", 1)[-1]

    if row["platform"] == "Visium HD":
        out["binned_url"] = prefix + "_binned_outputs.tar.gz"
        out["binned_bytes"] = head(out["binned_url"])
    else:
        out["counts_url"] = prefix + "_filtered_feature_bc_matrix.h5"
        out["counts_bytes"] = head(out["counts_url"])
        out["spatial_url"] = prefix + "_spatial.tar.gz"
        out["spatial_bytes"] = head(out["spatial_url"])

    for suffix in IMAGE_SUFFIXES:
        n = head(prefix + suffix)
        if n:
            out["image_url"], out["image_bytes"] = prefix + suffix, n
            break

    missing = []
    if row["platform"] == "Visium HD":
        if not out["binned_bytes"]:
            missing.append("binned_outputs")
    else:
        if not out["counts_bytes"]:
            missing.append("filtered h5")
        if not out["spatial_bytes"]:
            missing.append("spatial.tar.gz")
    if not out["image_bytes"]:
        missing.append("image")
    if missing:
        out["skip_reason"] = "not on the CDN: " + ", ".join(missing)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    d = pd.read_csv(REGISTRY, low_memory=False)
    rows = d[d.dataset_id.str.startswith("tenx_") & d.platform.str.contains("Visium", na=False)]
    print(f"{len(rows)} 10x Visium/HD rows")
    with cf.ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(resolve, [r for _, r in rows.iterrows()]))
    results.sort(key=lambda r: (bool(r["skip_reason"]), r["platform"], r["dataset_id"]))
    with open(OUT, "w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)

    ready = [r for r in results if not r["skip_reason"]]
    total = sum(
        r["counts_bytes"] + r["spatial_bytes"] + r["image_bytes"] + r["binned_bytes"] for r in ready
    )
    print(
        f"wrote {OUT}: {len(ready)} buildable, {len(results) - len(ready)} skipped, "
        f"{total / 1e9:.0f} GB to fetch"
    )
    for r in results:
        if r["skip_reason"]:
            print(f"  skip {r['dataset_id']}: {r['skip_reason']}")
    by_img = pd.Series(
        [r["image_url"].rsplit("_", 2)[-1] if r["image_url"] else "-" for r in ready]
    ).value_counts()
    print("image suffixes:", by_img.to_dict())


if __name__ == "__main__":
    main()
