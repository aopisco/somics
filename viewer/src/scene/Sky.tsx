/** The 360 alpine-valley plate the scene stands in — and the only lights in the scene.
 *
 * This used to be a two-tone shader dome, which read as a backdrop card rather than a
 * place: the horizon was the same everywhere, so orbiting told you nothing. It is now a
 * real equirectangular photograph of a Swiss valley (see `public/env/CREDITS.md`), drawn
 * on the inside of a large sphere.
 *
 * An inverted sphere is used rather than drei's `<Environment background>` for two
 * reasons. The scene cross-fades the whole world out at data levels via `fade`, and a
 * mesh material has an `opacity` to drive from that where `scene.background` does not.
 * And `<Environment>` would also set `scene.environment`, which would silently change how
 * every body and organ material is lit — outside this task's remit.
 */

import { useFrame, useLoader } from "@react-three/fiber";
import type { JSX } from "react";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { GROUND, SKY } from "../theme";

const PANORAMA_URL = "/env/alps_field_3k.jpg";

/** Comfortably inside the camera's far plane (4000) and far outside the grass field (90). */
const DOME_RADIUS = 1200;
/** Segments around and over. UVs interpolate linearly across a triangle while the
 *  equirectangular mapping is not linear, so a coarse sphere kinks the horizon; 96 x 48
 *  puts under 4 degrees in a segment, which is below what the error shows up at. */
const DOME_WIDTH_SEGMENTS = 96;
const DOME_HEIGHT_SEGMENTS = 48;
const LIGHT_DISTANCE = 200;

/** Where the sun sits in the plate's own frame.
 *
 * Measured from the panorama's brightest pixel — 0.598 across, 0.263 down — mapped
 * through the UV layout `SphereGeometry` gives a sphere of this orientation: a pixel at
 * horizontal fraction `u` faces `(-cos 2πu, ·, sin 2πu)`, and one at vertical fraction
 * `v` sits at elevation `90° - 180°v`.
 */
const PLATE_SUN_AZIMUTH = THREE.MathUtils.degToRad(-35.3);
const PLATE_SUN_ELEVATION = THREE.MathUtils.degToRad(42.6);

/** How far the plate is spun about Y before it is hung in the scene.
 *
 * Two things had to be true at once and this angle is what satisfies both. `focusFor`'s
 * `ORBIT_DIR` puts the orbit camera at azimuth 56 degrees looking back across the body;
 * at this yaw that opens onto the valley's far end with the snow line in it, rather than
 * onto the near forested slope. And it swings the sun to azimuth 134, which is 78 degrees
 * off the camera's left and on the camera's own side of the body — unyawed, the sun sat
 * behind the body and rimmed it.
 */
const SKY_YAW = THREE.MathUtils.degToRad(191);

/** The plate's sun after the yaw. Deriving it here rather than hard-coding a vector is
 *  what keeps the key light on the sun you can see when someone retunes SKY_YAW. */
const SUN_DIRECTION = new THREE.Vector3(
  Math.cos(PLATE_SUN_AZIMUTH - SKY_YAW) * Math.cos(PLATE_SUN_ELEVATION),
  Math.sin(PLATE_SUN_ELEVATION),
  Math.sin(PLATE_SUN_AZIMUTH - SKY_YAW) * Math.cos(PLATE_SUN_ELEVATION),
);

/** Suspends until the panorama has loaded; App scopes that with its own Suspense. */
export function SkyDome(props: { fade: number }): JSX.Element {
  const { fade } = props;
  const directionalRef = useRef<THREE.DirectionalLight>(null);
  const hemisphereRef = useRef<THREE.HemisphereLight>(null);
  const ambientRef = useRef<THREE.AmbientLight>(null);

  const texture = useLoader(THREE.TextureLoader, PANORAMA_URL);

  const domeMaterial = useMemo(() => {
    // The JPEG is Poly Haven's tone-mapped render of the HDRI, so it is already a
    // display-referred image. r3f puts ACES filmic on the renderer by default; running
    // that over the plate a second time washes it out — measured on the default view,
    // sky #a4bdd7 goes to #b7c6d2 and the far snow line loses most of its separation
    // from the sky behind it. The lit geometry does still go through ACES, which is what
    // we want; only the photograph opts out.
    texture.colorSpace = THREE.SRGBColorSpace;
    return new THREE.MeshBasicMaterial({
      map: texture,
      side: THREE.BackSide,
      transparent: true,
      depthWrite: false,
      toneMapped: false,
      fog: false,
    });
  }, [texture]);

  const lightPosition = useMemo(
    () => SUN_DIRECTION.clone().multiplyScalar(LIGHT_DISTANCE),
    [],
  );

  useFrame(() => {
    domeMaterial.opacity = fade;

    // Brighter than the old shader dome asked for. The plate is drawn untone-mapped and
    // the geometry goes through ACES, so at the previous intensities the modelled ground
    // came out around #5c6950 against the photographed field's #808951 and the seam at
    // the edge of the grass disc read as a step. These land the two within a few percent.
    const litness = Math.max(fade, 0.25);
    if (directionalRef.current) directionalRef.current.intensity = 0.45 + 1.45 * litness;
    if (hemisphereRef.current) hemisphereRef.current.intensity = 0.3 + 0.7 * litness;
    if (ambientRef.current) ambientRef.current.intensity = 0.14 + 0.2 * litness;
  });

  return (
    <group>
      <directionalLight ref={directionalRef} color={SKY.sun} position={lightPosition} castShadow={false} />
      <hemisphereLight ref={hemisphereRef} args={[SKY.zenith, GROUND.grassLow, 0.5]} />
      <ambientLight ref={ambientRef} color="#ffffff" intensity={0.2} />

      <mesh rotation={[0, SKY_YAW, 0]} renderOrder={-20}>
        <sphereGeometry args={[DOME_RADIUS, DOME_WIDTH_SEGMENTS, DOME_HEIGHT_SEGMENTS]} />
        <primitive object={domeMaterial} attach="material" />
      </mesh>
    </group>
  );
}
