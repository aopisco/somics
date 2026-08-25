#!/usr/bin/env python3
"""Recover the technology for HuBMAP derived datasets the portal TSV hides.

The portal's metadata export writes `dataset_type = N/A` for pipeline-derived
datasets — the very rows where the technology matters, because a "MIBI
[DeepCell + SPRM]" dataset is MIBI data. The search API does carry it, and the
stager already recorded it in each `_manifest.json`, so the fix is a join
against what we staged rather than another portal fetch.

366 registry rows land as `platform = unspecified` without this, 8.8 TB of
staged data among them, which is enough to make them the largest "technology"
in any summary.

Run:
    uv run --with boto3 python scripts/backfill_hubmap_dataset_type.py [--apply]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HBM_ID = re.compile(r"hubmap_(hbm[a-z0-9]+)_([a-z0-9]+)_([a-z0-9]+)_(.*)$", re.I)
PROT = re.compile(r"codex|imc|mibi|cycif|phenocycler|cell dive|ibex", re.I)
TRAN = re.compile(r"seqfish|merfish|visium|xenium|slide-?seq|geomx|cosmx|rnaseq|snare", re.I)


def slug(s, n=34):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:n] or "na"


def manifests(bucket, profile):
    import boto3

    s3 = (boto3.Session(profile_name=profile) if profile else boto3.Session()).client("s3")
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="hubmap/"):
        for o in page.get("Contents", []):
            if o["Key"].endswith("_manifest.json"):
                keys.append(o["Key"])

    def get(k):
        try:
            return json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read())
        except Exception:
            return None

    out = {}
    with cf.ThreadPoolExecutor(24) as ex:
        for m in ex.map(get, keys):
            if m and m.get("hubmap_id"):
                out[m["hubmap_id"].upper()] = m
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default="somics-dev")
    ap.add_argument("--profile", default="sci-data-dev-poweruser")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    man = manifests(args.bucket, args.profile)
    print(f"manifests read: {len(man)}")

    path = REPO / "data" / "datasets.csv"
    rows = list(csv.DictReader(open(path)))
    cols = list(rows[0].keys())

    changed = Counter()
    for r in rows:
        m = HBM_ID.match(r["dataset_id"])
        if not m:
            continue
        if (r.get("platform") or "").strip().lower() not in ("", "unspecified"):
            continue
        entry = man.get(".".join(m.groups()[:3]).upper())
        dtype = (entry or {}).get("dataset_type") or ""
        if not dtype or dtype == "N/A":
            continue
        # "MIBI [DeepCell + SPRM]" -> platform MIBI, pipeline DeepCell + SPRM
        tech, _, pipe = dtype.partition("[")
        tech, pipe = tech.strip(), pipe.rstrip("]").strip()
        r["platform"] = tech
        if not (r.get("modality") or "").strip():
            r["modality"] = (
                "spatial proteomics"
                if PROT.search(tech)
                else "spatial transcriptomics"
                if TRAN.search(tech)
                else ""
            )
        if pipe:
            note = f"HuBMAP pipeline: {pipe}"
            r["notes"] = f"{r['notes']}; {note}" if r.get("notes") else note
        # the id carries the technology for legibility; "unspecified" was wrong
        head, organ_analyte = m.group(0)[: m.start(4)], m.group(4)
        if head.endswith("unspecified_"):
            r["dataset_id"] = head[: -len("unspecified_")] + slug(tech) + "_" + organ_analyte
        changed[tech] += 1

    for k, v in changed.most_common():
        print(f"  {v:5}  {k}")
    print(f"total rows updated: {sum(changed.values())}")

    if not args.apply:
        print("dry run — pass --apply to write")
        return
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
