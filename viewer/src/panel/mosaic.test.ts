import { describe, expect, it } from "vitest";

import type { CropTile } from "../types";
import { mosaicLayout } from "./mosaic";

function tile(uid: string, xUm: number, yUm: number, side = 27.2): CropTile {
  return { uid, x_um: xUm, y_um: yUm, width_um: side, height_um: side, png: `png-${uid}` };
}

describe("mosaicLayout", () => {
  it("is null with no tiles", () => {
    expect(mosaicLayout([])).toBeNull();
  });

  it("is null when the tiles cover no area", () => {
    expect(mosaicLayout([tile("a", 100, 100, 0)])).toBeNull();
  });

  it("fills the box with a single tile", () => {
    const mosaic = mosaicLayout([tile("a", 100, 100)]);
    expect(mosaic).not.toBeNull();
    expect(mosaic?.aspect).toBeCloseTo(1);
    expect(mosaic?.tiles[0]).toMatchObject({ uid: "a", png: "png-a", leftPct: 0, topPct: 0 });
    expect(mosaic?.tiles[0].widthPct).toBeCloseTo(100);
    expect(mosaic?.tiles[0].heightPct).toBeCloseTo(100);
  });

  it("reports the covered tissue in microns, tile footprints included", () => {
    const mosaic = mosaicLayout([tile("a", 0, 0, 10), tile("b", 90, 40, 10)]);
    expect(mosaic?.widthUm).toBeCloseTo(100);
    expect(mosaic?.heightUm).toBeCloseTo(50);
    expect(mosaic?.aspect).toBeCloseTo(2);
  });

  it("places the rightmost tile against the right edge", () => {
    const mosaic = mosaicLayout([tile("a", 0, 0, 10), tile("b", 90, 0, 10)]);
    const b = mosaic?.tiles.find((t) => t.uid === "b");
    expect(b).toBeDefined();
    expect((b?.leftPct ?? 0) + (b?.widthPct ?? 0)).toBeCloseTo(100);
  });

  it("flips micron y (up) into CSS top (down), so the high-y tile is on top", () => {
    const mosaic = mosaicLayout([tile("low", 0, 0, 10), tile("high", 0, 90, 10)]);
    const low = mosaic?.tiles.find((t) => t.uid === "low");
    const high = mosaic?.tiles.find((t) => t.uid === "high");
    expect(high?.topPct).toBeCloseTo(0);
    expect((low?.topPct ?? 0) + (low?.heightPct ?? 0)).toBeCloseTo(100);
  });

  it("keeps every tile inside the box for a realistic scattered window", () => {
    const tiles = [
      tile("a", 4679.2, 3384.6),
      tile("b", 4689.4, 3373.3),
      tile("c", 4527.6, 3401.9),
      tile("d", 4401.1, 3355.5),
    ];
    const mosaic = mosaicLayout(tiles);
    expect(mosaic?.tiles).toHaveLength(4);
    for (const t of mosaic?.tiles ?? []) {
      expect(t.leftPct).toBeGreaterThanOrEqual(0);
      expect(t.topPct).toBeGreaterThanOrEqual(0);
      expect(t.leftPct + t.widthPct).toBeLessThanOrEqual(100.0001);
      expect(t.topPct + t.heightPct).toBeLessThanOrEqual(100.0001);
    }
  });
});
