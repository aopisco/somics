import { describe, expect, it } from "vitest";

import {
  buildColors,
  DOT_RADIUS_RANGE,
  dotRadiusPx,
  fitSection,
  normToPixel,
  pixelToUm,
  stampOffsets,
  umToPixel,
} from "./section";
import { magma, viridis } from "./colormap";
import type { GeneValues, PointCloud, PointMeta } from "../types";

/**
 * The server normalizes to +-1 on the *longer* axis, so `scale_um` is always half the longer
 * extent. Building metas any other way would test a frame the API never sends.
 */
function meta(xMin: number, yMin: number, xMax: number, yMax: number, nPoints = 100): PointMeta {
  return {
    n_points: nPoints,
    n_cells: nPoints,
    extent_um: [xMin, yMin, xMax, yMax],
    scale_um: Math.max(xMax - xMin, yMax - yMin) / 2,
    count_range: [0, 100],
  };
}

/** A LIBD Visium brain section: ~4,000 spots over a roughly square 6.5 mm of tissue. */
const VISIUM = meta(0, 0, 6500, 6500, 4000);
/** A Xenium colon: 587,115 cells over a long, thin section. */
const XENIUM = meta(0, 0, 12000, 5000, 587_115);

describe("fitSection", () => {
  it("fills the canvas for a square section in a square box", () => {
    const fit = fitSection(VISIUM, 300, 300);
    expect(fit.drawWidth).toBeCloseTo(300);
    expect(fit.drawHeight).toBeCloseTo(300);
    expect(fit.originX).toBe(150);
    expect(fit.originY).toBe(150);
  });

  it("preserves aspect and centres the slack", () => {
    const fit = fitSection(XENIUM, 400, 400);
    expect(fit.drawWidth).toBeCloseTo(400);
    expect(fit.drawHeight).toBeCloseTo(400 * (5000 / 12000));
    // Centred: the same margin above the tissue as below it.
    const [, topPx] = normToPixel(fit, 0, 5000 / 12000);
    const [, bottomPx] = normToPixel(fit, 0, -5000 / 12000);
    expect(topPx).toBeCloseTo(400 - bottomPx);
  });

  it("honours padding on the constraining axis", () => {
    const fit = fitSection(VISIUM, 300, 300, 10);
    expect(fit.drawWidth).toBeCloseTo(280);
    expect(fit.drawHeight).toBeCloseTo(280);
  });

  it("reports scale 0 — not NaN — for anything undrawable", () => {
    for (const fit of [
      fitSection(VISIUM, 0, 0),
      fitSection(VISIUM, 300, 300, 200),
      fitSection(meta(10, 10, 10, 400), 300, 300),
      fitSection(meta(0, 0, 500, 0), 300, 300),
    ]) {
      expect(fit.scale).toBe(0);
      for (const value of [fit.drawWidth, fit.drawHeight, fit.originX, fit.originY]) {
        expect(Number.isFinite(value)).toBe(true);
      }
    }
  });

  it("survives a scale_um the server should never send", () => {
    const broken: PointMeta = { ...VISIUM, scale_um: 0 };
    const fit = fitSection(broken, 300, 300);
    expect(Number.isFinite(fit.scale)).toBe(true);
    expect(Number.isFinite(pixelToUm(fit, 10, 10)[0])).toBe(true);
  });
});

describe("the pixel/micron round trip", () => {
  const fit = fitSection(XENIUM, 400, 300, 6);

  it("puts the middle of the canvas at the middle of the section", () => {
    expect(pixelToUm(fit, 200, 150)).toEqual([6000, 2500]);
    expect(umToPixel(fit, 6000, 2500)).toEqual([200, 150]);
  });

  it("flips y: further down the canvas is a smaller micron y", () => {
    expect(pixelToUm(fit, 200, 160)[1]).toBeLessThan(pixelToUm(fit, 200, 140)[1]);
    expect(umToPixel(fit, 6000, 4000)[1]).toBeLessThan(umToPixel(fit, 6000, 1000)[1]);
  });

  it("round-trips both ways", () => {
    for (const [px, py] of [
      [0, 0],
      [7, 293],
      [200, 150],
      [399, 1],
      [123.5, 87.25],
    ]) {
      const [xUm, yUm] = pixelToUm(fit, px, py);
      const [backX, backY] = umToPixel(fit, xUm, yUm);
      expect(backX).toBeCloseTo(px, 9);
      expect(backY).toBeCloseTo(py, 9);
    }
  });

  it("lands the tissue corners on the padded edges of the canvas", () => {
    // The long axis is x here, so x is what the padding constrains.
    const [leftPx] = umToPixel(fit, 0, 2500);
    const [rightPx] = umToPixel(fit, 12000, 2500);
    expect(leftPx).toBeCloseTo(6);
    expect(rightPx).toBeCloseTo(394);
  });

  it("answers with the middle of the section when there is nothing drawn", () => {
    const empty = fitSection(XENIUM, 0, 0);
    expect(pixelToUm(empty, 40, 40)).toEqual([6000, 2500]);
  });
});

describe("dotRadiusPx", () => {
  // The whole point of deriving the size: one constant cannot serve both of these.
  it("draws Visium spots big enough to see and Xenium cells small enough to resolve", () => {
    const visium = dotRadiusPx(4_000, 330, 330);
    const xenium = dotRadiusPx(587_115, 330, 137);
    expect(visium).toBeGreaterThan(2);
    expect(visium).toBeLessThan(4);
    expect(xenium).toBe(DOT_RADIUS_RANGE[0]);
  });

  it("shrinks as the section gets busier and grows as the panel gets bigger", () => {
    expect(dotRadiusPx(4_000, 330, 330)).toBeGreaterThan(dotRadiusPx(80_000, 330, 330));
    expect(dotRadiusPx(4_000, 900, 900)).toBeGreaterThan(dotRadiusPx(4_000, 330, 330));
  });

  it("stays inside its range for every degenerate input", () => {
    for (const r of [
      dotRadiusPx(0, 330, 330),
      dotRadiusPx(-1, 330, 330),
      dotRadiusPx(4_000, 0, 0),
      dotRadiusPx(1, 4000, 4000),
    ]) {
      expect(r).toBeGreaterThanOrEqual(DOT_RADIUS_RANGE[0]);
      expect(r).toBeLessThanOrEqual(DOT_RADIUS_RANGE[1]);
    }
  });
});

describe("stampOffsets", () => {
  it("is a single pixel below one pixel of radius", () => {
    for (const r of [0, 0.5, 0.99]) expect(stampOffsets(r)).toEqual([0, 0]);
  });

  it("is a disc, centred, with no duplicates", () => {
    const flat = stampOffsets(3);
    expect(flat.length % 2).toBe(0);
    const pairs = new Set<string>();
    let sumX = 0;
    let sumY = 0;
    for (let i = 0; i < flat.length; i += 2) {
      pairs.add(`${flat[i]},${flat[i + 1]}`);
      sumX += flat[i];
      sumY += flat[i + 1];
      expect(Math.hypot(flat[i], flat[i + 1])).toBeLessThanOrEqual(3.5);
    }
    expect(pairs.size).toBe(flat.length / 2);
    expect(sumX).toBe(0);
    expect(sumY).toBe(0);
    expect(pairs.has("0,0")).toBe(true);
  });

  it("grows with the radius", () => {
    const sizes = [1, 2, 3, 4].map((r) => stampOffsets(r).length);
    for (let i = 1; i < sizes.length; i++) expect(sizes[i]).toBeGreaterThan(sizes[i - 1]);
  });
});

function cloud(counts: number[], countRange: [number, number]): PointCloud {
  const n = counts.length;
  return {
    x: new Float32Array(n),
    y: new Float32Array(n),
    counts: new Float32Array(counts),
    meta: { ...meta(0, 0, 100, 100, n), count_range: countRange },
  };
}

function genes(values: number[], range: [number, number]): GeneValues {
  return {
    values: new Float32Array(values),
    meta: { gene: "GFAP", n_points: values.length, value_range: range, max_observed: range[1] },
  };
}

function rgbAt(colors: Uint8Array, i: number): [number, number, number] {
  return [colors[i * 3], colors[i * 3 + 1], colors[i * 3 + 2]];
}

function expected(map: (t: number) => [number, number, number], t: number): number[] {
  // Same 256-stop quantisation the module uses, so this pins the colour rather than re-deriving it.
  const k = Math.round(t * 255) / 255;
  return map(k).map((channel) => Math.round(channel * 255));
}

describe("buildColors", () => {
  const points = cloud([0, 5, 10], [0, 10]);

  it("paints transcript counts with viridis", () => {
    const colors = buildColors(points, "counts", null);
    expect(colors.ramp).toBe("viridis");
    expect(colors.range).toEqual([0, 10]);
    expect(rgbAt(colors.rgb, 0)).toEqual(expected(viridis, 0));
    expect(rgbAt(colors.rgb, 1)).toEqual(expected(viridis, 0.5));
    expect(rgbAt(colors.rgb, 2)).toEqual(expected(viridis, 1));
  });

  it("paints a selected gene with magma, on the gene's own range", () => {
    const colors = buildColors(points, "gene", genes([2, 2, 4], [2, 4]));
    expect(colors.ramp).toBe("magma");
    expect(colors.range).toEqual([2, 4]);
    expect(rgbAt(colors.rgb, 0)).toEqual(expected(magma, 0));
    expect(rgbAt(colors.rgb, 2)).toEqual(expected(magma, 1));
  });

  // Positions and gene values are two fetches; mid-swap the store can hold one section's positions
  // and another's values. Painting those against each other would look entirely plausible.
  it("falls back to counts when the gene values do not match the positions", () => {
    expect(buildColors(points, "gene", genes([1, 2], [1, 2])).ramp).toBe("viridis");
    expect(buildColors(points, "gene", null).ramp).toBe("viridis");
  });

  it("clamps values outside the range instead of running off the table", () => {
    const wild = cloud([-50, 500], [0, 10]);
    const colors = buildColors(wild, "counts", null);
    expect(rgbAt(colors.rgb, 0)).toEqual(expected(viridis, 0));
    expect(rgbAt(colors.rgb, 1)).toEqual(expected(viridis, 1));
  });

  it("emits three bytes per point and nothing else", () => {
    expect(buildColors(points, "counts", null).rgb.length).toBe(9);
    expect(buildColors(cloud([], [0, 1]), "counts", null).rgb.length).toBe(0);
  });
});
