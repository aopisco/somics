/** One palette for the whole viewer, so scene and UI agree without coordination.
 *
 * Alpine spring morning: high clear sun, sage grass, pale glass body, organs glowing
 * from inside. Data layers deliberately sit outside this range (viridis) so measured
 * values never read as decoration.
 *
 * The sky entries are read off the panorama in `public/env/` rather than chosen: the
 * photograph is the sky now, so a palette that disagreed with it would show up as a body
 * lit for a different day. `zenith` is the mean of the plate's top twelfth and drives the
 * hemisphere light. `horizon` is a pale alpine haze between the plate's near-horizon sky
 * and its mist; nothing in the scene is tinted with it — it is the canvas clear colour,
 * seen while the panorama loads and at the data levels, where no plate is drawn.
 */

export const SKY = {
  zenith: "#7896c0",
  horizon: "#9fb6c8",
  sun: "#fff3dd",
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
