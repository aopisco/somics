/** Drifting pollen: a single point cloud that rises and loops around the body. */

import { useFrame } from "@react-three/fiber";
import type { JSX } from "react";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { SKY } from "../theme";
import { hashNoise } from "../whimsy/motion";

const MOTE_COUNT = 300;
const RADIUS = 11;
const MIN_HEIGHT = 0;
const MAX_HEIGHT = 14;
const RISE_SPEED: [number, number] = [0.35, 1.1];
const DRIFT_AMPLITUDE = 0.6;
const DRIFT_RATE = 0.4;

interface MoteField {
  baseX: Float32Array;
  baseZ: Float32Array;
  y: Float32Array;
  speed: Float32Array;
  phase: Float32Array;
}

function seedMotes(): MoteField {
  const baseX = new Float32Array(MOTE_COUNT);
  const baseZ = new Float32Array(MOTE_COUNT);
  const y = new Float32Array(MOTE_COUNT);
  const speed = new Float32Array(MOTE_COUNT);
  const phase = new Float32Array(MOTE_COUNT);

  for (let i = 0; i < MOTE_COUNT; i++) {
    const base = i * 5;
    const angle = hashNoise(base) * Math.PI * 2;
    const radius = RADIUS * hashNoise(base + 1) ** 0.5;
    baseX[i] = Math.cos(angle) * radius;
    baseZ[i] = Math.sin(angle) * radius;
    y[i] = MIN_HEIGHT + (MAX_HEIGHT - MIN_HEIGHT) * hashNoise(base + 2);
    speed[i] = RISE_SPEED[0] + (RISE_SPEED[1] - RISE_SPEED[0]) * hashNoise(base + 3);
    phase[i] = hashNoise(base + 4) * Math.PI * 2;
  }

  return { baseX, baseZ, y, speed, phase };
}

export function Motes(props: { fade: number }): JSX.Element {
  const { fade } = props;
  const pointsRef = useRef<THREE.Points>(null);
  const materialRef = useRef<THREE.PointsMaterial>(null);
  const field = useMemo(() => seedMotes(), []);

  const positions = useMemo(() => new Float32Array(MOTE_COUNT * 3), []);
  const geometry = useMemo(() => {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geom;
  }, [positions]);

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;
    for (let i = 0; i < MOTE_COUNT; i++) {
      field.y[i] += field.speed[i] * delta;
      const span = MAX_HEIGHT - MIN_HEIGHT;
      const wrapped = MIN_HEIGHT + ((((field.y[i] - MIN_HEIGHT) % span) + span) % span);
      field.y[i] = wrapped;

      const drift = Math.sin(t * DRIFT_RATE + field.phase[i]) * DRIFT_AMPLITUDE;
      positions[i * 3] = field.baseX[i] + drift;
      positions[i * 3 + 1] = wrapped;
      positions[i * 3 + 2] = field.baseZ[i] + Math.cos(t * DRIFT_RATE + field.phase[i]) * DRIFT_AMPLITUDE;
    }
    geometry.attributes.position.needsUpdate = true;
    if (materialRef.current) materialRef.current.opacity = fade;
    if (pointsRef.current) pointsRef.current.visible = fade > 0.01;
  });

  return (
    <points ref={pointsRef} geometry={geometry}>
      <pointsMaterial
        ref={materialRef}
        color={SKY.sun}
        size={0.22}
        sizeAttenuation
        transparent
        opacity={fade}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
