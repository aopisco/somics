import { describe, expect, it } from "vitest";

import { MAX_GRID_SAMPLES, buildBodyVoxels } from "./voxelize";
import { VOXEL as SHIPPED_VOXEL } from "../theme";
import type { BodyDef, Species, Vec3 } from "../types";

// Bounds sit inside the human legs, the rat trunk and the zebrafish flank (see
// silhouette.ts), so the real per-species silhouette produces a non-empty shell
// without needing a fake sdf.
const BOUNDS: [Vec3, Vec3] = [
  [-2, 0, -2],
  [2, 4, 2],
];

function fixtureBody(): BodyDef {
  return {
    bounds: BOUNDS,
    organs: [
      {
        node_id: "heart",
        label: "Heart",
        system: "circulatory",
        color: "#ff5566",
        anchor: [0, 2, 0],
        blobs: [{ center: [0, 2, 0], size: [0.4, 0.4, 0.4] }],
      },
      {
        node_id: "colon",
        label: "Colon",
        system: "digestive",
        color: "#66aa33",
        anchor: [-0.15, 1, 0],
        blobs: [
          { center: [-1.2, 1, 0], size: [0.35, 0.25, 0.25] },
          { center: [-0.5, 1, 0], size: [0.35, 0.25, 0.25] },
          { center: [0.2, 1, 0], size: [0.35, 0.25, 0.25] },
          { center: [0.9, 1, 0], size: [0.35, 0.25, 0.25] },
        ],
      },
    ],
  };
}

// Widest of the three authored bodies, so it is the one the grid cap has to hold.
const HUMAN_BOUNDS: [Vec3, Vec3] = [
  [-4.5, 0, -2.5],
  [4.5, 18, 2.5],
];

// Grid size for the fixture only, not the shipped one (that is SHIPPED_VOXEL): 0.4
// divides the 4-unit bounds evenly so cell centres never spill outside.
const VOXEL = 0.4;

function inBounds(positions: Float32Array, bounds: [Vec3, Vec3]): boolean {
  const [min, max] = bounds;
  for (let i = 0; i < positions.length; i += 3) {
    for (let axis = 0; axis < 3; axis++) {
      const v = positions[i + axis];
      if (v < min[axis] - 1e-6 || v > max[axis] + 1e-6) return false;
    }
  }
  return true;
}

function positionSet(positions: Float32Array): Set<string> {
  const set = new Set<string>();
  for (let i = 0; i < positions.length; i += 3) {
    set.add(`${positions[i]},${positions[i + 1]},${positions[i + 2]}`);
  }
  return set;
}

describe("buildBodyVoxels", () => {
  const species: Species[] = ["human", "rat", "zebrafish"];

  for (const s of species) {
    it(`produces a non-empty shell for ${s}`, () => {
      const result = buildBodyVoxels(fixtureBody(), s, VOXEL);
      expect(result.shell.count).toBeGreaterThan(0);
      expect(result.shell.positions.length).toBe(result.shell.count * 3);
    });

    it(`includes every organ from the payload for ${s}`, () => {
      const body = fixtureBody();
      const result = buildBodyVoxels(body, s, VOXEL);
      expect(result.organs.map((o) => o.nodeId)).toEqual(body.organs.map((o) => o.node_id));
    });

    it(`keeps a colon-like multi-blob organ intact for ${s}`, () => {
      const result = buildBodyVoxels(fixtureBody(), s, VOXEL);
      const colon = result.organs.find((o) => o.nodeId === "colon");
      expect(colon).toBeDefined();
      expect(colon?.field.count).toBeGreaterThan(0);
    });

    it(`never shares a position between shell and organ voxels for ${s}`, () => {
      const result = buildBodyVoxels(fixtureBody(), s, VOXEL);
      const shellSet = positionSet(result.shell.positions);
      for (const organ of result.organs) {
        for (let i = 0; i < organ.field.positions.length; i += 3) {
          const key = `${organ.field.positions[i]},${organ.field.positions[i + 1]},${organ.field.positions[i + 2]}`;
          expect(shellSet.has(key)).toBe(false);
        }
      }
    });

    it(`keeps all voxel positions inside bounds for ${s}`, () => {
      const result = buildBodyVoxels(fixtureBody(), s, VOXEL);
      expect(inBounds(result.shell.positions, BOUNDS)).toBe(true);
      for (const organ of result.organs) {
        expect(inBounds(organ.field.positions, BOUNDS)).toBe(true);
      }
    });

    it(`reports count consistent with positions length for ${s}`, () => {
      const result = buildBodyVoxels(fixtureBody(), s, VOXEL);
      expect(result.shell.count).toBe(result.shell.positions.length / 3);
      for (const organ of result.organs) {
        expect(organ.field.count).toBe(organ.field.positions.length / 3);
      }
    });

    it(`is deterministic across calls for ${s}`, () => {
      const first = buildBodyVoxels(fixtureBody(), s, VOXEL);
      const second = buildBodyVoxels(fixtureBody(), s, VOXEL);
      expect(first.shell.positions).toEqual(second.shell.positions);
      expect(first.organs.map((o) => Array.from(o.field.positions))).toEqual(
        second.organs.map((o) => Array.from(o.field.positions)),
      );
    });
  }

  it("throws when the voxel grid would exceed the sample cap", () => {
    expect(() => buildBodyVoxels(fixtureBody(), "human", 0.001)).toThrow();
  });

  it("reports the grid it refused, rounding each axis up", () => {
    // 4-unit bounds at 0.03 is ceil(4 / 0.03) = 134 cells per axis. The refusal message
    // is the only place that arithmetic is visible from outside.
    expect(() => buildBodyVoxels(fixtureBody(), "human", 0.03)).toThrow(
      `grid of 134x134x134 = ${134 ** 3} samples`,
    );
  });

  it("admits a grid that fits under the cap", () => {
    // 80^3 = 512,000: over the pre-Task-9 cap of 400,000, inside the current one.
    expect(() => buildBodyVoxels(fixtureBody(), "human", 0.05)).not.toThrow();
  });

  it("keeps the largest authored body inside the cap at the shipped voxel size", () => {
    // The human is the biggest grid of the three; bounds mirror BODY_BOUNDS["human"] in
    // src/somics/viewer/anatomy.py, which the API serves. Lower theme.ts's VOXEL and this
    // is the test that tells you the body will fail to build instead of just being slow.
    const [min, max] = HUMAN_BOUNDS;
    const grid =
      Math.ceil((max[0] - min[0]) / SHIPPED_VOXEL) *
      Math.ceil((max[1] - min[1]) / SHIPPED_VOXEL) *
      Math.ceil((max[2] - min[2]) / SHIPPED_VOXEL);
    expect(grid).toBeLessThanOrEqual(MAX_GRID_SAMPLES);
  });
});
