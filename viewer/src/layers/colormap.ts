/** Perceptually-uniform colormaps for measured data. Never used for UI chrome. */

type Rgb = [number, number, number];

/** matplotlib viridis, sampled at 9 stops (0..255 per channel). */
const VIRIDIS_STOPS: Rgb[] = [
  [68, 1, 84],
  [72, 40, 120],
  [62, 74, 137],
  [49, 104, 142],
  [38, 130, 142],
  [31, 158, 137],
  [53, 183, 121],
  [109, 205, 89],
  [253, 231, 37],
];

/** matplotlib magma, sampled at 9 stops (0..255 per channel). */
const MAGMA_STOPS: Rgb[] = [
  [0, 0, 4],
  [28, 16, 68],
  [79, 18, 123],
  [129, 37, 129],
  [181, 54, 122],
  [229, 80, 100],
  [251, 135, 97],
  [254, 194, 135],
  [252, 253, 191],
];

function interpolate(stops: Rgb[], t: number): Rgb {
  const clamped = Math.min(1, Math.max(0, t));
  const scaled = clamped * (stops.length - 1);
  const i0 = Math.floor(scaled);
  const i1 = Math.min(stops.length - 1, i0 + 1);
  const frac = scaled - i0;
  const a = stops[i0];
  const b = stops[i1];
  return [
    (a[0] + (b[0] - a[0]) * frac) / 255,
    (a[1] + (b[1] - a[1]) * frac) / 255,
    (a[2] + (b[2] - a[2]) * frac) / 255,
  ];
}

export function viridis(t: number): Rgb {
  return interpolate(VIRIDIS_STOPS, t);
}

export function magma(t: number): Rgb {
  return interpolate(MAGMA_STOPS, t);
}

export function normalize(value: number, range: [number, number]): number {
  const [low, high] = range;
  if (high <= low) return 0;
  return Math.min(1, Math.max(0, (value - low) / (high - low)));
}
