"""Stacked bars: datasets per technology, colored by model-paper reuse.

Reads data/datasets.csv and data/model_dataset_usage.csv; writes
analysis/plots/technologies_by_model_reuse.png. Color encodes how many model
papers use each dataset (1 / 2-4 / 5+), as an ordered light-to-dark ramp.
Platforms are canonicalized by keyword; technologies outside the top 14 fold
into "Other" so every dataset is counted.
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "plots" / "technologies_by_model_reuse.png"

SURFACE = "#fcfcfb"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BUCKETS = [("1 model paper", "#86b6ef"), ("2–4 model papers", "#2a78d6"),
           ("5+ model papers", "#104281")]

TECH_RULES = [
    ("10x Visium HD", ("visium hd",)),
    ("10x Visium", ("visium",)),
    ("10x Xenium", ("xenium",)),
    ("MERFISH/MERSCOPE", ("merfish", "merscope")),
    ("CosMx", ("cosmx",)),
    ("Stereo-seq", ("stereo-seq", "stereoseq")),
    ("Slide-seq", ("slide-seq", "slideseq")),
    ("seqFISH", ("seqfish",)),
    ("ST (original)", ("spatial transcriptomics (st)", "st (", "legacy st",
                       "original st")),
    ("GeoMx DSP", ("geomx",)),
    ("CODEX/PhenoCycler", ("codex", "phenocycler")),
    ("IMC", ("imc", "imaging mass cytometry")),
    ("MIBI", ("mibi",)),
    ("ISS/STARmap", ("iss", "in situ sequencing", "starmap", "hybriss")),
    ("smFISH/ISH", ("smfish", "osmfish", "fish", "ish")),
    ("Mass spectrometry", ("mass spec", "maldi", "sims", "desi")),
    ("scRNA-seq (ref)", ("scrna", "snrna", "single-cell rna",
                         "single cell rna", "chromium")),
]


def tech(platform):
    p = platform.strip().lower()
    if not p:
        return "Not specified"
    if p == "st" or p.startswith("st ") or "spatial transcriptomics" in p:
        return "ST (original)"
    for name, keys in TECH_RULES:
        if any(k in p for k in keys):
            return name
    return "Other"


def main():
    with open(REPO / "data" / "model_dataset_usage.csv") as f:
        n_models = Counter()
        for r in csv.DictReader(f):
            n_models[r["dataset_id"]] += 1

    with open(REPO / "data" / "datasets.csv") as f:
        rows = list(csv.DictReader(f))

    def bucket(ds_id):
        n = n_models.get(ds_id, 0)
        return 0 if n <= 1 else (1 if n <= 4 else 2)

    counts = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        counts[tech(r["platform"])][bucket(r["dataset_id"])] += 1

    named = [t for t in counts if t not in ("Other", "Not specified")]
    top = sorted(named, key=lambda t: sum(counts[t]), reverse=True)[:14]
    for t in named:
        if t not in top:
            for b in range(3):
                counts["Other"][b] += counts[t][b]
    order = top + [t for t in ("Other", "Not specified") if t in counts]
    shown = sum(sum(counts[t]) for t in order)
    order = order[::-1]

    fig, ax = plt.subplots(figsize=(9.2, 6.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    y = range(len(order))
    left = [0.0] * len(order)
    for bi, (label, color) in enumerate(BUCKETS):
        vals = [counts[t][bi] for t in order]
        ax.barh(y, vals, left=left, height=0.62, color=color,
                edgecolor=SURFACE, linewidth=1.4, label=label)
        left = [l + v for l, v in zip(left, vals)]

    for i, t in enumerate(order):
        total = sum(counts[t])
        reused = counts[t][1] + counts[t][2]
        pct = round(100 * reused / total) if total else 0
        ax.text(total + 5, i, f"{total:,}  ({pct}% reused)", va="center",
                ha="left", fontsize=8.5, color=INK2)

    ax.set_yticks(list(y))
    ax.set_yticklabels(order, fontsize=10, color=INK2)
    ax.tick_params(axis="x", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(sum(counts[t]) for t in order) * 1.28)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    ax.set_title("somics dataset registry: datasets per technology,\n"
                 "colored by how many model papers use them",
                 fontsize=13, color=INK, loc="left", pad=16, fontweight="bold")
    ax.text(0, 1.012, f"data/datasets.csv × model_dataset_usage.csv · "
            f"{len(rows):,} datasets, {shown:,} shown · platform canonicalized "
            "by keyword", transform=ax.transAxes, fontsize=8.5, color=MUTED)

    totals = [sum(counts[t][b] for t in counts) for b in range(3)]
    handles, labels = ax.get_legend_handles_labels()
    labels = [f"{l}  ({totals[i]:,})" for i, l in enumerate(labels)]
    ax.legend(handles, labels, loc="lower right", frameon=False, fontsize=9,
              labelcolor=INK2, handlelength=1.0, handleheight=1.0,
              borderaxespad=0.2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
