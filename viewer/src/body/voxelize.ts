/** Turns a body's silhouette + authored organ blobs into instanced-mesh-ready voxel
 *  fields: a hollow glass shell plus one solid clump per organ.
 */

import { bodySdf } from "./silhouette";
import { VOXEL } from "../theme";
import type { BodyDef, OrganNode, Species, Vec3 } from "../types";

export interface VoxelField {
  positions: Float32Array;
  count: number;
}

export interface OrganVoxels {
  nodeId: string;
  label: string;
  color: string;
  field: VoxelField;
  centroid: Vec3;
}

export interface BodyVoxels {
  shell: VoxelField;
  organs: OrganVoxels[];
  voxel: number;
}

const MAX_GRID_SAMPLES = 400_000;
/** Shell keeps only voxels within this many voxel-widths of the sdf boundary,
 *  so the body reads as a hollow husk with organs visible inside.
 */
const SHELL_BAND_VOXELS = 1.6;

function insideAnyBlob(x: number, y: number, z: number, organ: OrganNode): boolean {
  for (const blob of organ.blobs) {
    const dx = (x - blob.center[0]) / blob.size[0];
    const dy = (y - blob.center[1]) / blob.size[1];
    const dz = (z - blob.center[2]) / blob.size[2];
    if (dx * dx + dy * dy + dz * dz <= 1) return true;
  }
  return false;
}

function toField(positions: number[]): VoxelField {
  return { positions: Float32Array.from(positions), count: positions.length / 3 };
}

function centroidOf(positions: number[], fallback: Vec3): Vec3 {
  const count = positions.length / 3;
  if (count === 0) return fallback;
  let sx = 0;
  let sy = 0;
  let sz = 0;
  for (let i = 0; i < positions.length; i += 3) {
    sx += positions[i];
    sy += positions[i + 1];
    sz += positions[i + 2];
  }
  return [sx / count, sy / count, sz / count];
}

export function buildBodyVoxels(body: BodyDef, species: Species, voxel: number = VOXEL): BodyVoxels {
  const [min, max] = body.bounds;
  const nx = Math.ceil((max[0] - min[0]) / voxel);
  const ny = Math.ceil((max[1] - min[1]) / voxel);
  const nz = Math.ceil((max[2] - min[2]) / voxel);
  const totalSamples = nx * ny * nz;
  if (totalSamples > MAX_GRID_SAMPLES) {
    throw new Error(
      `buildBodyVoxels: grid of ${nx}x${ny}x${nz} = ${totalSamples} samples exceeds the ` +
        `${MAX_GRID_SAMPLES} cap; use a larger voxel size`,
    );
  }

  const sdf = bodySdf(species);
  const shellBand = SHELL_BAND_VOXELS * voxel;

  const shellPositions: number[] = [];
  const organPositions: number[][] = body.organs.map(() => []);

  for (let i = 0; i < nx; i++) {
    const x = min[0] + (i + 0.5) * voxel;
    for (let j = 0; j < ny; j++) {
      const y = min[1] + (j + 0.5) * voxel;
      for (let k = 0; k < nz; k++) {
        const z = min[2] + (k + 0.5) * voxel;

        let organIndex = -1;
        for (let o = 0; o < body.organs.length; o++) {
          if (insideAnyBlob(x, y, z, body.organs[o])) {
            organIndex = o;
            break;
          }
        }

        if (organIndex >= 0) {
          organPositions[organIndex].push(x, y, z);
          continue;
        }

        const d = sdf(x, y, z);
        if (d <= 0 && d > -shellBand) shellPositions.push(x, y, z);
      }
    }
  }

  const shell = toField(shellPositions);
  const organs: OrganVoxels[] = body.organs.map((organ, index) => {
    const positions = organPositions[index];
    return {
      nodeId: organ.node_id,
      label: organ.label,
      color: organ.color,
      field: toField(positions),
      centroid: centroidOf(positions, organ.blobs[0]?.center ?? [0, 0, 0]),
    };
  });

  return { shell, organs, voxel };
}
