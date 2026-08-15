import { describe, expect, it } from "vitest";

import { PANEL_HEIGHT_RANGE, PANEL_WIDTH_RANGE } from "../types";
import type { PanelGeometry } from "../types";
import { clampRect, geometryFromRect, rectFromGeometry, sameGeometry } from "./geometry";
import type { Frame } from "./geometry";

const FRAME: Frame = { width: 1200, height: 800 };

describe("rectFromGeometry", () => {
  const size = { width: 300, height: 200 };

  it("measures a top-left panel from the top-left corner", () => {
    expect(rectFromGeometry({ anchor: "tl", dx: 20, dy: 30, ...size }, FRAME)).toEqual({
      left: 20,
      top: 30,
      ...size,
    });
  });

  it("measures a bottom-right panel from the bottom-right corner", () => {
    expect(rectFromGeometry({ anchor: "br", dx: 20, dy: 30, ...size }, FRAME)).toEqual({
      left: 1200 - 20 - 300,
      top: 800 - 30 - 200,
      ...size,
    });
  });

  it("keeps a right-anchored panel against the right edge when the frame narrows", () => {
    const geom: PanelGeometry = { anchor: "tr", dx: 24, dy: 24, ...size };
    const wide = rectFromGeometry(geom, FRAME);
    const narrow = rectFromGeometry(geom, { width: 900, height: 800 });
    expect(FRAME.width - (wide.left + wide.width)).toBe(24);
    expect(900 - (narrow.left + narrow.width)).toBe(24);
  });
});

describe("geometryFromRect", () => {
  it("anchors to the corner the panel ended up nearest", () => {
    // 40px from the right, 900px from the left; 30 from the bottom, 570 from the top.
    const rect = { left: 860, top: 570, width: 300, height: 200 };
    expect(geometryFromRect(rect, FRAME)).toEqual({
      anchor: "br",
      dx: 40,
      dy: 30,
      width: 300,
      height: 200,
    });
  });

  it("prefers the top-left corner on an exact tie, so a centred panel has one answer", () => {
    const rect = { left: 450, top: 300, width: 300, height: 200 };
    expect(geometryFromRect(rect, FRAME).anchor).toBe("tl");
  });

  it("rounds to whole pixels so a drag does not churn the hash", () => {
    const rect = { left: 20.4, top: 30.6, width: 300.5, height: 200.4 };
    expect(geometryFromRect(rect, FRAME)).toEqual({
      anchor: "tl",
      dx: 20,
      dy: 31,
      width: 301,
      height: 200,
    });
  });

  it("never records a negative offset for a panel hanging off the frame", () => {
    const rect = { left: -50, top: -80, width: 300, height: 200 };
    expect(geometryFromRect(rect, FRAME)).toMatchObject({ anchor: "tl", dx: 0, dy: 0 });
  });

  it("round-trips through rectFromGeometry for each corner", () => {
    for (const anchor of ["tl", "tr", "bl", "br"] as const) {
      const geom: PanelGeometry = { anchor, dx: 24, dy: 36, width: 320, height: 240 };
      expect(geometryFromRect(rectFromGeometry(geom, FRAME), FRAME)).toEqual(geom);
    }
  });
});

describe("clampRect", () => {
  it("leaves a rectangle that already fits alone", () => {
    const rect = { left: 20, top: 30, width: 300, height: 200 };
    expect(clampRect(rect, FRAME)).toEqual(rect);
  });

  it("pulls a panel dragged past the right and bottom edges back inside", () => {
    expect(clampRect({ left: 1500, top: 1000, width: 300, height: 200 }, FRAME)).toMatchObject({
      left: 900,
      top: 600,
    });
  });

  it("pulls a panel dragged past the top-left back inside", () => {
    expect(clampRect({ left: -80, top: -40, width: 300, height: 200 }, FRAME)).toMatchObject({
      left: 0,
      top: 0,
    });
  });

  it("shrinks a panel taller than the frame rather than letting it hang off", () => {
    const clamped = clampRect({ left: 0, top: 0, width: 300, height: 2000 }, FRAME);
    expect(clamped.height).toBe(800);
  });

  it("holds the minimum size when the frame is smaller than the minimum", () => {
    const clamped = clampRect({ left: 0, top: 0, width: 300, height: 200 }, { width: 100, height: 80 });
    expect(clamped.width).toBe(PANEL_WIDTH_RANGE[0]);
    expect(clamped.height).toBe(PANEL_HEIGHT_RANGE[0]);
    expect(clamped.left).toBe(0);
    expect(clamped.top).toBe(0);
  });

  it("refuses a resize below the minimum size", () => {
    const clamped = clampRect({ left: 10, top: 10, width: 10, height: 10 }, FRAME);
    expect(clamped.width).toBe(PANEL_WIDTH_RANGE[0]);
    expect(clamped.height).toBe(PANEL_HEIGHT_RANGE[0]);
  });

  it("refuses a resize above the maximum size", () => {
    const huge = clampRect({ left: 0, top: 0, width: 99999, height: 99999 }, { width: 5000, height: 5000 });
    expect(huge.width).toBe(PANEL_WIDTH_RANGE[1]);
    expect(huge.height).toBe(PANEL_HEIGHT_RANGE[1]);
  });

  it("survives a zero-sized frame, which is what the first render before measurement sees", () => {
    const clamped = clampRect({ left: 0, top: 0, width: 380, height: 560 }, { width: 0, height: 0 });
    expect(Number.isFinite(clamped.left)).toBe(true);
    expect(Number.isFinite(clamped.top)).toBe(true);
  });
});

describe("sameGeometry", () => {
  const base: PanelGeometry = { anchor: "tr", dx: 24, dy: 24, width: 380, height: 560 };

  it("is true for an equal-but-distinct object", () => {
    expect(sameGeometry(base, { ...base })).toBe(true);
  });

  it("is false when any single field differs", () => {
    expect(sameGeometry(base, { ...base, anchor: "tl" })).toBe(false);
    expect(sameGeometry(base, { ...base, dx: 25 })).toBe(false);
    expect(sameGeometry(base, { ...base, dy: 25 })).toBe(false);
    expect(sameGeometry(base, { ...base, width: 381 })).toBe(false);
    expect(sameGeometry(base, { ...base, height: 561 })).toBe(false);
  });
});
