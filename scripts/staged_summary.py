#!/usr/bin/env python3
"""One table for everything staged in s3://somics-dev: technology > tissue > species.

Reads a cached bucket listing (built by --refresh) rather than re-walking 94k
objects on every run, joins each staged prefix to its registry row, and folds
the two metadata vocabularies (HuBMAP Title Case, literature free text) with the
same tables bucket_inventory.py uses.

    uv run --with boto3 python scripts/staged_summary.py --refresh
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from bucket_inventory import ORGAN, SPECIES, TECH, canon

REPO = Path(__file__).resolve().parents[1]


def listing(cache: Path, bucket: str, profile: str | None, refresh: bool) -> dict:
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    import boto3

    s3 = (boto3.Session(profile_name=profile) if profile else boto3.Session()).client("s3")
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            parts = o["Key"].split("/")
            if len(parts) < 2:
                continue
            # raw/_candidates/<accession>/ nests one level deeper than raw/<id>/
            pre = (
                "raw/_candidates/" + parts[2]
                if parts[0] == "raw" and parts[1] == "_candidates" and len(parts) > 2
                else parts[0] + "/" + parts[1]
            )
            a = agg[pre]
            a[0] += o["Size"]
            a[1] += 1
    cache.write_text(json.dumps(agg))
    return agg


def registry():
    """dataset_id -> row, plus lookups for the two prefix shapes in the bucket."""
    rows = list(csv.DictReader(open(REPO / "data" / "datasets.csv")))
    by_id = {r["dataset_id"]: r for r in rows}
    by_hbm, by_acc = {}, {}
    for r in rows:
        # \w matches underscore, so the three HuBMAP ID segments need an
        # explicit character class or the first one swallows the whole id.
        m = re.match(r"hubmap_(hbm[a-z0-9]+)_([a-z0-9]+)_([a-z0-9]+)_", r["dataset_id"], re.I)
        if m:
            by_hbm[".".join(m.groups()).upper()] = r
        for acc in (r.get("candidate_accessions") or "").split(";"):
            acc = acc.strip()
            if acc:
                by_acc.setdefault(acc, r)
    return by_id, by_hbm, by_acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default="somics-dev")
    ap.add_argument("--profile", default="sci-data-dev-poweruser")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--min-gb", type=float, default=5.0, help="roll up smaller technologies")
    ap.add_argument("--max-tissues", type=int, default=8, help="tissues shown per technology")
    args = ap.parse_args()

    cache = Path(args.cache) if args.cache else REPO / "data" / ".bucket_listing.json"
    agg = listing(cache, args.bucket, args.profile, args.refresh)
    by_id, by_hbm, by_acc = registry()

    # (tech, tissue, species) -> [bytes, datasets]
    cell: dict[tuple, list] = defaultdict(lambda: [0, 0])
    unjoined = [0, 0]
    atlas = [0, 0]
    for pre, (nbytes, _nfiles) in agg.items():
        if pre.startswith("somics_spatial_atlas"):
            atlas[0] += nbytes
            atlas[1] = 1
            continue
        if pre.startswith("hubmap/_"):
            continue
        r = None
        if pre.startswith("hubmap/"):
            r = by_hbm.get(pre.split("/", 1)[1].upper())
        elif pre.startswith("raw/_candidates/"):
            r = by_acc.get(pre.rsplit("/", 1)[1])
        elif pre.startswith("raw/"):
            r = by_id.get(pre.split("/", 1)[1])
        if r is None:
            unjoined[0] += nbytes
            unjoined[1] += 1
            continue
        key = (
            canon(r["platform"], TECH),
            canon(r["tissue"], ORGAN),
            canon(r["species"], SPECIES),
        )
        c = cell[key]
        c[0] += nbytes
        c[1] += 1

    tech_tot: dict[str, list] = defaultdict(lambda: [0, 0])
    tt_tot: dict[tuple, list] = defaultdict(lambda: [0, 0])
    for (t, o, _sp), (b, n) in cell.items():
        for d, k in ((tech_tot, t), (tt_tot, (t, o))):
            d[k][0] += b
            d[k][1] += n

    w = 46
    print(f"{'TECHNOLOGY / tissue / species':<{w}} {'datasets':>9} {'volume':>11}")
    print("-" * (w + 22))
    ranked = sorted(tech_tot.items(), key=lambda kv: -kv[1][0])
    big = [(t, v) for t, v in ranked if v[0] >= args.min_gb * 1e9]
    small = [(t, v) for t, v in ranked if v[0] < args.min_gb * 1e9]
    for tech, (tb, tn) in big:
        print(f"{tech:<{w}} {tn:>9,} {tb / 1e12:>8.2f} TB")
        organs = sorted(
            ((o, v) for (t, o), v in tt_tot.items() if t == tech), key=lambda kv: -kv[1][0]
        )
        # Never drop rows silently: whatever is not shown is still counted on a
        # trailing line, so the children always add up to the technology total.
        if len(organs) > args.max_tissues:
            rest = organs[args.max_tissues :]
            organs = organs[: args.max_tissues]
        else:
            rest = []
        for organ, (ob, on) in organs:
            print(f"{'  ' + organ:<{w}} {on:>9,} {ob / 1e12:>8.2f} TB")
            specs = sorted(
                ((s, v) for (t, o, s), v in cell.items() if t == tech and o == organ),
                key=lambda kv: -kv[1][0],
            )
            for sp, (sb, sn) in specs:
                print(f"{'    ' + sp:<{w}} {sn:>9,} {sb / 1e12:>8.2f} TB")
        if rest:
            rb = sum(v[0] for _, v in rest)
            rn = sum(v[1] for _, v in rest)
            print(f"{'  + ' + str(len(rest)) + ' more tissues':<{w}} {rn:>9,} {rb / 1e12:>8.2f} TB")
    if small:
        sb = sum(v[0] for _, v in small)
        sn = sum(v[1] for _, v in small)
        print(f"{'other platforms (' + str(len(small)) + ')':<{w}} {sn:>9,} {sb / 1e12:>8.2f} TB")
    print("-" * (w + 22))
    gb = sum(v[0] for v in cell.values())
    gn = sum(v[1] for v in cell.values())
    print(f"{'staged datasets with registry metadata':<{w}} {gn:>9,} {gb / 1e12:>8.2f} TB")
    print(
        f"{'prefixes with no registry match':<{w}} {unjoined[1]:>9,} {unjoined[0] / 1e12:>8.2f} TB"
    )
    print(f"{'somics_spatial_atlas/ (ingested)':<{w}} {atlas[1]:>9,} {atlas[0] / 1e12:>8.2f} TB")
    tot_b = gb + unjoined[0] + atlas[0]
    print(f"{'BUCKET TOTAL':<{w}} {gn + unjoined[1] + atlas[1]:>9,} {tot_b / 1e12:>8.2f} TB")


if __name__ == "__main__":
    main()
