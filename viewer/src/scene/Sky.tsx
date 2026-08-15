/** The sky dome, sun and horizon haze — and the only lights in the scene. */

import { useFrame } from "@react-three/fiber";
import type { JSX } from "react";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { GROUND, SKY } from "../theme";

const DOME_RADIUS = 1200;
const SUN_DIRECTION = new THREE.Vector3(0.32, 0.3, -0.6).normalize();
const SUN_DISTANCE = 950;
const LIGHT_DISTANCE = 200;

const DOME_VERTEX_SHADER = `
  varying float vHeight;
  void main() {
    vHeight = normalize(position).y;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const DOME_FRAGMENT_SHADER = `
  uniform vec3 horizon;
  uniform vec3 zenith;
  uniform float opacity;
  varying float vHeight;
  void main() {
    float t = pow(clamp(vHeight, 0.0, 1.0), 0.55);
    gl_FragColor = vec4(mix(horizon, zenith, t), opacity);
  }
`;

const HAZE_FRAGMENT_SHADER = `
  uniform vec3 haze;
  uniform float opacity;
  varying float vHeight;
  void main() {
    float t = clamp(vHeight, 0.0, 1.0);
    float alpha = smoothstep(0.22, 0.0, t) * opacity;
    gl_FragColor = vec4(haze, alpha);
  }
`;

export function SkyDome(props: { fade: number }): JSX.Element {
  const { fade } = props;
  const directionalRef = useRef<THREE.DirectionalLight>(null);
  const hemisphereRef = useRef<THREE.HemisphereLight>(null);
  const ambientRef = useRef<THREE.AmbientLight>(null);

  const domeMaterial = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          horizon: { value: new THREE.Color(SKY.horizon) },
          zenith: { value: new THREE.Color(SKY.zenith) },
          opacity: { value: 1 },
        },
        vertexShader: DOME_VERTEX_SHADER,
        fragmentShader: DOME_FRAGMENT_SHADER,
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
        fog: false,
      }),
    [],
  );

  const hazeMaterial = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          haze: { value: new THREE.Color(SKY.haze) },
          opacity: { value: 1 },
        },
        vertexShader: DOME_VERTEX_SHADER,
        fragmentShader: HAZE_FRAGMENT_SHADER,
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
        fog: false,
      }),
    [],
  );

  const sunMaterial = useMemo(
    () => new THREE.SpriteMaterial({ color: SKY.sun, transparent: true, depthWrite: false, fog: false }),
    [],
  );

  const sunPosition = useMemo(
    () => SUN_DIRECTION.clone().multiplyScalar(SUN_DISTANCE),
    [],
  );
  const lightPosition = useMemo(
    () => SUN_DIRECTION.clone().multiplyScalar(LIGHT_DISTANCE),
    [],
  );

  useFrame(() => {
    domeMaterial.uniforms.opacity.value = fade;
    hazeMaterial.uniforms.opacity.value = fade;
    sunMaterial.opacity = fade;

    const litness = Math.max(fade, 0.25);
    if (directionalRef.current) directionalRef.current.intensity = 0.35 + 0.95 * litness;
    if (hemisphereRef.current) hemisphereRef.current.intensity = 0.25 + 0.5 * litness;
    if (ambientRef.current) ambientRef.current.intensity = 0.12 + 0.12 * litness;
  });

  return (
    <group>
      <directionalLight ref={directionalRef} color={SKY.sun} position={lightPosition} castShadow={false} />
      <hemisphereLight ref={hemisphereRef} args={[SKY.zenith, GROUND.grassLow, 0.5]} />
      <ambientLight ref={ambientRef} color="#ffffff" intensity={0.2} />

      <mesh renderOrder={-20}>
        <sphereGeometry args={[DOME_RADIUS, 32, 16]} />
        <primitive object={domeMaterial} attach="material" />
      </mesh>

      <mesh renderOrder={-10}>
        <sphereGeometry args={[DOME_RADIUS - 5, 32, 16]} />
        <primitive object={hazeMaterial} attach="material" />
      </mesh>

      <sprite position={sunPosition} scale={[130, 130, 1]} renderOrder={-15}>
        <primitive object={sunMaterial} attach="material" />
      </sprite>
    </group>
  );
}
