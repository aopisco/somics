"""Spatial omics modalities: resolution against plex, and which the atlas holds.

Writes analysis/plots/modality_landscape.png. One point per platform at its
typical published resolution (um; the capture unit for arrays, the imaging
resolution for in situ methods) and the number of features it measures (genes,
proteins, m/z species, or genome-wide peaks). Values are order-of-magnitude
typicals for orientation, not specifications. Filled = in the atlas;
open = in the registry but not yet ingested.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_technologies_by_model_reuse import GRID, INK, INK2, MUTED, SURFACE

OUT = Path(__file__).resolve().parent / "plots" / "modality_landscape.png"

FAMILIES = {
    "Sequencing-based capture": "#0279ee",
    "Imaging-based transcriptomics": "#0e7490",
    "Multiplexed protein imaging": "#e07b39",
    "Mass spectrometry imaging": "#8e6bbf",
    "Spatial epigenomics": "#3a9e6f",
}
# (label, family, resolution_um, features, in_atlas, label dx, label dy)
PLATFORMS = [
    ("ST (legacy)", "Sequencing-based capture", 100, 18000, False, 1.12, 1.0),
    ("Visium", "Sequencing-based capture", 55, 18000, True, 1.12, 1.25),
    ("Slide-seq", "Sequencing-based capture", 10, 18000, False, 1.12, 0.72),
    ("Visium HD", "Sequencing-based capture", 2, 18000, True, 1.12, 1.25),
    ("Stereo-seq", "Sequencing-based capture", 0.5, 25000, True, 1.12, 1.15),
    ("GeoMx (ROI)", "Sequencing-based capture", 300, 18000, False, 0.8, 0.68),
    ("Atera", "Imaging-based transcriptomics", 0.2, 18000, True, 1.12, 0.72),
    ("Xenium", "Imaging-based transcriptomics", 0.2, 5000, True, 1.12, 1.0),
    ("CosMx", "Imaging-based transcriptomics", 0.12, 1000, True, 1.12, 0.74),
    ("MERFISH / MERSCOPE", "Imaging-based transcriptomics", 0.1, 500, True, 1.12, 0.78),
    ("STARmap", "Imaging-based transcriptomics", 0.25, 1300, False, 1.12, 1.3),
    ("seqFISH", "Imaging-based transcriptomics", 0.1, 50, True, 1.12, 1.0),
    ("CODEX / PhenoCycler", "Multiplexed protein imaging", 0.4, 55, True, 1.12, 1.45),
    ("MIBI", "Multiplexed protein imaging", 0.6, 35, True, 1.12, 0.85),
    ("IMC", "Multiplexed protein imaging", 1, 40, False, 1.15, 1.0),
    ("Cell DIVE", "Multiplexed protein imaging", 0.28, 70, False, 0.88, 1.35),
    ("MALDI", "Mass spectrometry imaging", 20, 1000, False, 1.12, 1.0),
    ("DESI", "Mass spectrometry imaging", 100, 1000, False, 1.12, 1.0),
    ("spatial ATAC / CUT&Tag", "Spatial epigenomics", 20, 100000, False, 1.12, 1.0),
]


def main():
    fig, ax = plt.subplots(figsize=(9.2, 5.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for label, fam, res, feat, ingested, dx, dy in PLATFORMS:
        c = FAMILIES[fam]
        ax.scatter(res, feat, s=110, color=c if ingested else SURFACE, edgecolor=c, linewidth=1.8, zorder=3)
        ax.text(res * dx, feat * dy, label, fontsize=8.5, color=INK2, va="center",
                ha="left" if dx >= 1 else "right", zorder=4)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(0.06, 1500)
    ax.set_ylim(20, 300000)
    ax.set_xlabel("spatial resolution (µm, log) — finer ←", fontsize=9.5, color=INK2)
    ax.set_ylabel("features measured per location (log)", fontsize=9.5, color=INK2)
    ax.tick_params(labelsize=8.5, colors=MUTED, length=0)
    ax.grid(True, color=GRID, linewidth=0.8, which="major")
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.axvspan(0.06, 1.5, color="#f1f6f8", zorder=0)
    ax.text(0.07, 25, "single-cell / subcellular", fontsize=8, color=MUTED, style="italic")
    ax.set_title("Spatial omics modalities: resolution against plex", fontsize=13, color=INK,
                 loc="left", pad=16, fontweight="bold")
    ax.text(0, 1.012, "typical published values, order of magnitude · filled = in the somics atlas · open = registered, not yet ingested",
            transform=ax.transAxes, fontsize=8.5, color=MUTED)
    handles = [plt.Line2D([], [], marker="o", ls="", markersize=8, color=c, label=f) for f, c in FAMILIES.items()]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False,
              fontsize=8.5, labelcolor=INK2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
