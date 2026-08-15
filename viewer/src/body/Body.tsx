/** The voxel body: a translucent glass shell with glowing organs inside. Built once
 *  per (anatomy, species) as instanced boxes; only rotation, breathing scale and
 *  material intensities move per frame.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type { JSX } from "react";
import * as THREE from "three";

import { selectSamplesByNode, useStore } from "../state";
import { BODY, VOXEL } from "../theme";
import { breathe, damp, twitch, wobble } from "../whimsy/motion";
import { buildBodyVoxels } from "./voxelize";
import type { BodyVoxels, VoxelField } from "./voxelize";

const GEOMETRY_SCALE = 0.92;
const ORBIT_ROTATE_SPEED = 0.12;
const SHELL_OPACITY = 0.16;
const BASE_EMISSIVE = 1.1;
const DIM_SCALE = 1 / 3;
const TWITCH_BOOST = 0.55;
const HOVER_BOOST = 0.6;
const HOVER_LIFT = 0.35;
const HOVER_DAMP_SPEED = 8;
const ORGAN_PHASE_STEP = 0.83;

const tempMatrix = new THREE.Matrix4();
const tempPosition = new THREE.Vector3();
const tempQuaternion = new THREE.Quaternion();
const tempScale = new THREE.Vector3(1, 1, 1);
const DESATURATE_TARGET = new THREE.Color("#888888");

interface OrganState {
  nodeId: string;
  color: THREE.Color;
  hasSamples: boolean;
  phase: number;
}

function setInstancePositions(mesh: THREE.InstancedMesh, field: VoxelField): void {
  for (let i = 0; i < field.count; i++) {
    tempPosition.set(field.positions[i * 3], field.positions[i * 3 + 1], field.positions[i * 3 + 2]);
    tempMatrix.compose(tempPosition, tempQuaternion, tempScale);
    mesh.setMatrixAt(i, tempMatrix);
  }
  mesh.instanceMatrix.needsUpdate = true;
}

export function Body(props: { fade: number }): JSX.Element | null {
  const { fade } = props;

  const anatomy = useStore((s) => s.anatomy);
  const species = useStore((s) => s.species);
  const node = useStore((s) => s.node);
  const hoveredNode = useStore((s) => s.hoveredNode);
  const lod = useStore((s) => s.lod);
  const samplesByNode = useStore(selectSamplesByNode);
  const setHoveredNode = useStore((s) => s.setHoveredNode);
  const selectNode = useStore((s) => s.selectNode);

  const groupRef = useRef<THREE.Group>(null);
  const shellRef = useRef<THREE.InstancedMesh>(null);
  const organMeshes = useRef<Map<string, THREE.InstancedMesh>>(new Map());
  const organMaterials = useRef<Map<string, THREE.MeshStandardMaterial>>(new Map());

  const voxels: BodyVoxels | null = useMemo(() => {
    const body = anatomy?.bodies[species];
    if (!body) return null;
    return buildBodyVoxels(body, species);
  }, [anatomy, species]);

  const geometry = useMemo(() => {
    const size = (voxels?.voxel ?? VOXEL) * GEOMETRY_SCALE;
    return new THREE.BoxGeometry(size, size, size);
  }, [voxels]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  const organStates: OrganState[] = useMemo(() => {
    if (!voxels) return [];
    return voxels.organs.map((organ, index) => {
      const hasSamples = (samplesByNode[organ.nodeId]?.length ?? 0) > 0;
      const base = new THREE.Color(organ.color);
      const color = hasSamples ? base : base.clone().lerp(DESATURATE_TARGET, 0.55);
      return { nodeId: organ.nodeId, color, hasSamples, phase: index * ORGAN_PHASE_STEP };
    });
  }, [voxels, samplesByNode]);

  useEffect(() => {
    if (!voxels || !shellRef.current) return;
    setInstancePositions(shellRef.current, voxels.shell);
  }, [voxels]);

  useEffect(() => {
    if (!voxels) return;
    for (const organ of voxels.organs) {
      const mesh = organMeshes.current.get(organ.nodeId);
      if (mesh) setInstancePositions(mesh, organ.field);
    }
  }, [voxels]);

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;
    const group = groupRef.current;
    if (group) {
      if (lod === "orbit") group.rotation.y += (ORBIT_ROTATE_SPEED + wobble(t)) * delta;
      group.scale.setScalar(breathe(t));
    }

    for (const organ of organStates) {
      // Selection persists the lift/glow after the pointer leaves, hover is momentary.
      const isEmphasized = hoveredNode === organ.nodeId || node === organ.nodeId;

      const mesh = organMeshes.current.get(organ.nodeId);
      if (mesh) {
        mesh.position.y = damp(mesh.position.y, isEmphasized ? HOVER_LIFT : 0, HOVER_DAMP_SPEED, delta);
      }

      const material = organMaterials.current.get(organ.nodeId);
      if (material) {
        const base = organ.hasSamples
          ? BASE_EMISSIVE * breathe(t + organ.phase, 0.16, 0.35) +
            twitch(t, 1.7, 0.055, organ.phase) * TWITCH_BOOST
          : BASE_EMISSIVE * DIM_SCALE;
        material.emissiveIntensity = isEmphasized ? base + HOVER_BOOST : base;
      }
    }
  });

  if (!voxels || fade <= 0.01) return null;

  return (
    <group ref={groupRef}>
      <instancedMesh ref={shellRef} args={[geometry, undefined, voxels.shell.count]}>
        <meshStandardMaterial
          color={BODY.shell}
          transparent
          opacity={SHELL_OPACITY * fade}
          depthWrite={false}
          flatShading
        />
      </instancedMesh>

      {voxels.organs.map((organ, index) => {
        const state = organStates[index];
        return (
          <instancedMesh
            key={organ.nodeId}
            ref={(mesh) => {
              if (mesh) organMeshes.current.set(organ.nodeId, mesh);
              else organMeshes.current.delete(organ.nodeId);
            }}
            args={[geometry, undefined, organ.field.count]}
            onPointerOver={(event) => {
              event.stopPropagation();
              setHoveredNode(organ.nodeId);
            }}
            onPointerOut={(event) => {
              event.stopPropagation();
              setHoveredNode(null);
            }}
            onClick={(event) => {
              event.stopPropagation();
              selectNode(organ.nodeId);
            }}
          >
            <meshStandardMaterial
              ref={(material) => {
                if (material) organMaterials.current.set(organ.nodeId, material);
                else organMaterials.current.delete(organ.nodeId);
              }}
              color={state.color}
              emissive={state.color}
              emissiveIntensity={state.hasSamples ? BASE_EMISSIVE : BASE_EMISSIVE * DIM_SCALE}
              flatShading
            />
          </instancedMesh>
        );
      })}
    </group>
  );
}
