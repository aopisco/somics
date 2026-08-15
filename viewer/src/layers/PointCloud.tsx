/** The section-level point cloud: 587,115 cells rendered as a coloured THREE.Points cloud. */

import type { ThreeEvent } from "@react-three/fiber";
import { useFrame } from "@react-three/fiber";
import type { JSX } from "react";
import { useEffect, useMemo } from "react";
import * as THREE from "three";

import { SECTION_SIZE, sectionTransform } from "../camera/lod";
import { selectOrgan, useStore } from "../state";
import type { GeneValues, Paint, PointCloud as PointCloudData, Vec3 } from "../types";
import { damp } from "../whimsy/motion";
import { magma, normalize, viridis } from "./colormap";

const ORIGIN: Vec3 = [0, 0, 0];
const POINT_SIZE = 0.006 * SECTION_SIZE;
const OPACITY_RAMP_SPEED = 5;

function buildGeometry(
  points: PointCloudData | null,
  paint: Paint,
  geneValues: GeneValues | null,
): THREE.BufferGeometry | null {
  if (!points) return null;
  const n = points.x.length;
  const positions = new Float32Array(n * 3);
  const colors = new Float32Array(n * 3);
  const useGene = paint === "gene" && geneValues !== null && geneValues.values.length === n;
  const range = useGene ? geneValues.meta.value_range : points.meta.count_range;
  for (let i = 0; i < n; i++) {
    positions[i * 3] = points.x[i];
    positions[i * 3 + 1] = points.y[i];
    positions[i * 3 + 2] = 0;
    const value = useGene ? geneValues.values[i] : points.counts[i];
    const [r, g, b] = useGene ? magma(normalize(value, range)) : viridis(normalize(value, range));
    colors[i * 3] = r;
    colors[i * 3 + 1] = g;
    colors[i * 3 + 2] = b;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  return geometry;
}

export function PointCloudLayer(props: { opacity: number }): JSX.Element | null {
  const points = useStore((s) => s.points);
  const paint = useStore((s) => s.paint);
  const geneValues = useStore((s) => s.geneValues);
  const anchor = useStore((s) => selectOrgan(s, s.node)?.anchor ?? null);
  const setFocusUm = useStore((s) => s.setFocusUm);
  const setLod = useStore((s) => s.setLod);

  const geometry = useMemo(
    () => buildGeometry(points, paint, geneValues),
    [points, paint, geneValues],
  );
  useEffect(() => () => geometry?.dispose(), [geometry]);

  const planeGeometry = useMemo(() => {
    if (!points) return null;
    const [xMin, yMin, xMax, yMax] = points.meta.extent_um;
    return new THREE.PlaneGeometry(
      (xMax - xMin) / points.meta.scale_um,
      (yMax - yMin) / points.meta.scale_um,
    );
  }, [points]);
  useEffect(() => () => planeGeometry?.dispose(), [planeGeometry]);

  const material = useMemo(
    () =>
      new THREE.PointsMaterial({
        vertexColors: true,
        sizeAttenuation: true,
        transparent: true,
        depthWrite: false,
        size: POINT_SIZE,
        opacity: 0,
      }),
    [],
  );
  useEffect(() => () => material.dispose(), [material]);

  // A new points object means a fresh section: restart the opacity ramp from zero.
  useEffect(() => {
    material.opacity = 0;
  }, [points, material]);

  useFrame((_, delta) => {
    material.opacity = damp(material.opacity, props.opacity, OPACITY_RAMP_SPEED, delta);
  });

  function handleSectionClick(event: ThreeEvent<MouseEvent>): void {
    event.stopPropagation();
    if (!points || !planeGeometry || !event.uv) return;
    const { width, height } = planeGeometry.parameters;
    const [xMin, yMin, xMax, yMax] = points.meta.extent_um;
    const centreX = (xMin + xMax) / 2;
    const centreY = (yMin + yMax) / 2;
    // Inverse of the server's x_norm = (x_um - centre) / scale_um.
    const xUm = centreX + (event.uv.x - 0.5) * width * points.meta.scale_um;
    const yUm = centreY + (event.uv.y - 0.5) * height * points.meta.scale_um;
    setFocusUm([xUm, yUm]);
    setLod("cell");
  }

  if (props.opacity <= 0.01 || !points || !geometry) return null;

  const transform = sectionTransform(anchor ?? ORIGIN);

  return (
    <group position={transform.position} scale={transform.scale}>
      <points geometry={geometry} material={material} />
      {planeGeometry && (
        <mesh geometry={planeGeometry} onClick={handleSectionClick}>
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      )}
    </group>
  );
}
