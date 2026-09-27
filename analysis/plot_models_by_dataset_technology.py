"""Which models use which datasets: the most-used named models, one bar each.

Reads data/model_dataset_usage.csv and data/datasets.csv; writes
analysis/plots/models_by_dataset_technology.png. Each bar is one named model,
its length the number of distinct registry datasets the model paper trains or
evaluates on, segmented by the technology of those datasets (canonicalized with
the same keyword rules as plot_technologies_by_model_reuse.py).

"Named" is the same heuristic as the reuse chart: a usage row counts when its
`model` field is a name (TERRA, VirTues...) and not the fallback paper id. The
name comes from a `Name:` prefix in the paper title, so it also admits named
resources that are not models (databases such as SPASCER or SpatialDB).
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_technologies_by_model_reuse import GRID, INK, INK2, MUTED, SURFACE, UNNAMED_MODEL, tech

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "plots" / "models_by_dataset_technology.png"
TOP_MODELS = 30

# Technology groups, most used first; everything else folds into "Other".
GROUPS = [
    ("10x Visium", "#2a78d6"),
    ("10x Visium HD", "#86b6ef"),
    ("10x Xenium", "#e07b39"),
    ("MERFISH/MERSCOPE", "#3a9e6f"),
    ("ST (original)", "#8e6bbf"),
    ("Slide-seq", "#d4a72c"),
    ("Stereo-seq", "#c9547a"),
    ("CODEX/PhenoCycler", "#4fb3bf"),
    ("IMC", "#7a8b3a"),
    ("scRNA-seq (ref)", "#a3a29a"),
    ("Other", "#d6d5cd"),
]


def main():
    with open(REPO / "data" / "datasets.csv") as f:
        platform = {r["dataset_id"]: r["platform"] for r in csv.DictReader(f)}

    uses = defaultdict(set)
    with open(REPO / "data" / "model_dataset_usage.csv") as f:
        for r in csv.DictReader(f):
            if not UNNAMED_MODEL.match(r["model"]):
                uses[r["model"]].add(r["dataset_id"])

    names = [g for g, _ in GROUPS]
    by_model = {}
    for model, ds in uses.items():
        c = Counter()
        for d in ds:
            t = tech(platform.get(d, ""))
            c[t if t in names else "Other"] += 1
        by_model[model] = c
    top = sorted(by_model, key=lambda m: (-sum(by_model[m].values()), m.lower()))[:TOP_MODELS]
    order = top[::-1]

    fig, ax = plt.subplots(figsize=(9.2, 8.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    y = range(len(order))
    left = [0] * len(order)
    for g, color in GROUPS:
        vals = [by_model[m][g] for m in order]
        ax.barh(y, vals, left=left, height=0.66, color=color, edgecolor=SURFACE, linewidth=1.2, label=g)
        left = [a + v for a, v in zip(left, vals, strict=True)]
    for i, m in enumerate(order):
        ax.text(left[i] + 0.4, i, f"{left[i]}", va="center", ha="left", fontsize=8.5, color=INK2)

    ax.set_yticks(list(y))
    ax.set_yticklabels(order, fontsize=9.5, color=INK2)
    ax.tick_params(axis="x", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(left) * 1.1)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    ax.set_title(
        f"Which models use which datasets: the {TOP_MODELS} most-used named models,\n"
        "datasets each uses, by technology",
        fontsize=13,
        color=INK,
        loc="left",
        pad=16,
        fontweight="bold",
    )
    n_rows = sum(len(v) for v in uses.values())
    ax.text(
        0,
        1.008,
        f"data/model_dataset_usage.csv · {len(uses)} named models, {n_rows:,} model×dataset links · "
        "named by title prefix, so databases count too",
        transform=ax.transAxes,
        fontsize=8.5,
        color=MUTED,
    )
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.45, -0.04),
        ncol=4,
        frameon=False,
        fontsize=8.5,
        labelcolor=INK2,
        handlelength=1.0,
        handleheight=1.0,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
