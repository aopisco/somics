/** One palette for the whole viewer, so scene and UI agree without coordination.
 *
 * Golden-hour field: warm low sun, sage grass, pale glass body, organs glowing from
 * inside. Data layers deliberately sit outside this range (viridis) so measured values
 * never read as decoration.
 */

export const SKY = {
  zenith: "#7fb2d9",
  horizon: "#f6d7a8",
  haze: "#e9c79b",
  sun: "#fff0cf",
} as const;

export const GROUND = {
  soil: "#6b5b45",
  grassLow: "#6f8a4a",
  grassHigh: "#9cb163",
  grassDry: "#c2b06a",
} as const;

export const BODY = {
  shell: "#dfe7ee",
  shellEdge: "#a9c0d4",
  /** Organ voxels use the per-organ colour from the API; this is the fallback. */
  organ: "#c98bdb",
} as const;

export const UI = {
  panel: "rgba(18, 22, 28, 0.82)",
  panelEdge: "rgba(255, 255, 255, 0.12)",
  text: "#eef2f6",
  textDim: "#9fb0c0",
  accent: "#ffd479",
  warn: "#f2a65a",
} as const;

/** Marker states: a sample the atlas has, versus an organ waiting for one. */
export const MARKER = {
  live: "#ffd479",
  liveGlow: "#fff6dd",
  empty: "#5d6b78",
} as const;

/** World size of one body voxel, in body units. Shared by the voxelizer and the grass.
 *
 * Measured against the real anatomy payload: at 0.42 the smaller organs (eye, adrenal,
 * tonsil) round away to zero voxels and vanish; 0.26 gives every organ at least two and
 * the heart ~44, for ~3.7k cubes on the rat, ~5.2k on the human and ~2.3k on the
 * zebrafish. Going finer reads as smooth rather than pixelated and costs cubes for
 * nothing.
 */
export const VOXEL = 0.26;
