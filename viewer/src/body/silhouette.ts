/** Procedural body silhouettes as signed-distance functions, composed from ellipsoids,
 *  capsules and spheres. Negative inside, positive outside, roughly a signed distance
 *  in body units. No mesh assets — the voxelizer samples these directly.
 */

import type { Species } from "../types";

type Sdf = (x: number, y: number, z: number) => number;

/** Inigo Quilez's approximate ellipsoid SDF — exact on the surface, a reasonable
 *  bound elsewhere, which is all the voxel grid needs.
 */
function sdEllipsoid(
  x: number,
  y: number,
  z: number,
  cx: number,
  cy: number,
  cz: number,
  rx: number,
  ry: number,
  rz: number,
): number {
  const dx = (x - cx) / rx;
  const dy = (y - cy) / ry;
  const dz = (z - cz) / rz;
  const k0 = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (k0 === 0) return -Math.min(rx, ry, rz);
  const ex = (x - cx) / (rx * rx);
  const ey = (y - cy) / (ry * ry);
  const ez = (z - cz) / (rz * rz);
  const k1 = Math.sqrt(ex * ex + ey * ey + ez * ez);
  return (k0 * (k0 - 1)) / k1;
}

function sdSphere(x: number, y: number, z: number, cx: number, cy: number, cz: number, r: number): number {
  const dx = x - cx;
  const dy = y - cy;
  const dz = z - cz;
  return Math.sqrt(dx * dx + dy * dy + dz * dz) - r;
}

function sdCapsule(
  x: number,
  y: number,
  z: number,
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  r: number,
): number {
  const pax = x - ax;
  const pay = y - ay;
  const paz = z - az;
  const bax = bx - ax;
  const bay = by - ay;
  const baz = bz - az;
  const baLen2 = bax * bax + bay * bay + baz * baz;
  const t = baLen2 > 0 ? (pax * bax + pay * bay + paz * baz) / baLen2 : 0;
  const h = Math.min(1, Math.max(0, t));
  const dx = pax - bax * h;
  const dy = pay - bay * h;
  const dz = paz - baz * h;
  return Math.sqrt(dx * dx + dy * dy + dz * dz) - r;
}

function ellipsoid(cx: number, cy: number, cz: number, rx: number, ry: number, rz: number): Sdf {
  return (x, y, z) => sdEllipsoid(x, y, z, cx, cy, cz, rx, ry, rz);
}

function sphere(cx: number, cy: number, cz: number, r: number): Sdf {
  return (x, y, z) => sdSphere(x, y, z, cx, cy, cz, r);
}

function capsule(ax: number, ay: number, az: number, bx: number, by: number, bz: number, r: number): Sdf {
  return (x, y, z) => sdCapsule(x, y, z, ax, ay, az, bx, by, bz, r);
}

function humanParts(): Sdf[] {
  const parts: Sdf[] = [
    ellipsoid(0, 16.2, 0, 1.4, 1.5, 1.4), // head
    capsule(0, 14.3, 0, 0, 15.6, 0, 0.65), // neck, bridges head to shoulders
    ellipsoid(0, 13.5, 0, 2.6, 0.9, 1.0), // shoulders — wide flat slab arms hang from
    ellipsoid(0, 11, 0, 2.2, 3.0, 1.0), // torso
    ellipsoid(0, 7.8, 0, 1.8, 1.0, 1.0), // hips, bridges torso to legs
  ];
  for (const side of [-1, 1]) {
    parts.push(capsule(2.4 * side, 13.8, 0, 2.4 * side, 8.0, 0, 0.55)); // arm
    parts.push(capsule(0.95 * side, 7.5, 0, 0.95 * side, 0.6, 0, 0.8)); // leg
    parts.push(ellipsoid(0.95 * side, 0.4, 0.35, 0.6, 0.45, 0.85)); // foot, reaches to z=1.2
  }
  return parts;
}

/** Tail as a chain of spheres along a quadratic Bezier curve, base to tip. */
function ratTail(): Sdf[] {
  const base: [number, number, number] = [-2.8, 3.3, 0];
  const control: [number, number, number] = [-6.0, 2.2, 1.9];
  const tip: [number, number, number] = [-8.6, 1.4, 1.6];
  const beadCount = 9;
  const beads: Sdf[] = [];
  for (let i = 0; i < beadCount; i++) {
    const t = i / (beadCount - 1);
    const u = 1 - t;
    const x = u * u * base[0] + 2 * u * t * control[0] + t * t * tip[0];
    const y = u * u * base[1] + 2 * u * t * control[1] + t * t * tip[1];
    const z = u * u * base[2] + 2 * u * t * control[2] + t * t * tip[2];
    const r = 0.6 - 0.38 * t;
    beads.push(sphere(x, y, z, r));
  }
  return beads;
}

function ratParts(): Sdf[] {
  const parts: Sdf[] = [
    ellipsoid(1.5, 3.5, 0, 4.2, 1.6, 1.5), // trunk
    ellipsoid(5.8, 4.0, 0, 1.8, 1.0, 1.0), // neck, bridges trunk to head
    ellipsoid(7.0, 4.6, 0, 1.5, 1.2, 1.2), // head
    ellipsoid(8.3, 4.3, 0, 0.8, 0.6, 0.6), // snout
  ];
  for (const side of [-1, 1]) {
    parts.push(ellipsoid(6.4, 5.8, 0.9 * side, 0.7, 0.7, 0.6)); // ear
    // Leg tops angle in toward the trunk centreline so they land solidly inside it
    // rather than grazing the thin outer rim of the trunk ellipsoid.
    parts.push(capsule(4.6, 0, 1.0 * side, 3.8, 3.0, 0.4 * side, 0.62)); // front leg
    parts.push(capsule(-2.6, 0, 1.0 * side, -1.8, 3.0, 0.4 * side, 0.62)); // back leg
  }
  parts.push(...ratTail());
  return parts;
}

export function bodySdf(species: Species): (x: number, y: number, z: number) => number {
  const parts = species === "human" ? humanParts() : ratParts();
  return (x, y, z) => {
    let min = Infinity;
    for (const part of parts) {
      const d = part(x, y, z);
      if (d < min) min = d;
    }
    return min;
  };
}
