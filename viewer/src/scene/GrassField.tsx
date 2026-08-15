/** The wild-grass field the body stands in: soil, blades and a horizon silhouette. */

import { useFrame } from "@react-three/fiber";
import type { JSX } from "react";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";

import type { Species } from "../types";
import { GROUND } from "../theme";
import { hashNoise, sway } from "../whimsy/motion";

const BLADE_COUNT = 6000;
const HILL_COUNT = 32;
const FIELD_RADIUS = 90;
const HILL_RADIUS: [number, number] = [95, 108];
const BLADE_WIDTH = 0.18;
const BLADE_DEPTH = 0.18;
const RADIAL_BIAS = 2.2;
const MAX_LEAN = 0.5;
const TAU = Math.PI * 2;

/** Grass reads as belly-high on the rat and knee-high on the much taller human. */
const HEIGHT_RANGE: Record<Species, [number, number]> = {
  rat: [1.1, 2.2],
  human: [0.6, 1.3],
};

// Box centred on its base (local y in [0, 1]) so scaling and rotation both pivot at the ground.
const BOX_GEOMETRY = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
const LEAN_AXIS = new THREE.Vector3(0, 0, 1);

const tempPosition = new THREE.Vector3();
const tempQuaternion = new THREE.Quaternion();
const tempScale = new THREE.Vector3();
const tempMatrix = new THREE.Matrix4();

interface Scatter {
  x: Float32Array;
  z: Float32Array;
  heightSeed: Float32Array;
  phase: Float32Array;
  colors: Float32Array;
}

function scatterField(count: number, seedOffset: number): Scatter {
  const x = new Float32Array(count);
  const z = new Float32Array(count);
  const heightSeed = new Float32Array(count);
  const phase = new Float32Array(count);
  const colors = new Float32Array(count * 3);

  const low = new THREE.Color(GROUND.grassLow);
  const high = new THREE.Color(GROUND.grassHigh);
  const dry = new THREE.Color(GROUND.grassDry);
  const color = new THREE.Color();

  for (let i = 0; i < count; i++) {
    const base = (seedOffset + i) * 5;
    const angle = hashNoise(base) * TAU;
    const radiusSeed = hashNoise(base + 1);
    const radius = FIELD_RADIUS * radiusSeed ** RADIAL_BIAS;
    x[i] = Math.cos(angle) * radius;
    z[i] = Math.sin(angle) * radius;
    heightSeed[i] = hashNoise(base + 2);
    phase[i] = hashNoise(base + 3) * TAU;

    const mix = hashNoise(base + 4);
    if (mix < 0.5) color.lerpColors(low, high, mix * 2);
    else color.lerpColors(high, dry, (mix - 0.5) * 2);
    colors[i * 3] = color.r;
    colors[i * 3 + 1] = color.g;
    colors[i * 3 + 2] = color.b;
  }

  return { x, z, heightSeed, phase, colors };
}

export function GrassField(props: { fade: number; species: Species }): JSX.Element {
  const { fade, species } = props;
  const bladesRef = useRef<THREE.InstancedMesh>(null);
  const hillsRef = useRef<THREE.InstancedMesh>(null);
  const soilRef = useRef<THREE.MeshStandardMaterial>(null);
  const groundRef = useRef<THREE.MeshStandardMaterial>(null);
  const bladeMaterialRef = useRef<THREE.MeshStandardMaterial>(null);
  const hillMaterialRef = useRef<THREE.MeshStandardMaterial>(null);

  const field = useMemo(() => scatterField(BLADE_COUNT, 0), []);
  const hills = useMemo(() => scatterField(HILL_COUNT, 90_000), []);

  const heights = useMemo(() => {
    const [min, max] = HEIGHT_RANGE[species];
    const out = new Float32Array(BLADE_COUNT);
    for (let i = 0; i < BLADE_COUNT; i++) out[i] = min + (max - min) * field.heightSeed[i] ** 0.7;
    return out;
  }, [field, species]);

  const hillColor = useMemo(() => new THREE.Color(GROUND.grassLow).multiplyScalar(0.45), []);

  useEffect(() => {
    const mesh = bladesRef.current;
    if (!mesh) return;
    mesh.instanceColor = new THREE.InstancedBufferAttribute(field.colors, 3);
    mesh.instanceColor.needsUpdate = true;
  }, [field]);

  useEffect(() => {
    const mesh = hillsRef.current;
    if (!mesh) return;
    const [minR, maxR] = HILL_RADIUS;
    for (let i = 0; i < HILL_COUNT; i++) {
      const angle = hills.phase[i];
      const radius = minR + (maxR - minR) * hills.heightSeed[i];
      tempPosition.set(Math.cos(angle) * radius, 0, Math.sin(angle) * radius);
      tempQuaternion.identity();
      const height = 3 + hills.heightSeed[i] * 9;
      tempScale.set(5 + hashNoise(i * 7) * 8, height, 5 + hashNoise(i * 7 + 1) * 8);
      tempMatrix.compose(tempPosition, tempQuaternion, tempScale);
      mesh.setMatrixAt(i, tempMatrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
  }, [hills]);

  useFrame((state) => {
    if (fade <= 0.01) return;
    const t = state.clock.elapsedTime;
    const mesh = bladesRef.current;
    if (mesh) {
      for (let i = 0; i < BLADE_COUNT; i++) {
        const x = field.x[i];
        const z = field.z[i];
        const offset = x * 0.18 + z * 0.11 + field.phase[i];
        const angle = sway(t, offset) * MAX_LEAN;
        tempPosition.set(x, 0, z);
        tempQuaternion.setFromAxisAngle(LEAN_AXIS, angle);
        tempScale.set(BLADE_WIDTH, heights[i], BLADE_DEPTH);
        tempMatrix.compose(tempPosition, tempQuaternion, tempScale);
        mesh.setMatrixAt(i, tempMatrix);
      }
      mesh.instanceMatrix.needsUpdate = true;
    }

    if (soilRef.current) soilRef.current.opacity = fade;
    if (groundRef.current) groundRef.current.opacity = fade;
    if (bladeMaterialRef.current) bladeMaterialRef.current.opacity = fade;
    if (hillMaterialRef.current) hillMaterialRef.current.opacity = fade;
  });

  if (fade <= 0.01) return <></>;

  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]}>
        <circleGeometry args={[140, 48]} />
        <meshStandardMaterial ref={soilRef} color={GROUND.soil} transparent roughness={1} />
      </mesh>

      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}>
        <circleGeometry args={[FIELD_RADIUS + 5, 48]} />
        <meshStandardMaterial ref={groundRef} color={GROUND.grassLow} transparent roughness={1} />
      </mesh>

      <instancedMesh ref={bladesRef} args={[BOX_GEOMETRY, undefined, BLADE_COUNT]}>
        <meshStandardMaterial ref={bladeMaterialRef} vertexColors transparent roughness={0.85} />
      </instancedMesh>

      <instancedMesh ref={hillsRef} args={[BOX_GEOMETRY, undefined, HILL_COUNT]}>
        <meshStandardMaterial ref={hillMaterialRef} color={hillColor} transparent roughness={1} />
      </instancedMesh>
    </group>
  );
}
