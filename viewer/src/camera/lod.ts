/**
 * The camera/zoom-level model: what's visible at each level, where the section
 * plane sits in world space, and where the camera frames each level from.
 *
 * Pure and side-effect free so CameraRig and other layers can share one
 * definition of "where things are" without importing three.js here.
 */

import type { CameraState, Lod, Vec3 } from "../types";

export interface LayerOpacity {
  world: number;
  body: number;
}

/**
 * Nothing dims any more, at any level.
 *
 * Section and cell used to fade the valley to nothing and the body to a ghost, because the section
 * plane needed the frame to itself. The measured data is in the floating panel now, so there is no
 * plane to clear a stage for — and a stage cleared for nothing is exactly what made the app read as
 * broken. The body, its organs and their pins stay lit the whole way in.
 *
 * The table is kept rather than collapsed to a constant because the level is still the only thing
 * that would ever drive a fade, and every layer already takes one.
 */
const LAYER_OPACITY: Record<Lod, LayerOpacity> = {
  orbit: { world: 1, body: 1 },
  organ: { world: 1, body: 1 },
  section: { world: 1, body: 1 },
  cell: { world: 1, body: 1 },
};

export function layerOpacity(lod: Lod): LayerOpacity {
  return LAYER_OPACITY[lod];
}

export interface Focus {
  position: Vec3;
  target: Vec3;
}

const ORBIT_DISTANCE_FACTOR = 2.4;
const ORGAN_DISTANCE = 4;
// Section and cell are a lean-in on the organ, not a flight past it.
//
// They used to be a plane six world units across, hung on the organ's anchor, framed head-on from
// three units out and then from 0.3 — which is inside the body shell and, for a big organ, inside
// the organ. That is the "it zooms into the brain" the user reported. There is no plane now, so the
// deepest thing the camera has to look at is the organ, and it stops there.
//
// Fractions of the organ framing rather than absolutes, and deliberately shallow ones: the organ
// distance is already close enough that a rat's colon is a body-width from the shell, and anything
// that undercuts it much puts the camera inside the body. Whatever eventually re-frames the organ
// level for body size carries these two with it.
const SECTION_DISTANCE = ORGAN_DISTANCE * 0.92;
const CELL_DISTANCE = ORGAN_DISTANCE * 0.85;

export const TWEEN_SECONDS = 1.5;

function addV(a: Vec3, b: Vec3): Vec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function scaleV(a: Vec3, s: number): Vec3 {
  return [a[0] * s, a[1] * s, a[2] * s];
}

function normalize(a: Vec3): Vec3 {
  const len = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / len, a[1] / len, a[2] / len];
}

// Low three-quarter angle: off to one side, in front, above mid-height.
//
// The Y was 0.38, which is 21 degrees of elevation against a 21-degree half-FOV: the
// camera pitched down far enough to put the horizon exactly on the top edge of the
// frame. That was fine over a two-tone gradient dome and is not fine now the sky is a
// photographed valley — it left a sliver of plate above a frame of grass. At 0.20 the
// horizon sits about a quarter of the way down and the valley is what you open onto.
// Only the direction changed; orbitDistance and so lodForDistance's thresholds did not.
const ORBIT_DIR = normalize([0.55, 0.2, 0.82]);
const ORGAN_DIR = normalize([0.4, 0.22, 0.9]);

function bodyCenter(bounds: [Vec3, Vec3]): Vec3 {
  const [min, max] = bounds;
  return [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
}

function bodyRadius(bounds: [Vec3, Vec3]): number {
  const [min, max] = bounds;
  return Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2;
}

// Floored so orbit stays strictly outside organ even for a very small body.
function orbitDistance(bounds: [Vec3, Vec3]): number {
  return Math.max(bodyRadius(bounds) * ORBIT_DISTANCE_FACTOR, ORGAN_DISTANCE * 1.5);
}

export function focusFor(args: { lod: Lod; bounds: [Vec3, Vec3]; anchor: Vec3 | null }): Focus {
  const { lod, bounds, anchor } = args;
  const center = bodyCenter(bounds);
  const anchorPoint = anchor ?? center;

  switch (lod) {
    case "orbit":
      return { position: addV(center, scaleV(ORBIT_DIR, orbitDistance(bounds))), target: center };
    case "organ":
      return {
        position: addV(anchorPoint, scaleV(ORGAN_DIR, ORGAN_DISTANCE)),
        target: anchorPoint,
      };
    case "section":
      return {
        position: addV(anchorPoint, scaleV(ORGAN_DIR, SECTION_DISTANCE)),
        target: anchorPoint,
      };
    case "cell":
      return {
        position: addV(anchorPoint, scaleV(ORGAN_DIR, CELL_DISTANCE)),
        target: anchorPoint,
      };
  }
}

/** Maps a live camera-to-target distance onto a level; thresholds sit at the
 *  midpoint between the distances `focusFor` picks for each adjacent pair, so
 *  the two stay in agreement. */
export function lodForDistance(distance: number, bounds: [Vec3, Vec3]): Lod {
  const cellSection = (CELL_DISTANCE + SECTION_DISTANCE) / 2;
  const sectionOrgan = (SECTION_DISTANCE + ORGAN_DISTANCE) / 2;
  const organOrbit = (ORGAN_DISTANCE + orbitDistance(bounds)) / 2;

  if (distance <= cellSection) return "cell";
  if (distance <= sectionOrgan) return "section";
  if (distance <= organOrbit) return "organ";
  return "orbit";
}

/**
 * The level a manual orbit/wheel zoom lands on, given where it already is.
 *
 * Section and cell are no longer places the wheel can go. They used to be: a plane hung in front of
 * the organ, with its own camera distance, that the wheel could fall into and out of. The measured
 * data is in the floating panel now, so those two levels are a *selection* — a sample, and a point
 * within it — and the camera has nothing of its own to show at either. It stops at the organ.
 *
 * That leaves the wheel two jobs and takes one away. It moves between orbit and organ as before,
 * and it collapses the inner distances onto organ so wheeling in from the body cannot land on a
 * level with no sample behind it. But it may not report a level while one of the inner two is
 * selected: from the camera's distance, "parked at the organ" and "parked at the organ with a
 * section open" are the same reading, and letting it answer would drop the selection every frame.
 * The back control, the breadcrumb and Escape are how those levels are left.
 */
export function manualLod(distance: number, bounds: [Vec3, Vec3], current: Lod): Lod {
  if (current === "section" || current === "cell") return current;
  const reached = lodForDistance(distance, bounds);
  return reached === "section" || reached === "cell" ? "organ" : reached;
}

/**
 * What the rig owes the camera this frame.
 *
 * - `settled` — the camera has already been placed for the navigation in hand, so
 *   deriving a level from its distance is safe.
 * - `wait` — a move is owed but there is nowhere to move to yet. The camera is
 *   stale; nothing may read a level off it.
 * - `snap` — put the camera exactly here, no tween.
 * - `fly` — tween the camera to this framing.
 */
export type CameraStep =
  | { kind: "settled" }
  | { kind: "wait" }
  | { kind: "snap"; camera: CameraState }
  | { kind: "fly"; focus: Focus };

export interface NavigationInput {
  /** The current navigation, counted up by the store on every explicit move. */
  flyRequest: number;
  /** The `flyRequest` the camera has already been placed for; null before any. */
  placedFor: number | null;
  /** null until `/api/anatomy` lands. */
  bounds: [Vec3, Vec3] | null;
  lod: Lod;
  anchor: Vec3 | null;
  /** The camera a shared link pinned. Honoured only for the page's first navigation. */
  storedCamera: CameraState | null;
}

/**
 * Decides, without touching three.js, whether the camera still owes the current
 * navigation a move.
 *
 * This is the rule the rig got wrong: a level may only be derived from the camera's
 * distance once the camera has been placed for the navigation in hand. Until then —
 * including while the move is blocked on anatomy that has not arrived — the camera
 * is still sitting wherever the previous navigation or the R3F default left it, and
 * reading a level off it overwrites the level that asked for the move.
 */
export function cameraStep(input: NavigationInput): CameraStep {
  const { flyRequest, placedFor, bounds, lod, anchor, storedCamera } = input;
  if (placedFor === flyRequest) return { kind: "settled" };
  // A shared link's exact camera outranks the level's default framing, but only for
  // the state the page opened on; every later navigation flies.
  if (placedFor === null && storedCamera) return { kind: "snap", camera: storedCamera };
  if (!bounds) return { kind: "wait" };
  return { kind: "fly", focus: focusFor({ lod, bounds, anchor }) };
}
