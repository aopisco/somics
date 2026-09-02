"""Flag which registry rows are spatial, and name the epigenomics ones properly.

Two problems this fixes, both surfaced by a 374 GB dissociated scATAC-seq
archive appearing as the second-largest "technology" in a corpus summary:

1. **Nothing distinguished spatial data from reference data.** Papers routinely
   cite dissociated scRNA/scATAC for deconvolution or annotation, and those
   datasets are legitimately in the registry — but counting them alongside
   spatial ones overstates coverage. A new `is_spatial` column separates them.

2. **Spatial epigenomics had no modality.** The modality rules keyed off
   transcriptomics and proteomics keywords only, so spatial ATAC-RNA-seq,
   MISAR-seq, spatial CUT&Tag and Slide-tags all fell through to blank. They
   now get `spatial epigenomics`, and their platform strings are normalised to
   name the assay ("spatial ATAC-RNA-seq", "MISAR-seq (spatial ATAC)") rather
   than a vendor kit.

Conservative by construction: a row is only marked `no` when the platform
clearly names a dissociated or bulk assay. Anything unrecognised is left
`unknown` rather than guessed, since a wrong flag is worse than an absent one.

Run:
    uv run python scripts/classify_spatial_modality.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Spatially resolved epigenome assays. Slide-tags is included: nuclei are
# barcoded by spatial position before dissociation, so the readout is spatial.
EPIGENOMIC = re.compile(
    r"spatial[- ]?atac|atac[- ]?rna[- ]?seq|misar|spatial[- ]?cut&?tag|cut&?tag[- ]?rna"
    r"|spatial[- ]?epigenom|slide[- ]?tags|epigenome[- ]?transcriptome",
    re.I,
)

# Assays that resolve position. Deliberately excludes anything that begins by
# dissociating the tissue.
SPATIAL = re.compile(
    r"visium|xenium|merfish|merscope|cosmx|seqfish|slide-?seq|slide-?tags|stereo-?seq"
    r"|geomx|starmap|osmfish|smfish|dbit|hdst|tomo-?seq|in situ sequencing|\biss\b"
    r"|codex|phenocycler|imc|imaging mass cytometry|mibi|cycif|\b4i\b|cell ?dive|ibex"
    r"|maldi|desi|\bsims\b|mass spectrometry imaging|rnascope|spatial|curio|perturb-?fish"
    r"|perturb-?map|misar|baristaseq|expansion|split-?fish|sci-?space|histology"
    r"|auto-?fluorescence|pas microscopy|multiplexed imaging",
    re.I,
)

# Assays that require dissociation or bulk tissue — reference data, not spatial.
DISSOCIATED = re.compile(
    r"^\s*(sc|sn|sci-|bulk )?(rna-?seq|atac-?seq)|10x chromium|chromium single|drop-?seq"
    r"|smart-?seq|cite-?seq|mars-?seq|snrna|scrna|snatac|scatac|sci-atac|multiome"
    r"|hiseq|novaseq|nextseq|bgiseq|illumina|microarray|mass spec(?!.*imaging)"
    r"|flow cytometry|facs|elisa|western",
    re.I,
)


def classify(platform, modality, name):
    p = f"{platform} {name}"
    if EPIGENOMIC.search(p):
        return "yes", "spatial epigenomics"
    if SPATIAL.search(p):
        return "yes", modality or ""
    if DISSOCIATED.search(platform or ""):
        return "no", modality or ""
    return "unknown", modality or ""


# Platform strings that name a kit or sequencer rather than the spatial assay.
RENAME = [
    (re.compile(r"^spatial[- ]?atac[- ]?rna.*", re.I), "spatial ATAC-RNA-seq"),
    (re.compile(r"^misar", re.I), "MISAR-seq (spatial ATAC)"),
    (re.compile(r"spatial cut&?tag[- ]?rna", re.I), "spatial CUT&Tag-RNA-seq"),
    (re.compile(r"^slide-?tags", re.I), "Slide-tags (spatial snATAC + snRNA)"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(REPO / "data" / "datasets.csv")))
    cols = list(rows[0])
    if "is_spatial" not in cols:
        cols.insert(cols.index("modality") + 1, "is_spatial")

    flags, mod_changes, renames = Counter(), [], []
    for r in rows:
        spatial, modality = classify(r["platform"], r["modality"], r["dataset_name"])
        r.setdefault("is_spatial", "")
        r["is_spatial"] = spatial
        flags[spatial] += 1
        if modality and modality != r["modality"]:
            mod_changes.append((r["dataset_id"], r["modality"] or "(blank)", modality))
            r["modality"] = modality
        for pat, better in RENAME:
            if r["platform"] and pat.search(r["platform"]) and r["platform"] != better:
                renames.append((r["dataset_id"], r["platform"], better))
                # Record what was replaced. A normalised platform column is for
                # grouping; the source's own wording is evidence and is not
                # recoverable once overwritten. It matters most where two
                # spellings mean different instruments -- "VisiumHD" against
                # "Visium" is one letter and a different machine.
                note = (
                    f"platform recorded by the source as {r['platform']!r}; normalised for grouping"
                )
                r["notes"] = f"{r['notes']}; {note}" if r.get("notes") else note
                r["platform"] = better
                break

    print(f"is_spatial: {dict(flags)}")
    print(
        f"modality set to spatial epigenomics: "
        f"{sum(1 for _, _, m in mod_changes if m == 'spatial epigenomics')}"
    )
    for d, old, new in mod_changes[:8]:
        print(f"    {d[:36]:38} {old[:24]:26} -> {new}")
    print(f"\nplatform renames: {len(renames)}")
    for d, old, new in renames[:8]:
        print(f"    {d[:36]:38} {old[:30]:32} -> {new}")

    if not args.apply:
        print("\ndry run — pass --apply to write")
        return
    with open(REPO / "data" / "datasets.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwritten: {len(rows)} rows, {len(cols)} columns")


if __name__ == "__main__":
    main()
