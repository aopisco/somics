/**
 * Owns the R3F camera and its OrbitControls. Flies between zoom levels on
 * navigation, lets the user always orbit/wheel-zoom, and reports both the
 * settled camera and manual-zoom level changes back to the store.
 *
 * `flyRequest` is the only store field this component subscribes to
 * reactively. setCamera/setLod (below) never touch flyRequest, so writing
 * them back into the store can't loop into another tween — everything else
 * (lod, node, sample, species, anatomy, camera) is read imperatively via
 * `useStore.getState()` at the moment it's needed.
 */

import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import type { JSX } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";

import { focusFor, lodForDistance, TWEEN_SECONDS } from "./lod";
import { selectOrgan, useStore } from "../state";
import type { CameraState, Vec3 } from "../types";
import { easeInOutCubic } from "../whimsy/motion";

interface Tween {
  fromPosition: THREE.Vector3;
  fromTarget: THREE.Vector3;
  toPosition: THREE.Vector3;
  toTarget: THREE.Vector3;
  elapsed: number;
}

const scratchPosition = new THREE.Vector3();
const scratchTarget = new THREE.Vector3();

function currentBounds(): [Vec3, Vec3] | null {
  const { anatomy, species } = useStore.getState();
  return anatomy?.bodies[species]?.bounds ?? null;
}

function currentAnchor(): Vec3 | null {
  const state = useStore.getState();
  return selectOrgan(state, state.node)?.anchor ?? null;
}

function readCameraState(camera: THREE.Camera, controls: OrbitControlsImpl): CameraState {
  return {
    position: [camera.position.x, camera.position.y, camera.position.z],
    target: [controls.target.x, controls.target.y, controls.target.z],
  };
}

export function CameraRig(): JSX.Element {
  const { camera } = useThree();
  const controlsRef = useRef<OrbitControlsImpl>(null);
  const tweenRef = useRef<Tween | null>(null);
  const skipNextTweenRef = useRef(false);
  const flyRequest = useStore((s) => s.flyRequest);

  // A shared link's exact camera wins over a tween on load; consumed so the
  // paired effect below doesn't also fly for that same initial flyRequest.
  useEffect(() => {
    const stored = useStore.getState().camera;
    const controls = controlsRef.current;
    if (stored && controls) {
      camera.position.set(...stored.position);
      controls.target.set(...stored.target);
      controls.update();
      skipNextTweenRef.current = true;
    }
  }, [camera]);

  useEffect(() => {
    if (skipNextTweenRef.current) {
      skipNextTweenRef.current = false;
      return;
    }
    const controls = controlsRef.current;
    const bounds = currentBounds();
    if (!controls || !bounds) return;

    const focus = focusFor({ lod: useStore.getState().lod, bounds, anchor: currentAnchor() });
    tweenRef.current = {
      fromPosition: camera.position.clone(),
      fromTarget: controls.target.clone(),
      toPosition: new THREE.Vector3(...focus.position),
      toTarget: new THREE.Vector3(...focus.target),
      elapsed: 0,
    };
  }, [flyRequest, camera]);

  useFrame((state, delta) => {
    const controls = controlsRef.current;
    if (!controls) return;
    const activeCamera = state.camera;

    const tween = tweenRef.current;
    if (tween) {
      tween.elapsed += delta;
      const t = easeInOutCubic(tween.elapsed / TWEEN_SECONDS);
      scratchPosition.lerpVectors(tween.fromPosition, tween.toPosition, t);
      scratchTarget.lerpVectors(tween.fromTarget, tween.toTarget, t);
      activeCamera.position.copy(scratchPosition);
      controls.target.copy(scratchTarget);
      controls.update();
      if (tween.elapsed >= TWEEN_SECONDS) {
        tweenRef.current = null;
        useStore.getState().setCamera(readCameraState(activeCamera, controls));
      }
      return;
    }

    const bounds = currentBounds();
    if (!bounds) return;
    const distance = activeCamera.position.distanceTo(controls.target);
    let lod = lodForDistance(distance, bounds);
    const { sample, lod: storeLod, setLod } = useStore.getState();
    // Manual zoom can't reach section/cell without a loaded sample to show there.
    if (!sample && (lod === "section" || lod === "cell")) lod = "organ";
    if (lod !== storeLod) setLod(lod);
  });

  const handleTweenInterrupt = () => {
    tweenRef.current = null;
  };

  const handleSettled = () => {
    const controls = controlsRef.current;
    if (!controls) return;
    useStore.getState().setCamera(readCameraState(camera, controls));
  };

  return (
    <OrbitControls
      ref={controlsRef}
      enabled
      makeDefault
      onStart={handleTweenInterrupt}
      onEnd={handleSettled}
    />
  );
}
