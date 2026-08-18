"""Stacked bars: datasets per technology, colored by model-paper reuse.

Reads data/datasets.csv and data/model_dataset_usage.csv; writes
analysis/plots/technologies_by_model_reuse.png. Color encodes how many *named*
model papers use each dataset (none / 1 / 2-4 / 5+), as an ordered ramp with
grey for the no-model group.

"Named model" matters here. Every dataset has at least one row in
model_dataset_usage.csv, because the paper that reported it becomes a usage
row — so counting raw usage rows would say every dataset is used by a model,
which is false. A usage row counts only when its `model` field carries an
actual model name (TERRA, VirTues, Thor...) rather than the fallback paper id,
which is how the merge records a plain analysing paper.

Platforms are canonicalized by keyword; technologies outside the top 14 fold
into "Other" so every dataset is counted.
"""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "plots" / "technologies_by_model_reuse.png"

SURFACE = "#fcfcfb"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BUCKETS = [
    ("No model paper", "#c3c2b7"),
    ("1 model paper", "#86b6ef"),
    ("2–4 model papers", "#2a78d6"),
    ("5+ model papers", "#104281"),
]

# A usage row whose `model` is a bare paper id (PMC…, bio_…, or a raw uuid) is
# an analysing paper, not a named model.
UNNAMED_MODEL = re.compile(r"^(PMC\d+|bio_[a-f0-9]+|arx_|med_|[0-9a-f]{8}-)")

TECH_RULES = [
    ("10x Visium HD", ("visium hd",)),
    ("10x Visium", ("visium",)),
    ("10x Xenium", ("xenium",)),
    ("MERFISH/MERSCOPE", ("merfish", "merscope")),
    ("CosMx", ("cosmx",)),
    ("Stereo-seq", ("stereo-seq", "stereoseq")),
    ("Slide-seq", ("slide-seq", "slideseq")),
    ("seqFISH", ("seqfish",)),
    ("ST (original)", ("spatial transcriptomics (st)", "st (", "legacy st", "original st")),
    ("GeoMx DSP", ("geomx",)),
    ("CODEX/PhenoCycler", ("codex", "phenocycler")),
    ("IMC", ("imc", "imaging mass cytometry")),
    ("MIBI", ("mibi",)),
    ("ISS/STARmap", ("iss", "in situ sequencing", "starmap", "hybriss")),
    ("smFISH/ISH", ("smfish", "osmfish", "fish", "ish")),
    ("Mass spectrometry", ("mass spec", "maldi", "sims", "desi")),
    ("scRNA-seq (ref)", ("scrna", "snrna", "single-cell rna", "single cell rna", "chromium")),
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
            if not UNNAMED_MODEL.match(r["model"]):
                n_models[r["dataset_id"]] += 1

    with open(REPO / "data" / "datasets.csv") as f:
        rows = list(csv.DictReader(f))

    def bucket(ds_id):
        n = n_models.get(ds_id, 0)
        if n == 0:
            return 0
        return 1 if n == 1 else (2 if n <= 4 else 3)

    counts = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        counts[tech(r["platform"])][bucket(r["dataset_id"])] += 1

    named = [t for t in counts if t not in ("Other", "Not specified")]
    top = sorted(named, key=lambda t: sum(counts[t]), reverse=True)[:14]
    for t in named:
        if t not in top:
            for b in range(len(BUCKETS)):
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
        ax.barh(
            y,
            vals,
            left=left,
            height=0.62,
            color=color,
            edgecolor=SURFACE,
            linewidth=1.4,
            label=label,
        )
        left = [acc + v for acc, v in zip(left, vals, strict=True)]

    for i, t in enumerate(order):
        total = sum(counts[t])
        reused = counts[t][2] + counts[t][3]
        pct = round(100 * reused / total) if total else 0
        ax.text(
            total + 5,
            i,
            f"{total:,}  ({pct}% reused)",
            va="center",
            ha="left",
            fontsize=8.5,
            color=INK2,
        )

    ax.set_yticks(list(y))
    ax.set_yticklabels(order, fontsize=10, color=INK2)
    ax.tick_params(axis="x", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(sum(counts[t]) for t in order) * 1.28)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    ax.set_title(
        "somics dataset registry: datasets per technology,\n"
        "colored by how many named model papers use them",
        fontsize=13,
        color=INK,
        loc="left",
        pad=16,
        fontweight="bold",
    )
    ax.text(
        0,
        1.012,
        f"data/datasets.csv × model_dataset_usage.csv · "
        f"{len(rows):,} datasets, {shown:,} shown · platform canonicalized "
        "by keyword · reuse counts named models only",
        transform=ax.transAxes,
        fontsize=8.5,
        color=MUTED,
    )

    totals = [sum(counts[t][b] for t in counts) for b in range(len(BUCKETS))]
    handles, labels = ax.get_legend_handles_labels()
    labels = [f"{lab}  ({totals[i]:,})" for i, lab in enumerate(labels)]
    # Below the axes: the no-model group makes the lower bars long enough that a
    # legend inside the plot lands on top of their value labels.
    ax.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.07),
        ncol=len(BUCKETS),
        frameon=False,
        fontsize=9,
        labelcolor=INK2,
        handlelength=1.0,
        handleheight=1.0,
        borderaxespad=0.0,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
