/** The wild-grass field the body stands in: a ground disc and swaying blades.
 *
 * There used to be a ring of dark boxes out at radius ~100 standing in for hills. The
 * sky is a photographed alpine valley now and has its own ridgeline, so the boxes were
 * cubes floating in front of real mountains. They are gone; the horizon is the plate's.
 */

import { useFrame } from "@react-three/fiber";
import type { JSX } from "react";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import type { Species } from "../types";
import { GROUND } from "../theme";
import { hashNoise, sway } from "../whimsy/motion";

const BLADE_COUNT = 6000;
/** Where blades are scattered. */
const FIELD_RADIUS = 90;
/** Where the flat ground stops and the panorama's own field takes over. */
const GROUND_RADIUS = 140;
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

function scatterField(count: number): Scatter {
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
    const base = i * 5;
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
  const groundRef = useRef<THREE.MeshStandardMaterial>(null);
  const bladeMaterialRef = useRef<THREE.MeshStandardMaterial>(null);

  const field = useMemo(() => scatterField(BLADE_COUNT), []);

  const heights = useMemo(() => {
    const [min, max] = HEIGHT_RANGE[species];
    const out = new Float32Array(BLADE_COUNT);
    for (let i = 0; i < BLADE_COUNT; i++) out[i] = min + (max - min) * field.heightSeed[i] ** 0.7;
    return out;
  }, [field, species]);

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

    if (groundRef.current) groundRef.current.opacity = fade;
    if (bladeMaterialRef.current) bladeMaterialRef.current.opacity = fade;
  });

  if (fade <= 0.01) return <></>;

  return (
    <group>
      {/* One disc, out to where the plate's own field takes over. It used to be a green
          disc at FIELD_RADIUS + 5 over a wider soil disc, which put a brown ring between
          the blades and the horizon — invisible while dark boxes stood on it, a mud strip
          across the skyline once the boxes went and a photographed green valley showed
          through. Nothing is drawn under it now, so the soil layer went with them. */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]}>
        <circleGeometry args={[GROUND_RADIUS, 64]} />
        <meshStandardMaterial ref={groundRef} color={GROUND.grassLow} transparent roughness={1} />
      </mesh>

      {/* instanceColor is attached declaratively, not assigned in an effect, for two
          reasons. three decides USE_INSTANCING_COLOR when it compiles the program, so
          the attribute has to exist before the first render or the blades draw in the
          material's plain white. And this subtree unmounts whenever the field fades
          out at section level, so a fresh InstancedMesh is built on the way back —
          an effect keyed on the (memoised, never-changing) scatter would not re-run
          for it, which is why the grass came back white after a zoom.

          The material deliberately does not set `vertexColors`: that define expects a
          per-vertex `color` attribute, the shared BoxGeometry has none, and the shader
          then multiplies by an unbound attribute — which is what rendered every blade
          black regardless of the palette. */}
      <instancedMesh ref={bladesRef} args={[BOX_GEOMETRY, undefined, BLADE_COUNT]}>
        <instancedBufferAttribute attach="instanceColor" args={[field.colors, 3]} />
        <meshStandardMaterial ref={bladeMaterialRef} transparent roughness={0.85} />
      </instancedMesh>
    </group>
  );
}
