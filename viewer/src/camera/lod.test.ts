import { describe, expect, it } from "vitest";

import { cameraStep, focusFor, layerOpacity, lodForDistance, manualLod } from "./lod";
import { LODS } from "../types";
import type { CameraState, Lod, Vec3 } from "../types";

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
  // The data left the scene for the panel, so no level clears the stage for it any more. A blank
  // 3D stage is the thing that made this read as broken; this is the assertion that catches it
  // coming back.
  it("leaves the world and the body at full strength at every level", () => {
    for (const lod of LODS) expect(layerOpacity(lod)).toEqual({ world: 1, body: 1 });
  });
});

describe("focusFor", () => {
  it("frames every level from outside the organ, never through it", () => {
    // The complaint was "it zooms into the brain". The closest level must still stand off the
    // anchor by more than the largest organ blob is likely to be.
    for (const lod of LODS) {
      expect(focusDistance(lod, RAT_BOUNDS, ANCHOR)).toBeGreaterThan(2);
    }
  });

  it("keeps section and cell pointed at the organ, not at a plane in front of it", () => {
    for (const lod of ["organ", "section", "cell"] as const) {
      expect(focusFor({ lod, bounds: RAT_BOUNDS, anchor: ANCHOR }).target).toEqual(ANCHOR);
    }
  });

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

describe("manualLod", () => {
  it("moves between orbit and organ on distance alone", () => {
    for (const from of ["orbit", "organ"] as const) {
      for (const to of ["orbit", "organ"] as const) {
        expect(manualLod(focusDistance(to, RAT_BOUNDS, ANCHOR), RAT_BOUNDS, from)).toBe(to);
      }
    }
  });

  it("collapses the inner distances onto organ — the wheel cannot select a sample", () => {
    for (const lod of ["section", "cell"] as const) {
      const d = focusDistance(lod, RAT_BOUNDS, ANCHOR);
      expect(manualLod(d, RAT_BOUNDS, "organ")).toBe("organ");
      expect(manualLod(d, RAT_BOUNDS, "orbit")).toBe("organ");
    }
  });

  // The regression this guards: section and cell now frame the organ, so their distance reads as
  // "organ". If the wheel were allowed to answer, arriving at a section would immediately throw
  // the section away again.
  it("never moves a selected section or cell, at any distance", () => {
    for (const current of ["section", "cell"] as const) {
      for (const d of [0.01, 1, 2.6, 3.2, 4, 12, 40, 5000]) {
        expect(manualLod(d, RAT_BOUNDS, current)).toBe(current);
      }
    }
  });
});

describe("cameraStep", () => {
  const PINNED: CameraState = { position: [1, 2, 3], target: [0, 0, 0] };

  const step = (patch: Partial<Parameters<typeof cameraStep>[0]> = {}) =>
    cameraStep({
      flyRequest: 1,
      placedFor: null,
      bounds: RAT_BOUNDS,
      lod: "section",
      anchor: ANCHOR,
      storedCamera: null,
      ...patch,
    });

  it("is settled only once the camera has been placed for this navigation", () => {
    expect(step({ flyRequest: 4, placedFor: 4 })).toEqual({ kind: "settled" });
    expect(step({ flyRequest: 4, placedFor: 3 }).kind).toBe("fly");
  });

  // The regression: with anatomy still in flight there is nowhere to fly, and the
  // camera is still at the R3F default. Reporting "settled" here is what let the
  // rig read "orbit" off that default and throw away the URL's level.
  it("waits — never settles — while anatomy has not landed", () => {
    expect(step({ bounds: null })).toEqual({ kind: "wait" });
    expect(step({ bounds: null, flyRequest: 9, placedFor: 2 })).toEqual({ kind: "wait" });
  });

  it("stays settled after the anatomy-less wait once the fly has happened", () => {
    // Cold load asking for section: wait, wait, fly, then settled forever.
    expect(step({ bounds: null, placedFor: null }).kind).toBe("wait");
    const flight = step({ bounds: RAT_BOUNDS, placedFor: null });
    expect(flight).toEqual({ kind: "fly", focus: focusFor({ lod: "section", bounds: RAT_BOUNDS, anchor: ANCHOR }) });
    expect(step({ placedFor: 1 })).toEqual({ kind: "settled" });
  });

  it("flies to the framing the store's level and anchor ask for", () => {
    const flight = step({ lod: "organ", placedFor: 0 });
    expect(flight).toEqual({
      kind: "fly",
      focus: focusFor({ lod: "organ", bounds: RAT_BOUNDS, anchor: ANCHOR }),
    });
  });

  it("snaps to a shared link's pinned camera on the page's first navigation", () => {
    expect(step({ storedCamera: PINNED })).toEqual({ kind: "snap", camera: PINNED });
    // Even before anatomy: the link already says exactly where to be.
    expect(step({ storedCamera: PINNED, bounds: null })).toEqual({ kind: "snap", camera: PINNED });
  });

  it("flies rather than snapping once a navigation has already been served", () => {
    // `camera` in the store is now our own settled report, not the link's request.
    expect(step({ storedCamera: PINNED, placedFor: 0 }).kind).toBe("fly");
  });
});
