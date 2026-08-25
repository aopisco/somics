"""Add HuBMAP datasets to the curated registry, one row per dataset.

Source is the portal's metadata export (TSV), which is richer than the search
API: it carries `origin_samples_unique_mapped_organs` for every dataset, where
the API leaves `anatomy_1` empty for most of them, and it states
`data_access_level` outright rather than making us infer access from
`contains_human_genetic_sequences`.

Every dataset gets a row, including the protected ones. They exist and are
worth recording; what differs is that they carry no `download_url` and are
marked so in `data_downloadable`.

dataset_id follows hubmap_<HBM-ID>_<technology>_<tissue>_<analyte>. The HuBMAP
ID alone is already unique (3,945/3,945), so the trailing parts are for
legibility rather than disambiguation — you can read what a row is without
joining anything.

Run:
    uv run python scripts/add_hubmap_to_registry.py --tsv <export.tsv> [--apply]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORTAL = "https://portal.hubmapconsortium.org/browse/dataset"
ASSETS = "https://assets.hubmapconsortium.org"

PROT = re.compile(
    r"codex|mibi|phenocycler|cell dive|imc|immunofluor|maldi|desi|sims|"
    r"auto-?fluorescence|histology|imaging mass",
    re.I,
)
TRAN = re.compile(r"geomx|visium|xenium|slide-?seq|seqfish|rnaseq|snare|merfish", re.I)


def slug(s, n=34):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:n] or "na"


def clean(v):
    v = (v or "").strip()
    return "" if v in ("", "N/A", "n/a", "None") else v


def modality(tech, analyte):
    t = f"{tech} {analyte}"
    if TRAN.search(t) or "rna" in (analyte or "").lower():
        return "spatial transcriptomics"
    if PROT.search(t) or "protein" in (analyte or "").lower():
        return "spatial proteomics"
    if "lipid" in (analyte or "").lower() or "polysacc" in (analyte or "").lower():
        return "spatial metabolomics"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src = [
        r
        for r in csv.DictReader(open(args.tsv), delimiter="\t")
        if r.get("uuid") and len(r["uuid"]) == 32
    ]
    print(f"{len(src)} HuBMAP datasets in the export")

    existing = list(csv.DictReader(open(REPO / "data" / "datasets.csv")))
    cols = list(existing[0])
    have = {r["dataset_id"] for r in existing}

    new, kinds = [], Counter()
    for r in src:
        hid = r["hubmap_id"]
        tech = clean(r.get("dataset_type")) or clean(r.get("assay_type")) or "unspecified"
        organ = clean(r.get("origin_samples_unique_mapped_organs")) or "unspecified"
        analyte = clean(r.get("analyte_class"))
        public = clean(r.get("data_access_level")) == "public"
        did = f"hubmap_{slug(hid, 18)}_{slug(tech, 22)}_{slug(organ, 20)}_{slug(analyte, 16)}"
        if did in have:
            continue
        have.add(did)
        year = ""
        ts = clean(r.get("published_timestamp"))
        if ts.isdigit():
            year = dt.datetime.fromtimestamp(int(ts) / 1000, dt.UTC).year
        name = f"HuBMAP {hid} · {tech} · {organ}" + (f" · {analyte}" if analyte else "")
        row = dict.fromkeys(cols, "")
        row.update(
            {
                "dataset_id": did,
                "dataset_name": name,
                "platform": tech,
                "modality": modality(tech, analyte),
                "species": "human",
                "tissue": organ,
                "disease": "",
                "n_samples": "",
                "data_access_link": f"{PORTAL}/{r['uuid']}",
                "download_url": f"{ASSETS}/{r['uuid']}/" if public else "",
                "data_downloadable": (
                    "yes (public HuBMAP assets)"
                    if public
                    else "no (HuBMAP protected: controlled access required)"
                ),
                "original_publication": f"HuBMAP Consortium · {clean(r.get('group_name'))}",
                "original_publication_link": f"{PORTAL}/{r['uuid']}",
                "original_publication_year": year,
                "first_published_by_model_paper": "no",
                "notes": f"from the HuBMAP portal metadata export {args.tsv.name}; "
                f"uuid {r['uuid']}; access {clean(r.get('data_access_level'))}",
            }
        )
        if "candidate_accessions" in row:
            row["candidate_accessions"] = ""
        if "perturbation" in row:
            row["perturbation"] = ""
        new.append(row)
        kinds[("public" if public else "protected")] += 1

    print(f"new rows: {len(new)}  ({dict(kinds)})")
    print(f"  modality: {dict(Counter(r['modality'] or 'unclassified' for r in new))}")
    print("  sample ids:")
    for r in new[:4]:
        print(f"    {r['dataset_id']}")
    if not args.apply:
        print("\ndry run — pass --apply to write")
        return
    with open(REPO / "data" / "datasets.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(existing + new)
    print(f"\nwritten: {len(existing)} + {len(new)} = {len(existing) + len(new)} rows")


if __name__ == "__main__":
    main()
