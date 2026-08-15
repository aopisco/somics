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

/** World size of one body voxel, in body units.
 *
 * Chosen by looking, then costed. Measured against the real anatomy payload in Chrome:
 *
 *   0.26  human 2.5k shell + 2.7k organ cubes,  12 ms build   (chunky; silhouette lost)
 *   0.12  human 15k shell + 27k organ cubes,   113 ms build   (this)
 *   0.10  human 21k shell + 47k organ cubes,   189 ms build   (cubes stop reading)
 *
 * All three sizes hold 60 fps at orbit on an M4, so frame rate is not what picks this
 * number. The floor is legibility: the canvas renders at `pixel` scale (0.4 by default,
 * see `PIXEL_RANGE`) and is upscaled with `image-rendering: pixelated`, so a voxel is
 * worth roughly two render-buffer pixels at 0.12 and fewer than two below it. At 0.10 the
 * organs go smooth and the bodies read as airbrushed lumps rather than voxel art — more
 * cubes, less voxel. 0.12 is the finest size where an individual cube face is still
 * visible at the default render scale.
 *
 * Smallest organ claim also improves with the finer grid: 1-4 voxels at 0.26, 18-44 at
 * 0.12, so nothing is one cube away from vanishing (see `test_every_organ_claims_voxels`).
 */
export const VOXEL = 0.12;
