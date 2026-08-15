/**
 * The morphology imagery inside the floating panel.
 *
 * The 3D scene only shows crops at the cell level, and only where the camera is pointing. The
 * panel is where the imagery belongs for the *sample*, so it fetches its own window: around the
 * cell-level focus when there is one, and around the middle of the section otherwise. Samples
 * without morphology (every Visium section in the atlas today carries H&E instead) render nothing
 * — `modalityLine` in `Panel.tsx` is already where that is said in words.
 */

import type { JSX } from "react";
import { useEffect, useState } from "react";

import { fetchCrops } from "../api";
import type { LoadPhase } from "../state";
import type { CropTile, Sample } from "../types";
import { mosaicLayout } from "./mosaic";

/**
 * A tighter window than the scene's tile layer (150 µm): crops sit one per cell, so at 150 µm the
 * nearest sixteen sprawl over ~300 µm and the panel is mostly the gaps between them. At 40 µm the
 * same sixteen 27 µm tiles cover the box. The caption states the window either way, so nothing
 * about the scale is left implied.
 */
const CROP_RADIUS_UM = 40;
const CROP_LIMIT = 16;

export function Morphology({
  sample,
  focusUm,
}: {
  sample: Sample;
  focusUm: [number, number] | null;
}): JSX.Element | null {
  const [tiles, setTiles] = useState<CropTile[]>([]);
  const [phase, setPhase] = useState<LoadPhase>("idle");

  const hasCrops = sample.has_morphology_crop;
  const [xMin, yMin, xMax, yMax] = sample.extent_um;
  const xUm = focusUm ? focusUm[0] : (xMin + xMax) / 2;
  const yUm = focusUm ? focusUm[1] : (yMin + yMax) / 2;

  useEffect(() => {
    if (!hasCrops) {
      setTiles([]);
      setPhase("idle");
      return;
    }
    let cancelled = false;
    setPhase("loading");
    fetchCrops(sample.section_uid, xUm, yUm, CROP_RADIUS_UM, CROP_LIMIT)
      .then((loaded) => {
        if (cancelled) return;
        setTiles(loaded);
        setPhase("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setTiles([]);
        setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [sample.section_uid, hasCrops, xUm, yUm]);

  if (!hasCrops) return null;

  const mosaic = mosaicLayout(tiles);

  return (
    <div className="panel-morphology">
      <h2 className="panel-subtitle">Morphology</h2>
      {phase === "loading" && <p className="panel-loading">Pulling crops from the atlas.</p>}
      {phase === "error" && <p className="panel-muted">The atlas dropped that crop request.</p>}
      {phase === "ready" && !mosaic && (
        <p className="panel-muted">No crops within {CROP_RADIUS_UM} µm of this point.</p>
      )}
      {mosaic && (
        <>
          <div
            className="panel-mosaic"
            style={{ aspectRatio: `${mosaic.aspect}` }}
            role="img"
            aria-label={`${mosaic.tiles.length} morphology crops in their measured positions`}
          >
            {mosaic.tiles.map((tile) => (
              <img
                key={tile.uid}
                alt=""
                src={`data:image/png;base64,${tile.png}`}
                style={{
                  left: `${tile.leftPct}%`,
                  top: `${tile.topPct}%`,
                  width: `${tile.widthPct}%`,
                  height: `${tile.heightPct}%`,
                }}
              />
            ))}
          </div>
          <p className="panel-muted">
            {mosaic.tiles.length} crops over {Math.round(mosaic.widthUm)} ×{" "}
            {Math.round(mosaic.heightUm)} µm, centred on ({Math.round(xUm)}, {Math.round(yUm)}) µm
            {focusUm ? " — where the cell level is looking." : " — the middle of the section."}
          </p>
        </>
      )}
    </div>
  );
}
