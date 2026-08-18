"""Stacked bars: datasets by organism, colored by modality.

Reads data/datasets.csv; writes analysis/plots/datasets_by_organism_by_modality.png.
Modality is taken from the `modality` column where present and inferred from
platform keywords where blank; residual unknowns stay gray "Not specified".
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "plots" / "datasets_by_organism_by_modality.png"

SURFACE = "#fcfcfb"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
COLORS = {
    "Spatial transcriptomics": "#2a78d6",
    "Spatial proteomics": "#eb6834",
    "Reference (sc/bulk)": "#1baf7a",
    "Other spatial": "#eda100",
    "Not specified": "#c3c2b7",
}
MODALITIES = list(COLORS)

SP_KEYS = (
    "codex",
    "imc",
    "imaging mass cytometry",
    "mibi",
    "cycif",
    "4i",
    "phenocycler",
    "geomx",
    "lopit",
    "mass spec",
    "maldi",
    "ims",
    "immunofluorescence",
    "ihc",
    "proteom",
)
ST_KEYS = (
    "visium",
    "xenium",
    "merfish",
    "merscope",
    "cosmx",
    "seqfish",
    "slide-seq",
    "slideseq",
    "stereo-seq",
    "stereoseq",
    "dbit",
    "hdst",
    "starmap",
    "iss",
    "in situ sequencing",
    "fish",
    "ish",
    "tomo-seq",
    "spatial transcriptom",
    "st",
    "hybriss",
    "expansion",
)
REF_KEYS = (
    "scrna",
    "snrna",
    "single-cell",
    "single cell",
    "10x chromium",
    "smart-seq",
    "smartseq",
    "drop-seq",
    "bulk",
    "rna-seq",
    "rnaseq",
    "cite-seq",
    "atac",
)
EPI_KEYS = ("epigenom", "cut&tag", "cut&run", "spatial-atac", "spatial atac", "methyl", "perturb")


def modality(row):
    m = row["modality"].strip().lower()
    if m == "spatial transcriptomics":
        return "Spatial transcriptomics"
    if m == "spatial proteomics":
        return "Spatial proteomics"
    p = row["platform"].strip().lower()
    if p:
        if any(k in p for k in EPI_KEYS):
            return "Other spatial"
        if any(k in p for k in SP_KEYS):
            return "Spatial proteomics"
        if any(k in p for k in REF_KEYS) and not any(k in p for k in ST_KEYS):
            return "Reference (sc/bulk)"
        if any(k in p for k in ST_KEYS):
            return "Spatial transcriptomics"
    return "Not specified"


def organism(row):
    s = row["species"].strip().lower()
    if not s:
        return "Not specified"
    both = ("human" in s or "homo" in s) and ("mouse" in s or "mus " in s or "mice" in s)
    if both:
        return "Human & Mouse"
    if "human" in s or "homo sapiens" in s:
        return "Human"
    if "mouse" in s or "mus musculus" in s or s == "mice":
        return "Mouse"
    if "drosophila" in s:
        return "Drosophila"
    if "zebrafish" in s or "danio" in s:
        return "Zebrafish"
    if "elegans" in s:
        return "C. elegans"
    if s.startswith("rat") and "arab" not in s:
        return "Rat"
    if "arabidopsis" in s:
        return "Arabidopsis"
    return "Other"


def main():
    with open(REPO / "data" / "datasets.csv") as f:
        rows = list(csv.DictReader(f))

    counts = defaultdict(Counter)
    for r in rows:
        counts[organism(r)][modality(r)] += 1

    named = [o for o in counts if o not in ("Other", "Not specified")]
    order = sorted(named, key=lambda o: sum(counts[o].values()), reverse=True)
    order += [o for o in ("Other", "Not specified") if o in counts]
    order = order[::-1]

    fig, ax = plt.subplots(figsize=(9.2, 5.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    y = range(len(order))
    left = [0.0] * len(order)
    for mod in MODALITIES:
        vals = [counts[o].get(mod, 0) for o in order]
        ax.barh(
            y,
            vals,
            left=left,
            height=0.62,
            color=COLORS[mod],
            edgecolor=SURFACE,
            linewidth=1.4,
            label=mod,
        )
        left = [acc + v for acc, v in zip(left, vals, strict=True)]

    for i, o in enumerate(order):
        total = sum(counts[o].values())
        ax.text(total + 8, i, f"{total:,}", va="center", ha="left", fontsize=9, color=INK2)

    ax.set_yticks(list(y))
    ax.set_yticklabels(order, fontsize=10, color=INK2)
    ax.tick_params(axis="x", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(sum(counts[o].values()) for o in order) * 1.10)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    ax.set_title(
        "somics dataset registry: datasets by organism and modality",
        fontsize=13,
        color=INK,
        loc="left",
        pad=14,
        fontweight="bold",
    )
    ax.text(
        0,
        1.015,
        f"data/datasets.csv · {len(rows):,} datasets · modality "
        "inferred from platform where unlabeled",
        transform=ax.transAxes,
        fontsize=8.5,
        color=MUTED,
    )

    totals = Counter()
    for c in counts.values():
        totals.update(c)
    handles, labels = ax.get_legend_handles_labels()
    labels = [f"{lab}  ({totals[lab]:,})" for lab in labels]
    ax.legend(
        handles,
        labels,
        loc="lower right",
        frameon=False,
        fontsize=9,
        labelcolor=INK2,
        handlelength=1.0,
        handleheight=1.0,
        borderaxespad=0.2,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
