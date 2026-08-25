"""Summarise what is actually in the somics S3 bucket, joined to the registry.

The bucket holds three things with different provenance, so they are counted
separately rather than pooled:

  somics_spatial_atlas/  the ingested atlas (Lance + zarr) — queryable today
  raw/                   literature-derived source bundles, one prefix per
                         dataset_id, plus raw/_candidates/<accession>/ for the
                         datasets that cite 2-3 accessions with no way to tell
                         which is which
  hubmap/                whole HuBMAP datasets, one prefix per HuBMAP ID

What is *in the bucket* is deliberately distinguished from what is *in the
registry*: the registry lists 5,767 datasets, most of which have never been
downloaded. Only prefixes carrying a `_manifest.json` count as staged.

Run:
    uv run --with boto3 python scripts/bucket_inventory.py --bucket somics-dev [--profile NAME]
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import boto3

REPO = Path(__file__).resolve().parents[1]


def walk(s3, bucket, prefix):
    """prefix-key -> (bytes, files, has_manifest)"""
    agg = defaultdict(lambda: [0, 0, False])
    p = s3.get_paginator("list_objects_v2")
    for page in p.paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            parts = o["Key"].split("/")
            if len(parts) < 2:
                continue
            # raw/_candidates/<acc>/... nests one level deeper
            key = "/".join(parts[1:3]) if parts[1] == "_candidates" else parts[1]
            a = agg[key]
            a[0] += o["Size"]
            a[1] += 1
            if o["Key"].endswith("_manifest.json"):
                a[2] = True
    return agg


# The registry pools two vocabularies — HuBMAP writes Title Case ("Kidney
# (Left)", "Human"), the literature rows lowercase free text ("kidney",
# "homo sapiens") — so a raw group-by splits the same thing several ways.
# These fold only for display; the underlying rows are left as written.
SPECIES = [
    ("human", ("human", "homo sapiens")),
    ("mouse", ("mouse", "mus musculus", "mice", "murine")),
    ("rat", ("rat", "rattus")),
    ("zebrafish", ("zebrafish", "danio")),
    ("drosophila", ("drosophila",)),
    ("c. elegans", ("elegans",)),
    ("arabidopsis", ("arabidopsis",)),
    ("macaque", ("macaque", "rhesus")),
    ("chicken", ("chicken", "gallus")),
    ("pig", ("pig",)),
]
TECH = [
    ("Visium HD", ("visium hd", "visiumhd")),
    ("Visium", ("visium",)),
    ("Xenium", ("xenium",)),
    ("MERFISH", ("merfish", "merscope")),
    ("CosMx", ("cosmx",)),
    ("Stereo-seq", ("stereo",)),
    ("Slide-seq", ("slide-seq", "slideseq")),
    ("seqFISH", ("seqfish",)),
    ("GeoMx", ("geomx",)),
    ("CODEX/PhenoCycler", ("codex", "phenocycler")),
    ("IMC", ("imc", "imaging mass cytometry")),
    ("MIBI", ("mibi",)),
    ("Cell DIVE", ("cell dive",)),
    ("MALDI/DESI/SIMS", ("maldi", "desi", "sims")),
    ("Histology/H&E", ("histology", "h&e", "pas microscopy")),
    ("Autofluorescence", ("auto-fluorescence", "autofluorescence", "af")),
    ("STARmap/ISS", ("starmap", "in situ sequencing", "iss")),
    ("scRNA/snRNA (ref)", ("scrna", "snrna", "single-cell rna", "single cell rna", "drop-seq")),
    ("osmFISH/smFISH", ("osmfish", "smfish")),
    ("DBiT-seq", ("dbit",)),
]
ORGAN = [
    ("kidney", ("kidney", "renal", "nephron")),
    ("brain/CNS", ("brain", "cortex", "cns", "hippocamp", "cerebell", "olfactory bulb", "spinal")),
    ("lung/airway", ("lung", "bronch", "respiratory", "alveol")),
    ("heart", ("heart", "cardiac", "myocard")),
    ("liver", ("liver", "hepat")),
    ("placenta", ("placenta",)),
    ("uterus", ("uterus", "uterine")),
    ("intestine/colon", ("intestine", "colon", "ileum", "bowel", "appendix", "rect")),
    ("breast", ("breast", "mammary")),
    ("skin", ("skin", "epiderm", "derm")),
    ("spleen", ("spleen",)),
    ("lymph node", ("lymph",)),
    ("bone marrow", ("bone marrow",)),
    ("eye/retina", ("eye", "retina")),
    ("thymus", ("thymus",)),
    ("pancreas", ("pancrea",)),
    ("stomach", ("stomach", "gastric")),
    ("prostate/repro", ("prostate", "ovar", "testis", "endometri")),
    ("vasculature", ("vascul", "artery", "aorta")),
    ("muscle", ("muscle",)),
]


def canon(value, table):
    v = (value or "").strip().lower()
    if not v:
        return "(unstated)"
    for name, keys in table:
        if any(k in v for k in keys):
            return name
    return value.strip().lower()[:30]


def bar(n, total, width=22):
    return "█" * max(1, round(width * n / total)) if n else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--profile")
    args = ap.parse_args()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    reg = {r["dataset_id"]: r for r in csv.DictReader(open(REPO / "data" / "datasets.csv"))}
    # HuBMAP prefixes are HBM IDs; index the registry by the ID inside dataset_id
    by_hbmid = {}
    for did, r in reg.items():
        if did.startswith("hubmap_"):
            tok = did.split("_")[1:4]  # hbm493, vrqm, 643
            by_hbmid["".join(tok).upper()] = r

    sections = {}
    for pref in ("raw/", "hubmap/", "somics_spatial_atlas/"):
        sections[pref] = walk(s3, args.bucket, pref)

    print(f"s3://{args.bucket}\n")
    grand_b = grand_f = 0
    for pref, agg in sections.items():
        b = sum(v[0] for v in agg.values())
        f = sum(v[1] for v in agg.values())
        staged = sum(1 for v in agg.values() if v[2])
        grand_b += b
        grand_f += f
        label = {
            "raw/": "raw/ (literature)",
            "hubmap/": "hubmap/",
            "somics_spatial_atlas/": "somics_spatial_atlas/ (ingested)",
        }[pref]
        extra = f", {staged} with a manifest" if staged else ""
        print(f"  {label:36} {b / 1e12:7.2f} TB  {f:>8,} objects, {len(agg):>5} prefixes{extra}")
    print(f"  {'TOTAL':36} {grand_b / 1e12:7.2f} TB  {grand_f:>8,} objects\n")

    # join staged prefixes to registry metadata
    rows = []
    for did, v in sections["raw/"].items():
        if not v[2]:
            continue
        r = reg.get(did)
        rows.append(
            (
                r["tissue"] if r else "",
                r["species"] if r else "",
                r["platform"] if r else "",
                v[0],
                "literature",
            )
        )
    for hid, v in sections["hubmap/"].items():
        if not v[2]:
            continue
        r = by_hbmid.get(hid.replace(".", "").replace("-", "").upper())
        rows.append(
            (
                r["tissue"] if r else "",
                r["species"] if r else "human",
                r["platform"] if r else "",
                v[0],
                "hubmap",
            )
        )

    print(f"staged datasets joined to the registry: {len(rows)}\n")
    axes = ((0, "TISSUE", ORGAN), (1, "SPECIES", SPECIES), (2, "TECHNOLOGY", TECH))
    for idx, title, table in axes:
        agg = defaultdict(lambda: [0, 0])
        for row in rows:
            k = canon(row[idx], table)
            agg[k][0] += 1
            agg[k][1] += row[3]
        tot = sum(v[0] for v in agg.values()) or 1
        print(f"{title}")
        for k, (n, b) in sorted(agg.items(), key=lambda x: -x[1][0])[:14]:
            print(f"  {n:>5} {b / 1e9:>9.1f} GB  {bar(n, tot):22} {k}")
        print()


if __name__ == "__main__":
    main()
