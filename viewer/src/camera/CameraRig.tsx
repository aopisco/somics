/**
 * Owns the R3F camera and its OrbitControls. Flies between zoom levels on
 * navigation, lets the user always orbit/wheel-zoom, and reports both the
 * settled camera and manual-zoom level changes back to the store.
 *
 * Every one of those jobs runs in the frame loop, deliberately. The rig both moves
 * the camera and reads a level back off where the camera is, and the two only agree
 * if the read never happens before the move. Starting the move from a `useEffect`
 * did not give that: `useFrame` subscribes in a layout effect, so a rAF can land
 * between the commit and React flushing passive effects. The level-from-distance
 * read then fired against the un-flown default camera (35 units out, i.e. "orbit")
 * and overwrote the level in the URL that had asked for the fly — after which the
 * fly, running a frame later, flew to that overwritten level. Sequencing the whole
 * thing inside one `useFrame` is what stops it; `cameraStep` holds the rule.
 *
 * No store write here can loop back into another move: setCamera/setLod never touch
 * `flyRequest`. It is read imperatively per frame rather than subscribed to, as is
 * everything else (lod, node, sample, species, anatomy, camera) — this component
 * has no reason to re-render.
 */

import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useRef } from "react";
import type { JSX } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";

import { cameraStep, manualLod, TWEEN_SECONDS } from "./lod";
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
  /** The `flyRequest` the camera has already been placed for; null before any. */
  const placedForRef = useRef<number | null>(null);

  useFrame((state, delta) => {
    const controls = controlsRef.current;
    if (!controls) return;
    const activeCamera = state.camera;
    const store = useStore.getState();
    const bounds = currentBounds();

    const step = cameraStep({
      flyRequest: store.flyRequest,
      placedFor: placedForRef.current,
      bounds,
      lod: store.lod,
      anchor: currentAnchor(),
      storedCamera: store.camera,
    });

    switch (step.kind) {
      case "wait":
        // Anatomy has not landed, so there is nowhere to fly and nothing may read
        // the camera. Sit still; a later frame picks the same navigation back up.
        return;
      case "snap":
        placedForRef.current = store.flyRequest;
        tweenRef.current = null;
        activeCamera.position.set(...step.camera.position);
        controls.target.set(...step.camera.target);
        controls.update();
        return;
      case "fly":
        placedForRef.current = store.flyRequest;
        tweenRef.current = {
          fromPosition: activeCamera.position.clone(),
          fromTarget: controls.target.clone(),
          toPosition: new THREE.Vector3(...step.focus.position),
          toTarget: new THREE.Vector3(...step.focus.target),
          elapsed: 0,
        };
        break;
      case "settled":
        break;
    }

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
        store.setCamera(readCameraState(activeCamera, controls));
      }
      return;
    }

    // Settled: the camera is where this navigation put it, so the level may follow
    // wherever the user has since orbited or wheeled it to.
    if (!bounds) return;
    const distance = activeCamera.position.distanceTo(controls.target);
    const lod = manualLod(distance, bounds, store.sample !== null);
    if (lod !== store.lod) store.setLod(lod);
  });

  // The user grabbing the controls outranks a fly in progress.
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
