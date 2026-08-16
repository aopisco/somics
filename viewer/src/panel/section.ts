/**
 * The maths behind the section's 2D plot in the floating panel: how the server's normalized point
 * coordinates land on a canvas, how a canvas pixel maps back to a micron, and what colour and size
 * each point gets.
 *
 * Pure and DOM-free on purpose. `SectionView` owns the canvas; everything decidable without one
 * lives here so it can be tested, and so the pixel to micron round trip that drives the morphology
 * view is a function with a name rather than four lines inside an event handler.
 *
 * Coordinate frames, innermost out:
 *   micron    — the section's own frame, as the atlas measured it. y runs up.
 *   normalized— what `/points` returns: (x_um - centre) / scale_um, so +-1 on the long axis. y up.
 *   CSS px    — the canvas element's box. y runs *down*; the fit flips it.
 *   backing px— CSS px times the device pixel ratio. Only the draw loop sees these.
 */

import type { GeneValues, Paint, PointCloud, PointMeta } from "../types";
import { magma, normalize, viridis } from "./colormap";

/**
 * Where a section's normalized square sits on a canvas, and what it takes to get back to microns.
 * Everything here is in CSS pixels; the draw loop multiplies by the device pixel ratio itself.
 */
export interface SectionFit {
  /** The canvas box this fit was computed for. */
  width: number;
  height: number;
  /** CSS pixels per unit of normalized position. Zero when there is nothing drawable. */
  scale: number;
  /** Where normalized (0, 0) — the middle of the section — lands. */
  originX: number;
  originY: number;
  /** The tissue's own footprint on the canvas, centred on the origin. */
  drawWidth: number;
  drawHeight: number;
  /** Microns per unit of normalized position, and the centre those units are measured from. */
  scaleUm: number;
  centreXUm: number;
  centreYUm: number;
}

/**
 * Fit a section's extent into a canvas box, preserving aspect and centring what is left over.
 *
 * A degenerate extent (zero width or height), a zero-sized canvas, or a padding that eats the whole
 * box all yield `scale: 0`: the mapping is still well defined and finite, there is simply nothing to
 * draw. Callers check `scale` rather than guessing at NaN.
 */
export function fitSection(
  meta: PointMeta,
  width: number,
  height: number,
  padding = 0,
): SectionFit {
  const [xMin, yMin, xMax, yMax] = meta.extent_um;
  // A non-positive scale_um would make every micron conversion infinite. The server should never
  // send one; if it does, fall back to 1 so the plot still draws and only the caption is wrong.
  const scaleUm = meta.scale_um > 0 ? meta.scale_um : 1;
  const halfX = (xMax - xMin) / 2 / scaleUm;
  const halfY = (yMax - yMin) / 2 / scaleUm;

  const usableWidth = Math.max(0, width - 2 * padding);
  const usableHeight = Math.max(0, height - 2 * padding);
  const scale =
    halfX > 0 && halfY > 0 && usableWidth > 0 && usableHeight > 0
      ? Math.min(usableWidth / (2 * halfX), usableHeight / (2 * halfY))
      : 0;

  return {
    width,
    height,
    scale,
    originX: width / 2,
    originY: height / 2,
    drawWidth: 2 * halfX * scale,
    drawHeight: 2 * halfY * scale,
    scaleUm,
    centreXUm: (xMin + xMax) / 2,
    centreYUm: (yMin + yMax) / 2,
  };
}

/** A normalized point's place on the canvas, in CSS pixels. */
export function normToPixel(fit: SectionFit, xNorm: number, yNorm: number): [number, number] {
  return [fit.originX + xNorm * fit.scale, fit.originY - yNorm * fit.scale];
}

/**
 * A canvas pixel's place in the section's micron frame — the half of the round trip that turns a
 * click into the point the morphology view goes and looks at.
 *
 * With nothing drawable the honest answer is the middle of the section: it is where a click would
 * have landed had the plot been one pixel wide, and it keeps the caller from having to handle a
 * NaN it can do nothing about.
 */
export function pixelToUm(fit: SectionFit, px: number, py: number): [number, number] {
  if (fit.scale <= 0) return [fit.centreXUm, fit.centreYUm];
  return [
    fit.centreXUm + ((px - fit.originX) / fit.scale) * fit.scaleUm,
    fit.centreYUm - ((py - fit.originY) / fit.scale) * fit.scaleUm,
  ];
}

/** The other half: where a micron coordinate shows up on the canvas, for the focus marker. */
export function umToPixel(fit: SectionFit, xUm: number, yUm: number): [number, number] {
  if (fit.scale <= 0) return [fit.originX, fit.originY];
  return [
    fit.originX + ((xUm - fit.centreXUm) / fit.scaleUm) * fit.scale,
    fit.originY - ((yUm - fit.centreYUm) / fit.scaleUm) * fit.scale,
  ];
}

/**
 * Widest and narrowest a dot may get, in CSS pixels of radius. The floor keeps a 400k-cell Xenium
 * section from vanishing into sub-pixel dust; the ceiling keeps a 4k-spot Visium section from
 * turning into overlapping blobs when the panel is dragged out to full screen.
 */
export const DOT_RADIUS_RANGE: [number, number] = [0.5, 5];

/**
 * How big to draw one point, from how much room each one has.
 *
 * The two spatial units in the atlas are two orders of magnitude apart — a LIBD Visium brain
 * section is ~4,000 spots, a Xenium colon is 587,115 cells — so a fixed size is wrong for one of
 * them by construction: it is either invisible dust or a solid smear. Half the mean centre-to-centre
 * spacing (the square root of area per point) makes the dots just touch at an even spread, which
 * reads as tissue at both densities and needs no per-technology special case.
 */
export function dotRadiusPx(nPoints: number, drawWidth: number, drawHeight: number): number {
  const area = drawWidth * drawHeight;
  if (nPoints <= 0 || area <= 0) return DOT_RADIUS_RANGE[0];
  const spacing = Math.sqrt(area / nPoints);
  return Math.min(DOT_RADIUS_RANGE[1], Math.max(DOT_RADIUS_RANGE[0], spacing / 2));
}

/**
 * The pixels one dot covers, as flat `[dx, dy, dx, dy, ...]` offsets from its centre.
 *
 * Precomputed once per radius and reused for every point: a disc test inside the plotting loop
 * would run 587,115 times over. Sub-pixel radii collapse to the single centre pixel — the honest
 * rendering of "less than one pixel each" is one pixel each.
 */
export function stampOffsets(radiusPx: number): number[] {
  const r = Math.floor(radiusPx);
  if (!(r >= 1)) return [0, 0];
  // Half a pixel of slack so the rim pixels land inside rather than being cut by the exact radius.
  const rSq = (r + 0.5) * (r + 0.5);
  const out: number[] = [];
  for (let dy = -r; dy <= r; dy++) {
    for (let dx = -r; dx <= r; dx++) {
      if (dx * dx + dy * dy <= rSq) out.push(dx, dy);
    }
  }
  return out;
}

/** Colour table resolution. 256 stops is finer than an 8-bit channel can show. */
const LUT_SIZE = 256;

function buildLut(map: (t: number) => [number, number, number]): Uint8Array {
  const table = new Uint8Array(LUT_SIZE * 3);
  for (let i = 0; i < LUT_SIZE; i++) {
    const [r, g, b] = map(i / (LUT_SIZE - 1));
    table[i * 3] = Math.round(r * 255);
    table[i * 3 + 1] = Math.round(g * 255);
    table[i * 3 + 2] = Math.round(b * 255);
  }
  return table;
}

const VIRIDIS_LUT = buildLut(viridis);
const MAGMA_LUT = buildLut(magma);

/** Which colormap a plot is showing, so the caption can say so without re-deriving it. */
export type Ramp = "viridis" | "magma";

export interface SectionColors {
  /** Packed RGB, three bytes per point, parallel to `points.x`. */
  rgb: Uint8Array;
  ramp: Ramp;
  /** The value range the ramp is stretched across, for the legend. */
  range: [number, number];
}

/**
 * Colour every point: viridis by transcript count, magma by a selected gene (Global Constraint 2 —
 * measured data gets a perceptually uniform ramp, never the scene palette).
 *
 * Gene values fall back to counts unless they are the same length as the positions. They are two
 * separate fetches against the same budget, so mid-swap the store can briefly hold one section's
 * positions and another's — or a stale budget's — values, and painting those against each other
 * would be a plausible-looking lie.
 */
export function buildColors(
  points: PointCloud,
  paint: Paint,
  geneValues: GeneValues | null,
): SectionColors {
  const n = points.x.length;
  const useGene = paint === "gene" && geneValues !== null && geneValues.values.length === n;
  const table = useGene ? MAGMA_LUT : VIRIDIS_LUT;
  const values = useGene ? geneValues.values : points.counts;
  const range = useGene ? geneValues.meta.value_range : points.meta.count_range;

  const rgb = new Uint8Array(n * 3);
  for (let i = 0; i < n; i++) {
    const k = Math.round(normalize(values[i], range) * (LUT_SIZE - 1)) * 3;
    rgb[i * 3] = table[k];
    rgb[i * 3 + 1] = table[k + 1];
    rgb[i * 3 + 2] = table[k + 2];
  }
  return { rgb, ramp: useGene ? "magma" : "viridis", range };
}
