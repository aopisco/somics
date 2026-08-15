/** Presentation formatting for the panel. Kept free of React so it is trivial to test. */

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
