/**
 * Lays morphology crops out in the panel the way they lie in the tissue.
 *
 * The crops the atlas returns are scattered around a point, not a grid — one per cell in the
 * requested window — so stacking them in a row would throw away the only thing that makes them
 * spatial. This turns their micron footprints into percentages of their shared bounding box, which
 * a plain absolutely-positioned `<img>` can then place. Percentages rather than pixels because the
 * panel is resizable.
 *
 * Constraint 2: measured data is never decorated. The output carries positions and the raw PNG,
 * and no colour of any kind.
 */

import type { CropTile } from "../types";

export interface MosaicTile {
  uid: string;
  png: string;
  /** All four are percentages of the mosaic box, ready for CSS. */
  leftPct: number;
  topPct: number;
  widthPct: number;
  heightPct: number;
}

export interface Mosaic {
  tiles: MosaicTile[];
  /** Width / height of the covered tissue, for holding the box's aspect ratio. */
  aspect: number;
  widthUm: number;
  heightUm: number;
}

/**
 * Null when there is nothing to draw, or when the tiles somehow cover no area — a zero-extent
 * bounding box would divide by zero, and an empty box is not worth a heading.
 */
export function mosaicLayout(tiles: CropTile[]): Mosaic | null {
  if (tiles.length === 0) return null;

  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const tile of tiles) {
    xMin = Math.min(xMin, tile.x_um - tile.width_um / 2);
    xMax = Math.max(xMax, tile.x_um + tile.width_um / 2);
    yMin = Math.min(yMin, tile.y_um - tile.height_um / 2);
    yMax = Math.max(yMax, tile.y_um + tile.height_um / 2);
  }

  const widthUm = xMax - xMin;
  const heightUm = yMax - yMin;
  if (!(widthUm > 0) || !(heightUm > 0)) return null;

  return {
    widthUm,
    heightUm,
    aspect: widthUm / heightUm,
    tiles: tiles.map((tile) => ({
      uid: tile.uid,
      png: tile.png,
      leftPct: ((tile.x_um - tile.width_um / 2 - xMin) / widthUm) * 100,
      // Micron y runs up, CSS top runs down; flipping keeps this and the section plot agreeing.
      topPct: ((yMax - (tile.y_um + tile.height_um / 2)) / heightUm) * 100,
      widthPct: (tile.width_um / widthUm) * 100,
      heightPct: (tile.height_um / heightUm) * 100,
    })),
  };
}
