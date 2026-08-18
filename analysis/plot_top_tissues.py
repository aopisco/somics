"""Bars: the most represented tissues, split by modality.

Reads data/datasets.csv; writes analysis/plots/top_tissues.png. Tissue is free
text in the registry ("colon", "dorsolateral prefrontal cortex", "lung
adenocarcinoma"), so labels are lowercased, stripped of a disease suffix where
one is obvious, and matched to a small keyword map. Anything unmatched is left
out of the chart rather than forced into a bucket it may not belong to; the
subtitle reports how much that is.
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "plots" / "top_tissues.png"

SURFACE = "#fcfcfb"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
COLORS = {
    "Spatial transcriptomics": "#2a78d6",
    "Spatial proteomics": "#eb6834",
    "Other / unclassified": "#c3c2b7",
}

TISSUE_KEYS = [
    (
        "Brain / CNS",
        (
            "brain",
            "cortex",
            "cortical",
            "hippocamp",
            "cerebell",
            "hypothalam",
            "olfactory bulb",
            "spinal",
            "neural",
            "midbrain",
            "striat",
        ),
    ),
    ("Breast", ("breast", "mammary")),
    ("Lung", ("lung", "pulmonary", "alveol", "airway", "bronch")),
    ("Liver", ("liver", "hepat")),
    ("Kidney", ("kidney", "renal", "nephron")),
    ("Intestine / colon", ("colon", "intestin", "ileum", "gut", "colorect", "appendix", "rect")),
    ("Heart", ("heart", "cardiac", "myocard")),
    ("Embryo / development", ("embryo", "fetal", "organogenesis", "developing")),
    ("Skin", ("skin", "epiderm", "dermis", "melanoma")),
    ("Pancreas", ("pancrea",)),
    ("Lymphoid / immune", ("lymph", "spleen", "thymus", "tonsil", "bone marrow", "pbmc")),
    ("Prostate / repro", ("prostate", "ovar", "uter", "testis", "placenta", "endometri")),
    ("Stomach / esophagus", ("stomach", "gastric", "esophag")),
    ("Eye / retina", ("retina", "eye")),
    ("Muscle / bone", ("muscle", "skeletal", "bone", "cartilage")),
]

PROT = ("codex", "imc", "imaging mass", "mibi", "cycif", "phenocycler", "geomx", "proteom")
TRAN = (
    "visium",
    "xenium",
    "merfish",
    "cosmx",
    "seqfish",
    "slide",
    "stereo",
    "starmap",
    "dbit",
    "iss",
    "fish",
    "transcriptom",
    "curio",
    "hdst",
)


def tissue(raw):
    s = (raw or "").strip().lower()
    if not s:
        return None
    for name, keys in TISSUE_KEYS:
        if any(k in s for k in keys):
            return name
    return None


def modality(row):
    m = (row["modality"] or "").strip().lower()
    if m == "spatial transcriptomics":
        return "Spatial transcriptomics"
    if m == "spatial proteomics":
        return "Spatial proteomics"
    p = (row["platform"] or "").lower()
    if any(k in p for k in PROT):
        return "Spatial proteomics"
    if any(k in p for k in TRAN):
        return "Spatial transcriptomics"
    return "Other / unclassified"


def main():
    with open(REPO / "data" / "datasets.csv") as f:
        rows = list(csv.DictReader(f))

    counts = defaultdict(Counter)
    unmatched = 0
    for r in rows:
        t = tissue(r["tissue"])
        if t is None:
            unmatched += 1
            continue
        counts[t][modality(r)] += 1

    order = sorted(counts, key=lambda t: sum(counts[t].values()))
    shown = sum(sum(c.values()) for c in counts.values())

    fig, ax = plt.subplots(figsize=(9.2, 6.0), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    y = range(len(order))
    left = [0.0] * len(order)
    for mod, color in COLORS.items():
        vals = [counts[t][mod] for t in order]
        ax.barh(
            y,
            vals,
            left=left,
            height=0.62,
            color=color,
            edgecolor=SURFACE,
            linewidth=1.4,
            label=mod,
        )
        left = [acc + v for acc, v in zip(left, vals, strict=True)]

    for i, t in enumerate(order):
        total = sum(counts[t].values())
        ax.text(total + 4, i, f"{total:,}", va="center", ha="left", fontsize=8.5, color=INK2)

    ax.set_yticks(list(y))
    ax.set_yticklabels(order, fontsize=10, color=INK2)
    ax.tick_params(axis="x", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(sum(c.values()) for c in counts.values()) * 1.18)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    ax.set_title(
        "somics dataset registry: most represented tissues,\ncolored by modality",
        fontsize=13,
        color=INK,
        loc="left",
        pad=16,
        fontweight="bold",
    )
    ax.text(
        0,
        1.012,
        f"data/datasets.csv · {shown:,} of {len(rows):,} datasets shown · "
        f"{unmatched:,} tissues unmatched by the keyword map and left out",
        transform=ax.transAxes,
        fontsize=8.5,
        color=MUTED,
    )
    ax.legend(
        loc="lower right",
        frameon=False,
        fontsize=9,
        labelcolor=INK2,
        handlelength=1.0,
        handleheight=1.0,
        borderaxespad=0.4,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
