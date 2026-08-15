/**
 * The camera/zoom-level model: what's visible at each level, where the section
 * plane sits in world space, and where the camera frames each level from.
 *
 * Pure and side-effect free so CameraRig and other layers can share one
 * definition of "where things are" without importing three.js here.
 */

import type { Lod, Vec3 } from "../types";

export interface LayerOpacity {
  world: number;
  body: number;
  points: number;
  crops: number;
}

const LAYER_OPACITY: Record<Lod, LayerOpacity> = {
  orbit: { world: 1, body: 1, points: 0, crops: 0 },
  organ: { world: 1, body: 1, points: 0, crops: 0 },
  section: { world: 0, body: 0.12, points: 1, crops: 0 },
  cell: { world: 0, body: 0, points: 0.55, crops: 1 },
};

export function layerOpacity(lod: Lod): LayerOpacity {
  return LAYER_OPACITY[lod];
}

/** World units across the section plane's long axis. */
export const SECTION_SIZE = 6;

/** Where the section's unit square ([-1, 1] on its long axis) sits in world space. */
export function sectionTransform(anchor: Vec3): { position: Vec3; scale: number } {
  return { position: anchor, scale: SECTION_SIZE / 2 };
}

export interface Focus {
  position: Vec3;
  target: Vec3;
}

const ORBIT_DISTANCE_FACTOR = 2.4;
const ORGAN_DISTANCE = 4;
const SECTION_DISTANCE = SECTION_SIZE * 0.5;
const CELL_DISTANCE = SECTION_DISTANCE * 0.1;

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
const ORBIT_DIR = normalize([0.55, 0.38, 0.82]);
const ORGAN_DIR = normalize([0.4, 0.22, 0.9]);
// Section/cell look straight down the section plane's normal.
const PLANE_DIR: Vec3 = [0, 0, 1];

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
    case "section": {
      const plane = sectionTransform(anchorPoint).position;
      return { position: addV(plane, scaleV(PLANE_DIR, SECTION_DISTANCE)), target: plane };
    }
    case "cell": {
      const plane = sectionTransform(anchorPoint).position;
      return { position: addV(plane, scaleV(PLANE_DIR, CELL_DISTANCE)), target: plane };
    }
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
