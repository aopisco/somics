#!/usr/bin/env python3
"""
Append literature-extracted dataset rows to data/literature_datasets.csv.

The table is claim-level: one row per (dataset x source paper). The same dataset
reported by a different paper is a wanted row. Re-mining a paper already in the
table is not -- it silently duplicates everything that paper contributed. So the
dedup key is source_paper_id.

Usage
-----
  # which papers have already been mined? (feed this to your search filter)
  python append_datasets.py --known --sheet data/literature_datasets.csv

  # append extracted rows
  python append_datasets.py \
      --rows /tmp/extracted.json \
      --sheet data/literature_datasets.csv \
      --found-via literature_search

  # preview without writing
  python append_datasets.py --rows /tmp/extracted.json --sheet data/... --dry-run

Input format for --rows: a JSON array of objects. Unknown keys are ignored,
missing keys become blank. Blank is always preferable to a guess.

Exit codes
----------
  0  appended (or nothing to append)
  1  bad input, schema violation, or refused rows without --force
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------
# Schema -- order is load-bearing; the CSV is read by other tooling
# --------------------------------------------------------------------------

COLUMNS = [
    "dataset_name", "platform", "modality", "species", "tissue", "disease",
    "n_samples", "data_access_link", "origin",
    "source_paper_title", "source_paper_doi", "source_paper_year",
    "source_paper_id", "found_via",
]

VALID_ORIGIN = {"generated", "reused", ""}
VALID_MODALITY = {"spatial transcriptomics", "spatial proteomics", "spatial multiomics", ""}

# Canonical platform -> spellings seen in the wild. Extend this as new
# spellings appear; an unmapped platform is left alone rather than guessed at,
# so the cost of an omission is a fragmented group-by, not a wrong value.
PLATFORM_ALIASES: dict[str, list[str]] = {
    "10x Visium": [
        "visium", "10x visium", "visium spatial gene expression",
        "10x genomics visium", "visium sge", "10x visium spatial transcriptomics",
    ],
    "10x Visium HD": ["visium hd", "10x visium hd", "visium-hd"],
    "10x Xenium": ["xenium", "10x xenium", "xenium in situ", "10x genomics xenium"],
    "MERFISH": ["merfish", "merscope", "vizgen merscope", "vizgen merfish"],
    "CosMx": ["cosmx", "nanostring cosmx", "cosmx smi", "bruker cosmx"],
    "GeoMx": ["geomx", "nanostring geomx", "geomx dsp", "geomx digital spatial profiler"],
    "Stereo-seq": ["stereo-seq", "stereoseq", "stereo seq", "bgi stereo-seq"],
    "Slide-seq": ["slide-seq", "slideseq", "slide-seqv2", "slide-seq v2", "slideseqv2"],
    "seqFISH": ["seqfish", "seqfish+", "seq-fish"],
    "CODEX": ["codex", "phenocycler", "akoya codex", "codex phenocycler"],
    "IMC": ["imc", "imaging mass cytometry", "hyperion", "hyperion imc"],
    "MIBI-TOF": ["mibi", "mibi-tof", "mibitof", "multiplexed ion beam imaging"],
    "CyCIF": ["cycif", "t-cycif", "tissue cycif"],
    "MALDI": ["maldi", "maldi-msi", "maldi imaging"],
    "Spatial CITE-seq": ["spatial cite-seq", "spatial citeseq"],
    "DBiT-seq": ["dbit-seq", "dbitseq"],
    "ST (legacy)": ["spatial transcriptomics (st)", "legacy st", "original st"],
}
_ALIAS_LOOKUP = {a: canon for canon, aliases in PLATFORM_ALIASES.items() for a in aliases}


def normalize_platform(value: str) -> tuple[str, bool]:
    """Return (normalized, was_recognized). Unrecognized values pass through."""
    raw = str(value or "").strip()
    if not raw:
        return "", True
    key = re.sub(r"\s+", " ", raw.lower()).strip(" .")
    if key in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[key], True
    if raw in PLATFORM_ALIASES:
        return raw, True
    return raw, False


def clean(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() in {"nan", "none", "null", "n/a", "na", "unknown", "not stated"} else s


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def read_sheet(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], COLUMNS[:]
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        cols = list(reader.fieldnames or COLUMNS)
    return rows, cols


def write_sheet(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in cols})
    tmp.replace(path)  # atomic -- a crash mid-write can't truncate the inventory


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sheet", required=True, type=Path)
    ap.add_argument("--rows", type=Path, help="JSON array of extracted dataset rows")
    ap.add_argument("--found-via", default="literature_search")
    ap.add_argument("--known", action="store_true",
                    help="print source_paper_ids already mined, one per line, then exit")
    ap.add_argument("--force", action="store_true",
                    help="append even for already-mined papers (delete their old rows first)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    existing, cols = read_sheet(args.sheet)
    known_ids = {clean(r.get("source_paper_id")) for r in existing} - {""}

    if args.known:
        for pid in sorted(known_ids):
            print(pid)
        return 0

    if not args.rows:
        print("error: --rows is required unless --known is given", file=sys.stderr)
        return 1
    if not args.rows.exists():
        print(f"error: {args.rows} not found", file=sys.stderr)
        return 1

    try:
        incoming = json.loads(args.rows.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {args.rows} is not valid JSON -- {exc}", file=sys.stderr)
        return 1
    if not isinstance(incoming, list):
        print("error: --rows must contain a JSON array of row objects", file=sys.stderr)
        return 1

    accepted: list[dict] = []
    refused: Counter = Counter()
    unmapped: Counter = Counter()
    warnings: list[str] = []

    for i, raw in enumerate(incoming):
        if not isinstance(raw, dict):
            refused["not an object"] += 1
            continue

        row = {c: clean(raw.get(c)) for c in COLUMNS}
        row["found_via"] = row["found_via"] or args.found_via

        pid = row["source_paper_id"]
        if not pid:
            refused["no source_paper_id"] += 1
            continue
        if pid in known_ids and not args.force:
            refused[f"already mined ({pid})"] += 1
            continue
        if not row["dataset_name"]:
            refused["no dataset_name"] += 1
            continue

        platform, ok = normalize_platform(row["platform"])
        row["platform"] = platform
        if not ok:
            unmapped[platform] += 1

        if row["origin"] not in VALID_ORIGIN:
            warnings.append(f"row {i}: origin={row['origin']!r} is not generated/reused -- blanked")
            row["origin"] = ""
        if row["modality"] not in VALID_MODALITY:
            warnings.append(f"row {i}: modality={row['modality']!r} unrecognized -- kept as-is")

        if row["n_samples"] and not re.fullmatch(r"\d+", row["n_samples"]):
            warnings.append(f"row {i}: n_samples={row['n_samples']!r} is not an integer -- blanked")
            row["n_samples"] = ""

        accepted.append(row)

    before = len(existing)
    merged = existing + accepted
    new_papers = len({r["source_paper_id"] for r in accepted})

    if args.dry_run:
        print(f"[dry run] would add {len(accepted)} rows from {new_papers} papers "
              f"({before} -> {before + len(accepted)})")
    else:
        write_sheet(args.sheet, merged, cols)

    print(f"{len(accepted)} rows from {new_papers} papers · {before} -> {len(merged)} total")

    if refused:
        print("\nrefused:")
        for reason, n in refused.most_common():
            print(f"  {n:>4}  {reason}")
        if any("already mined" in r for r in refused):
            print("  (re-mining a paper duplicates its rows -- delete the old ones, then --force)")
    if unmapped:
        print("\nplatform not in the controlled vocabulary (left as written):")
        for name, n in unmapped.most_common():
            print(f"  {n:>4}  {name!r}")
        print("  add real aliases to PLATFORM_ALIASES so the next run groups them correctly")
    if warnings:
        print("\nwarnings:")
        for w in warnings[:20]:
            print(f"  {w}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more")

    if refused and not accepted:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
