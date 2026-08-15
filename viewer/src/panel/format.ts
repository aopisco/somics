/** Presentation formatting for the panel. Kept free of React so it is trivial to test. */

import type { Lod } from "../types";

const DASH = "-";

/** Truncates (never rounds) to `decimals` places, so e.g. 6.916 -> 6.91, not 6.92. */
function truncate(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.trunc(value * factor) / factor;
}

export function formatCount(n: number): string {
  if (!Number.isFinite(n) || n < 0) return DASH;
  return Math.round(n).toLocaleString("en-US");
}

export function formatMicrons(um: number): string {
  if (!Number.isFinite(um) || um < 0) return DASH;
  if (um < 1000) return `${truncate(um, 1).toFixed(1)} µm`;
  return `${truncate(um / 1000, 2).toFixed(2)} mm`;
}

export function humanizeKey(key: string): string {
  if (!key) return key;
  const spaced = key.split("_").join(" ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Label for the panel's "go up a level" control. Named after where the click lands, not the
 * generic word "back" — and worded with the same names the breadcrumb (`App.tsx`) already uses
 * for each level, so the two controls read as one vocabulary. `null` at `orbit`: there is nowhere
 * further up to go, so the caller should hide the control rather than render a dead one.
 */
export function backLabel(
  lod: Lod,
  names: { organ: string | null; section: string | null; species: string },
): string | null {
  switch (lod) {
    case "cell":
      return `Back to ${names.section ?? "the section"}`;
    case "section":
      return `Back to ${names.organ ?? "the organ"}`;
    case "organ":
      return `Back to ${names.species}`;
    case "orbit":
      return null;
  }
}

export function formatExtent(extent: [number, number, number, number]): string {
  const [xMin, yMin, xMax, yMax] = extent;
  if (![xMin, yMin, xMax, yMax].every(Number.isFinite)) return DASH;
  const width = xMax - xMin;
  const height = yMax - yMin;
  if (width < 0 || height < 0) return DASH;
  const widthMm = truncate(width / 1000, 2).toFixed(2);
  const heightMm = truncate(height / 1000, 2).toFixed(2);
  return `${widthMm} × ${heightMm} mm`;
}
