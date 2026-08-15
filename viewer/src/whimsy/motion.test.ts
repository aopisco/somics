import { describe, expect, it } from "vitest";

import {
  breathe,
  damp,
  easeInOutCubic,
  hashNoise,
  springPop,
  sway,
  twitch,
  wobble,
} from "./motion";

describe("breathe", () => {
  it("stays within its depth of 1", () => {
    for (let t = 0; t < 20; t += 0.05) {
      expect(Math.abs(breathe(t) - 1)).toBeLessThanOrEqual(0.014 + 1e-9);
    }
  });
});

describe("wobble", () => {
  it("stays inside its amplitude", () => {
    for (let t = 0; t < 40; t += 0.05) expect(Math.abs(wobble(t, 0.05))).toBeLessThanOrEqual(0.05);
  });

  it("is not a pure sine, so the idle spin never looks mechanical", () => {
    expect(wobble(1)).not.toBeCloseTo(wobble(1 + TAU_OVER(0.37)), 3);
  });
});

const TAU_OVER = (rate: number) => (Math.PI * 2) / rate;

describe("twitch", () => {
  it("is zero for most of its period", () => {
    let firing = 0;
    const steps = 2000;
    for (let i = 0; i < steps; i++) if (twitch((i / steps) * 17) > 0) firing++;
    expect(firing / steps).toBeLessThan(0.1);
  });

  it("peaks at 1 in the middle of a flick", () => {
    expect(twitch(1.7 * 0.055 * 0.5)).toBeCloseTo(1, 5);
  });

  it("separates by phase so two ears do not flick together", () => {
    const t = 1.7 * 0.055 * 0.5;
    expect(twitch(t, 1.7, 0.055, 0.6)).toBe(0);
  });
});

describe("sway", () => {
  it("shifts with offset, so neighbouring blades lean in sequence", () => {
    expect(sway(3, 0)).not.toBeCloseTo(sway(3, 1.2), 3);
  });
});

describe("springPop", () => {
  it("is clamped at both ends", () => {
    expect(springPop(-1)).toBe(0);
    expect(springPop(0)).toBe(0);
    expect(springPop(1)).toBe(1);
    expect(springPop(4)).toBe(1);
  });

  it("overshoots past 1 before settling", () => {
    let peak = 0;
    for (let t = 0; t < 1; t += 0.005) peak = Math.max(peak, springPop(t));
    expect(peak).toBeGreaterThan(1);
  });
});

describe("easeInOutCubic", () => {
  it("pins its ends and its midpoint", () => {
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5, 6);
  });

  it("clamps out-of-range input", () => {
    expect(easeInOutCubic(-3)).toBe(0);
    expect(easeInOutCubic(3)).toBe(1);
  });
});

describe("damp", () => {
  it("approaches the target without overshooting", () => {
    let value = 0;
    for (let i = 0; i < 200; i++) value = damp(value, 10, 4, 1 / 60);
    expect(value).toBeGreaterThan(9.9);
    expect(value).toBeLessThanOrEqual(10);
  });

  it("moves the same distance per second at different frame rates", () => {
    let slow = 0;
    let fast = 0;
    for (let i = 0; i < 30; i++) slow = damp(slow, 1, 3, 1 / 30);
    for (let i = 0; i < 120; i++) fast = damp(fast, 1, 3, 1 / 120);
    expect(slow).toBeCloseTo(fast, 3);
  });
});

describe("hashNoise", () => {
  it("is stable across calls, so a shared link scatters grass identically", () => {
    expect(hashNoise(42)).toBe(hashNoise(42));
  });

  it("stays in [0, 1) and spreads out", () => {
    const values = Array.from({ length: 500 }, (_, i) => hashNoise(i));
    for (const value of values) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    expect(mean).toBeGreaterThan(0.4);
    expect(mean).toBeLessThan(0.6);
  });
});
