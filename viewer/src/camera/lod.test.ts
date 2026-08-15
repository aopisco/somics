import { describe, expect, it } from "vitest";

import { focusFor, layerOpacity, lodForDistance, sectionTransform, SECTION_SIZE } from "./lod";
import { LODS } from "../types";
import type { Lod, Vec3 } from "../types";

const RAT_BOUNDS: [Vec3, Vec3] = [
  [-9.5, 0, -9.5],
  [9.5, 7, 9.5],
];
const HUMAN_BOUNDS: [Vec3, Vec3] = [
  [-4.5, 0, -4.5],
  [4.5, 18, 4.5],
];
const ANCHOR: Vec3 = [3, 2.5, -1];

function distance(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

function focusDistance(lod: Lod, bounds: [Vec3, Vec3], anchor: Vec3 | null): number {
  const focus = focusFor({ lod, bounds, anchor });
  return distance(focus.position, focus.target);
}

describe("layerOpacity", () => {
  it("pins the exact opacity table", () => {
    expect(layerOpacity("orbit")).toEqual({ world: 1, body: 1, points: 0, crops: 0 });
    expect(layerOpacity("organ")).toEqual({ world: 1, body: 1, points: 0, crops: 0 });
    expect(layerOpacity("section")).toEqual({ world: 0, body: 0.12, points: 1, crops: 0 });
    expect(layerOpacity("cell")).toEqual({ world: 0, body: 0, points: 0.55, crops: 1 });
  });
});

describe("sectionTransform", () => {
  it("centres the unit square on the anchor at SECTION_SIZE / 2 scale", () => {
    expect(SECTION_SIZE).toBe(6);
    const { position, scale } = sectionTransform(ANCHOR);
    expect(position).toEqual(ANCHOR);
    expect(scale).toBe(3);
  });
});

describe("focusFor", () => {
  it.each([
    ["rat", RAT_BOUNDS],
    ["human", HUMAN_BOUNDS],
  ] as const)("distances strictly decrease orbit > organ > section > cell (%s)", (_name, bounds) => {
    const distances = LODS.map((lod) => focusDistance(lod, bounds, ANCHOR));
    expect(distances[0]).toBeGreaterThan(distances[1]);
    expect(distances[1]).toBeGreaterThan(distances[2]);
    expect(distances[2]).toBeGreaterThan(distances[3]);
  });

  it.each([
    ["rat", RAT_BOUNDS],
    ["human", HUMAN_BOUNDS],
  ] as const)("never produces NaN with a null anchor (%s)", (_name, bounds) => {
    for (const lod of LODS) {
      const focus = focusFor({ lod, bounds, anchor: null });
      for (const value of [...focus.position, ...focus.target]) {
        expect(Number.isFinite(value)).toBe(true);
      }
    }
  });

  it("falls back to the body centre when anchor is null", () => {
    const focus = focusFor({ lod: "organ", bounds: RAT_BOUNDS, anchor: null });
    expect(focus.target).toEqual([0, 3.5, 0]);
  });
});

describe("lodForDistance", () => {
  it("is monotonic across a sweep and saturates at the extremes", () => {
    const depth: Record<Lod, number> = { cell: 0, section: 1, organ: 2, orbit: 3 };
    const sweep = [0.001, 0.05, 0.15, 0.3, 0.5, 1, 2, 3, 3.5, 4, 6, 10, 20, 50, 1000, 1_000_000];
    let previous = -1;
    for (const d of sweep) {
      const current = depth[lodForDistance(d, RAT_BOUNDS)];
      expect(current).toBeGreaterThanOrEqual(previous);
      previous = current;
    }
    expect(lodForDistance(1_000_000, RAT_BOUNDS)).toBe("orbit");
    expect(lodForDistance(0.0001, RAT_BOUNDS)).toBe("cell");
  });

  it.each([
    ["rat", RAT_BOUNDS],
    ["human", HUMAN_BOUNDS],
  ] as const)("agrees with focusFor for every level (%s)", (_name, bounds) => {
    for (const lod of LODS) {
      const d = focusDistance(lod, bounds, ANCHOR);
      expect(lodForDistance(d, bounds)).toBe(lod);
    }
  });
});
