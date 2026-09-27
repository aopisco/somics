"""What the ingested atlas holds: observations per technology, by species.

Reads the viewer index's samples.json (one record per section; written by
scripts/build_viewer_cache.py under s3://somics-dev/viewer_cache/<stamp>/) and
writes analysis/plots/atlas_composition.png. Unlike the other plots here this
describes the atlas, not the registry.

    aws s3 cp s3://somics-dev/viewer_cache/<stamp>/samples.json /tmp/samples.json
    uv run --with matplotlib python analysis/plot_atlas_composition.py /tmp/samples.json

Species comes from `organism`; the record's `species` field is the 3D body
model the viewer pins a section onto (mouse and macaque use the rat body).
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_technologies_by_model_reuse import GRID, INK, INK2, MUTED, SURFACE

OUT = Path(__file__).resolve().parent / "plots" / "atlas_composition.png"

SPECIES = [
    ("Homo sapiens", "human", "#0a2540"),
    ("Mus musculus", "mouse", "#0279ee"),
    ("Macaca mulatta", "macaque", "#0e7490"),
    ("Rattus norvegicus", "rat", "#86b6ef"),
    ("Danio rerio", "zebrafish", "#e07b39"),
]
TECH_LABEL = {
    "stereo_seq": "Stereo-seq",
    "xenium": "Xenium",
    "visium_hd": "Visium HD",
    "merfish": "MERFISH / MERSCOPE",
    "codex": "CODEX (SPRM)",
    "phenocycler": "PhenoCycler (SPRM)",
    "cosmx": "CosMx",
    "mibi": "MIBI",
    "visium": "Visium",
    "atera": "Atera",
    "seqfish": "seqFISH",
}


def main(path: str):
    samples = json.load(open(path))
    obs = defaultdict(Counter)
    sections = Counter()
    for s in samples:
        obs[s["technology"]][s["organism"]] += s["n_cells"]
        sections[s["technology"]] += 1
    order = sorted(obs, key=lambda t: sum(obs[t].values()))
    total = sum(sum(c.values()) for c in obs.values())

    fig, ax = plt.subplots(figsize=(9.2, 5.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    y = range(len(order))
    left = [0.0] * len(order)
    for org, label, color in SPECIES:
        vals = [obs[t][org] / 1e6 for t in order]
        ax.barh(y, vals, left=left, height=0.64, color=color, edgecolor=SURFACE, linewidth=1.2, label=label)
        left = [a + v for a, v in zip(left, vals, strict=True)]
    for i, t in enumerate(order):
        m = left[i]
        ax.text(m + 0.3, i, f"{m:.2f}M · {sections[t]} section{'s' * (sections[t] != 1)}" if m < 1 else f"{m:.1f}M · {sections[t]} sections",
                va="center", ha="left", fontsize=8.5, color=INK2)

    ax.set_yticks(list(y))
    ax.set_yticklabels([TECH_LABEL.get(t, t) for t in order], fontsize=10, color=INK2)
    ax.tick_params(axis="x", labelsize=9, colors=MUTED, length=0)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(left) * 1.32)
    ax.set_xlabel("observations (millions: cells, bins or spots)", fontsize=9, color=MUTED)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_title("The somics atlas: observations per technology, by species", fontsize=13, color=INK,
                 loc="left", pad=16, fontweight="bold")
    ax.text(0, 1.012, f"{len(samples):,} sections · {total / 1e6:.1f}M observations · {len(obs)} technologies",
            transform=ax.transAxes, fontsize=8.5, color=MUTED)
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK2, handlelength=1.0, handleheight=1.0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/samples.json")
