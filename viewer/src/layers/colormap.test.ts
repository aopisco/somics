import { describe, expect, it } from "vitest";

import { magma, normalize, viridis } from "./colormap";

describe("viridis", () => {
  it("starts dark blue-purple: green is the smallest channel", () => {
    const [r, g, b] = viridis(0);
    expect(g).toBeLessThan(r);
    expect(g).toBeLessThan(b);
  });

  it("ends yellow-green: blue is the smallest channel", () => {
    const [r, g, b] = viridis(1);
    expect(b).toBeLessThan(r);
    expect(b).toBeLessThan(g);
  });

  it("stays in 0..1 across a sweep", () => {
    for (let i = 0; i <= 20; i++) {
      for (const c of viridis(i / 20)) {
        expect(c).toBeGreaterThanOrEqual(0);
        expect(c).toBeLessThanOrEqual(1);
      }
    }
  });

  it("clamps outside 0..1", () => {
    expect(viridis(-5)).toEqual(viridis(0));
    expect(viridis(5)).toEqual(viridis(1));
  });

  it("trends brighter across the sweep (channel sum non-decreasing)", () => {
    let previousSum = -Infinity;
    for (let i = 0; i <= 20; i++) {
      const sum = viridis(i / 20).reduce((a, b) => a + b, 0);
      expect(sum).toBeGreaterThanOrEqual(previousSum);
      previousSum = sum;
    }
  });
});

describe("magma", () => {
  it("stays in 0..1 across a sweep", () => {
    for (let i = 0; i <= 20; i++) {
      for (const c of magma(i / 20)) {
        expect(c).toBeGreaterThanOrEqual(0);
        expect(c).toBeLessThanOrEqual(1);
      }
    }
  });

  it("clamps outside 0..1", () => {
    expect(magma(-5)).toEqual(magma(0));
    expect(magma(5)).toEqual(magma(1));
  });
});

describe("normalize", () => {
  it("clamps below and above the range", () => {
    expect(normalize(-10, [0, 10])).toBe(0);
    expect(normalize(20, [0, 10])).toBe(1);
  });

  it("maps the midpoint to 0.5", () => {
    expect(normalize(5, [0, 10])).toBeCloseTo(0.5);
  });

  it("returns 0 for a degenerate range instead of NaN", () => {
    expect(normalize(5, [10, 10])).toBe(0);
    expect(normalize(5, [10, 0])).toBe(0);
    expect(Number.isNaN(normalize(5, [10, 10]))).toBe(false);
  });
});
