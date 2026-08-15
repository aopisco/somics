import { describe, expect, it } from "vitest";

import { buildBodyVoxels } from "./voxelize";
import type { BodyDef, Species, Vec3 } from "../types";

// Bounds sit inside both the human legs and the rat trunk (see silhouette.ts), so the
// real per-species silhouette produces a non-empty shell without needing a fake sdf.
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

const VOXEL = 0.4; // divides the 4-unit bounds evenly so cell centres never spill outside

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
  const species: Species[] = ["human", "rat"];

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
});
