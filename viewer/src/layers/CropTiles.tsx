/** Morphology image tiles at the cell level: real 128x128px crops fading in like satellite imagery. */

import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

import { fetchCrops } from "../api";
import { sectionTransform } from "../camera/lod";
import { selectCurrentSample, selectOrgan, useStore } from "../state";
import type { CropTile, Vec3 } from "../types";

const ORIGIN: Vec3 = [0, 0, 0];
const DEBOUNCE_MS = 250;
/** Tiles are 27.2um square; this radius gives a readable cluster around the click. */
const CROP_RADIUS_UM = 150;
const CROP_LIMIT = 24;
const TILE_Z_LIFT = 0.002;

interface LoadedTile {
  tile: CropTile;
  texture: THREE.Texture;
}

function decodeTileTexture(tile: CropTile): THREE.Texture {
  const image = new Image();
  const texture = new THREE.Texture(image);
  image.onload = () => {
    texture.needsUpdate = true;
  };
  image.src = `data:image/png;base64,${tile.png}`;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export function CropTilesLayer(props: { opacity: number }): JSX.Element | null {
  const sample = useStore(selectCurrentSample);
  const focusUm = useStore((s) => s.focusUm);
  const points = useStore((s) => s.points);
  const anchor = useStore((s) => selectOrgan(s, s.node)?.anchor ?? null);

  const [loaded, setLoaded] = useState<LoadedTile[]>([]);
  const requestIdRef = useRef(0);

  useEffect(() => {
    if (!sample || !sample.has_morphology_crop || !focusUm) {
      setLoaded([]);
      return;
    }
    const [xUm, yUm] = focusUm;
    const timer = window.setTimeout(() => {
      const requestId = ++requestIdRef.current;
      fetchCrops(sample.section_uid, xUm, yUm, CROP_RADIUS_UM, CROP_LIMIT)
        .then((tiles) => {
          if (requestIdRef.current !== requestId) return;
          setLoaded(tiles.map((tile) => ({ tile, texture: decodeTileTexture(tile) })));
        })
        .catch(() => {
          if (requestIdRef.current === requestId) setLoaded([]);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [sample, focusUm]);

  // Textures are GPU resources; drop the previous batch whenever a new one lands or we unmount.
  useEffect(() => {
    return () => {
      for (const { texture } of loaded) texture.dispose();
    };
  }, [loaded]);

  if (props.opacity <= 0.01 || !sample || !sample.has_morphology_crop || !focusUm || !points) {
    return null;
  }

  const transform = sectionTransform(anchor ?? ORIGIN);
  const [xMin, yMin, xMax, yMax] = points.meta.extent_um;
  const centreX = (xMin + xMax) / 2;
  const centreY = (yMin + yMax) / 2;
  const scaleUm = points.meta.scale_um;

  return (
    <group position={transform.position} scale={transform.scale}>
      {loaded.map(({ tile, texture }) => {
        const x = (tile.x_um - centreX) / scaleUm;
        const y = (tile.y_um - centreY) / scaleUm;
        const width = tile.width_um / scaleUm;
        const height = tile.height_um / scaleUm;
        return (
          <mesh key={tile.uid} position={[x, y, TILE_Z_LIFT]}>
            <planeGeometry args={[width, height]} />
            <meshBasicMaterial map={texture} transparent opacity={props.opacity} depthWrite={false} />
          </mesh>
        );
      })}
    </group>
  );
}
