"""Bars: datasets per publication year of their original publication.

Reads data/datasets.csv; writes analysis/plots/datasets_by_year.png. The year is
the original publication's year — the paper that first released the data — so
this is a view of when the underlying data appeared, not when we harvested it.
Rows with no resolved original year are excluded and reported in the subtitle.
"""

import csv
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "plots" / "datasets_by_year.png"

SURFACE = "#fcfcfb"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BAR = "#2a78d6"
FIRST_YEAR = 2016  # Ståhl et al. introduce spatial transcriptomics in 2016


def main():
    with open(REPO / "data" / "datasets.csv") as f:
        rows = list(csv.DictReader(f))

    years = Counter()
    undated = 0
    early = 0
    for r in rows:
        y = r["original_publication_year"].strip()
        if not y.isdigit():
            undated += 1
            continue
        y = int(y)
        if y < FIRST_YEAR:
            early += 1
            continue
        years[y] += 1

    span = sorted(years)
    vals = [years[y] for y in span]

    fig, ax = plt.subplots(figsize=(9.2, 4.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.bar(span, vals, width=0.68, color=BAR, edgecolor=SURFACE, linewidth=1.2)
    for x, v in zip(span, vals, strict=True):
        ax.text(x, v + max(vals) * 0.02, f"{v}", ha="center", va="bottom", fontsize=8.5, color=INK2)

    ax.set_xticks(span)
    ax.set_xticklabels([str(y) for y in span], fontsize=9, color=INK2)
    ax.tick_params(axis="y", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.set_ylim(0, max(vals) * 1.15)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    ax.set_title(
        "somics dataset registry: datasets by year of original publication",
        fontsize=13,
        color=INK,
        loc="left",
        pad=14,
        fontweight="bold",
    )
    ax.text(
        0,
        1.03,
        f"data/datasets.csv · {sum(vals):,} of {len(rows):,} datasets shown · "
        f"{undated:,} have no resolved original year, {early} predate {FIRST_YEAR}",
        transform=ax.transAxes,
        fontsize=8.5,
        color=MUTED,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
