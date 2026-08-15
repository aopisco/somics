/** Pins on the body — the affordance that makes it clickable. One pin per organ:
 *  a live glowing marker where the atlas has a sample, a small dark socket where it
 *  doesn't. Clicking either hands off to the store; the panel and camera rig react.
 */

import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { CSSProperties, JSX } from "react";
import * as THREE from "three";

import { selectOrgans, selectSamplesByNode, useStore } from "../state";
import { MARKER, UI } from "../theme";
import { breathe, damp, hashNoise, springPop, sway } from "../whimsy/motion";

const PIN_HEIGHT = 1.2;
const PIN_HOVER_SCALE = 1.35;
const HOVER_DAMP_SPEED = 9;
const ENTRANCE_STAGGER = 0.08;
const ROTATE_SPEED = 0.35;
const BOB_RATE = 0.6;
const BOB_AMPLITUDE = 0.05;
const LIVE_HIT_RADIUS = 0.45;
const EMPTY_HIT_RADIUS = 0.32;
const LABEL_DISTANCE_FACTOR = 9;
const GLOW_SCALE = 1.3;

const pinGeometry = new THREE.OctahedronGeometry(0.28, 0);
const stemGeometry = new THREE.CylinderGeometry(0.045, 0.06, 1, 6);
const dotGeometry = new THREE.SphereGeometry(0.11, 8, 6);
const hitGeometry = new THREE.SphereGeometry(1, 8, 6);

const pinMaterial = new THREE.MeshStandardMaterial({
  color: MARKER.live,
  emissive: new THREE.Color(MARKER.live),
  emissiveIntensity: 0.65,
  roughness: 0.35,
  metalness: 0.05,
  transparent: true,
});
const glowMaterial = new THREE.SpriteMaterial({
  color: MARKER.liveGlow,
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});
const stemMaterial = new THREE.MeshBasicMaterial({
  color: MARKER.live,
  transparent: true,
  depthWrite: false,
});
const emptyMaterial = new THREE.MeshBasicMaterial({
  color: MARKER.empty,
  transparent: true,
});
const hitMaterial = new THREE.MeshBasicMaterial({
  transparent: true,
  opacity: 0,
  depthWrite: false,
});

const LABEL_STYLE: CSSProperties = {
  background: UI.panel,
  border: `1px solid ${UI.panelEdge}`,
  borderRadius: 999,
  padding: "4px 10px",
  color: UI.text,
  fontSize: 12,
  whiteSpace: "nowrap",
  pointerEvents: "none",
};

const BADGE_STYLE: CSSProperties = {
  background: UI.accent,
  color: "#1c1406",
  borderRadius: 999,
  padding: "1px 6px",
  fontSize: 10,
  fontWeight: 600,
  whiteSpace: "nowrap",
  pointerEvents: "none",
};

export function SampleMarkers(props: { fade: number }): JSX.Element | null {
  const store = useStore();
  const organs = selectOrgans(store);
  const samplesByNode = selectSamplesByNode(store);

  const mountTimeRef = useRef<number | null>(null);
  const hoverScaleRef = useRef<Map<string, number>>(new Map());
  const pinGroupRefs = useRef<Map<string, THREE.Group>>(new Map());

  useFrame((state, delta) => {
    if (mountTimeRef.current === null) mountTimeRef.current = state.clock.elapsedTime;
    const elapsed = state.clock.elapsedTime - mountTimeRef.current;
    const fade = props.fade;

    pinMaterial.opacity = fade;
    glowMaterial.opacity = 0.5 * fade;
    stemMaterial.opacity = 0.45 * fade;
    emptyMaterial.opacity = 0.25 * fade;

    organs.forEach((organ, index) => {
      const group = pinGroupRefs.current.get(organ.node_id);
      if (!group) return;

      const targetScale = store.hoveredNode === organ.node_id ? PIN_HOVER_SCALE : 1;
      const currentScale = hoverScaleRef.current.get(organ.node_id) ?? targetScale;
      const dampedScale = damp(currentScale, targetScale, HOVER_DAMP_SPEED, delta);
      hoverScaleRef.current.set(organ.node_id, dampedScale);

      const samples = samplesByNode[organ.node_id] ?? [];
      if (samples.length === 0) {
        group.scale.setScalar(dampedScale);
        return;
      }

      const phase = hashNoise(index) * 100;
      const entranceT = elapsed - index * ENTRANCE_STAGGER;
      const pop = entranceT <= 0 ? 0 : springPop(Math.min(entranceT, 1));
      const breatheScale = breathe(elapsed + phase, 0.2, 0.05);
      const bob = sway(elapsed, phase, BOB_RATE, BOB_AMPLITUDE);

      group.position.y = PIN_HEIGHT + bob;
      group.rotation.y = elapsed * ROTATE_SPEED + phase;
      group.scale.setScalar(pop * dampedScale * breatheScale);
    });
  });

  if (props.fade <= 0.01) return null;

  return (
    <>
      {organs.map((organ) => {
        const samples = samplesByNode[organ.node_id] ?? [];
        const isLive = samples.length > 0;
        const hovered = store.hoveredNode === organ.node_id;
        const totalCells = samples.reduce((sum, sample) => sum + sample.n_cells, 0);

        const registerGroup = (el: THREE.Group | null) => {
          if (el) pinGroupRefs.current.set(organ.node_id, el);
          else pinGroupRefs.current.delete(organ.node_id);
        };

        return (
          <group key={organ.node_id} position={organ.anchor}>
            {isLive && (
              <mesh
                geometry={stemGeometry}
                material={stemMaterial}
                position={[0, PIN_HEIGHT / 2, 0]}
                scale={[1, PIN_HEIGHT, 1]}
                renderOrder={4}
              />
            )}

            <group ref={registerGroup} scale={0}>
              {isLive ? (
                <>
                  <sprite material={glowMaterial} scale={[GLOW_SCALE, GLOW_SCALE, 1]} renderOrder={5} />
                  <mesh geometry={pinGeometry} material={pinMaterial} renderOrder={7} />
                </>
              ) : (
                <mesh geometry={dotGeometry} material={emptyMaterial} renderOrder={3} />
              )}

              <mesh
                geometry={hitGeometry}
                material={hitMaterial}
                scale={isLive ? LIVE_HIT_RADIUS : EMPTY_HIT_RADIUS}
                renderOrder={8}
                onPointerOver={(event) => {
                  event.stopPropagation();
                  store.setHoveredNode(organ.node_id);
                }}
                onPointerOut={(event) => {
                  event.stopPropagation();
                  store.setHoveredNode(null);
                }}
                onClick={(event) => {
                  event.stopPropagation();
                  if (!isLive) {
                    store.selectNode(organ.node_id);
                    return;
                  }
                  if (samples.length === 1) store.selectSample(samples[0].section_uid);
                  else store.selectNode(organ.node_id);
                }}
              />

              {isLive && samples.length > 1 && (
                <Html position={[0.32, 0.3, 0]} center distanceFactor={LABEL_DISTANCE_FACTOR} style={BADGE_STYLE}>
                  {samples.length}
                </Html>
              )}

              {hovered && (
                <Html
                  position={[0, isLive ? 0.55 : 0.3, 0]}
                  center
                  distanceFactor={LABEL_DISTANCE_FACTOR}
                  style={LABEL_STYLE}
                >
                  {isLive
                    ? `${organ.label} · ${samples.length === 1 ? "1 sample" : `${samples.length} samples`} · ${totalCells.toLocaleString()} cells`
                    : `${organ.label} · no data yet`}
                </Html>
              )}
            </group>
          </group>
        );
      })}
    </>
  );
}
