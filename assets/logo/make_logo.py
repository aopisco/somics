"""Generate the somics logo files.

The lowercase 's' is a hex-packed spatial-capture lattice split along its
anti-diagonal: capture spots (dots) on the upper-right, segmented cells with
nuclei on the lower-left — spot resolution vs single-cell resolution in one
glyph. Two niche clusters (orange, teal) sit on the arcs.

Usage: uv run python assets/logo/make_logo.py
Writes somics-mark.{svg,png} and somics-logo.{svg,png} next to this script.
"""

import math
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import PathPatch, Polygon
from matplotlib.textpath import TextPath
from matplotlib.transforms import Affine2D

OUTDIR = Path(__file__).resolve().parent

BLUE, ORANGE, TEAL, INK = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b"
LIGHT = {BLUE: "#b7d3f6", ORANGE: "#f7c4ab", TEAL: "#a9e3cd"}

prop = FontProperties(family="DejaVu Sans", weight="bold")
glyph = TextPath((0, 0), "s", size=100, prop=prop)
xmin, ymin = glyph.vertices.min(axis=0)
xmax, ymax = glyph.vertices.max(axis=0)
w, h = xmax - xmin, ymax - ymin
cx, cy = xmin + w / 2, ymin + h / 2

SPACING = 5.4
R = 2.05

pts = []
row = 0
yy = ymin + R
while yy <= ymax - R * 0.2:
    xoff = (SPACING / 2) if row % 2 else 0.0
    xx = xmin + R + xoff
    while xx <= xmax:
        pts.append((xx, yy))
        xx += SPACING
    yy += SPACING * math.sqrt(3) / 2
    row += 1

INSIDE = [p for p in pts if glyph.contains_point(p, radius=-R * 0.55)]

SEED_A = (xmin + 0.80 * w, ymin + 0.78 * h)
SEED_B = (xmin + 0.22 * w, ymin + 0.20 * h)
CLUSTER_R = 0.24 * max(w, h)


def color_for(p):
    if math.dist(p, SEED_A) < CLUSTER_R:
        return ORANGE
    if math.dist(p, SEED_B) < CLUSTER_R:
        return TEAL
    return BLUE


def is_cell_half(p):
    return (p[0] - cx) + (p[1] - cy) < 0


def blob(center, r0):
    k3, k5 = random.uniform(0.12, 0.22), random.uniform(0.06, 0.14)
    p3, p5 = random.uniform(0, 2 * math.pi), random.uniform(0, 2 * math.pi)
    verts = []
    for i in range(40):
        th = 2 * math.pi * i / 40
        r = r0 * (1 + k3 * math.sin(3 * th + p3) + k5 * math.sin(5 * th + p5))
        verts.append((center[0] + r * math.cos(th),
                      center[1] + r * math.sin(th)))
    return verts


def draw_glyph(ax, x0=0.0, y0=0.0):
    """Hybrid 's': spots upper-right, cells lower-left. Deterministic."""
    random.seed(42)
    for p in INSIDE:
        c = color_for(p)
        px, py = x0 + p[0] - xmin, y0 + p[1] - ymin
        if is_cell_half(p):
            jx = px + random.uniform(-0.5, 0.5)
            jy = py + random.uniform(-0.5, 0.5)
            ax.add_patch(Polygon(blob((jx, jy), R * 1.28), closed=True,
                                 facecolor=LIGHT[c], edgecolor=c, lw=0.9))
            nx = jx + random.uniform(-0.6, 0.6)
            ny = jy + random.uniform(-0.6, 0.6)
            ax.add_patch(plt.Circle((nx, ny), R * 0.45, color=c, lw=0))
        else:
            ax.add_patch(plt.Circle((px, py), R, color=c, lw=0))


def save(fig, stem):
    for ext in ("svg", "png"):
        fig.savefig(OUTDIR / f"{stem}.{ext}", transparent=True,
                    bbox_inches="tight", pad_inches=0.02, dpi=300)
    plt.close(fig)
    print("saved", stem, "(svg, png)")


# --- standalone mark ---
fig, ax = plt.subplots(figsize=(3, 3))
pad = 6
side = max(w, h) + 2 * pad
ax.set_xlim(-(side - w) / 2, w + (side - w) / 2)
ax.set_ylim(-(side - h) / 2, h + (side - h) / 2)
draw_glyph(ax)
ax.set_aspect("equal")
ax.axis("off")
save(fig, "somics-mark")

# --- lockups: mark + "omics" wordmark (ink for light bg, white for dark) ---
def lockup(stem, ink):
    fig, ax = plt.subplots(figsize=(8, 2.6))
    draw_glyph(ax)
    GAP = 6
    LETTER_SPACE = 4.5
    cur = w + GAP
    for ch in "omics":
        tp = TextPath((0, 0), ch, size=100, prop=prop)
        cxmin = tp.vertices[:, 0].min()
        cxmax = tp.vertices[:, 0].max()
        t = Affine2D().translate(cur - cxmin, 0)
        ax.add_patch(PathPatch(tp.transformed(t), color=ink, lw=0))
        cur += (cxmax - cxmin) + LETTER_SPACE
    ax.set_xlim(-pad, cur + pad)
    ax.set_ylim(-pad, h * 1.35 + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    save(fig, stem)


lockup("somics-logo", INK)
lockup("somics-logo-dark", "#ffffff")  # for dark backgrounds (slides, dark mode)
