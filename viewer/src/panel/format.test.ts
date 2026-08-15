import { describe, expect, it } from "vitest";

import { backLabel, formatCount, formatExtent, formatMicrons, humanizeKey } from "./format";

describe("formatCount", () => {
  const cases: [number, string][] = [
    [587_115, "587,115"],
    [0, "0"],
    [1, "1"],
    [999, "999"],
    [1000, "1,000"],
    [-5, "-"],
    [NaN, "-"],
    [Infinity, "-"],
    [-Infinity, "-"],
  ];
  it.each(cases)("formatCount(%p) -> %p", (input, expected) => {
    expect(formatCount(input)).toBe(expected);
  });
});

describe("formatMicrons", () => {
  const cases: [number, string][] = [
    [27.2, "27.2 µm"],
    [0, "0.0 µm"],
    [999, "999.0 µm"],
    [999.99, "999.9 µm"],
    [1000, "1.00 mm"],
    [9103, "9.10 mm"],
    [-1, "-"],
    [NaN, "-"],
    [Infinity, "-"],
  ];
  it.each(cases)("formatMicrons(%p) -> %p", (input, expected) => {
    expect(formatMicrons(input)).toBe(expected);
  });
});

describe("humanizeKey", () => {
  const cases: [string, string][] = [
    ["human_development_stage", "Human development stage"],
    ["n_targets", "N targets"],
    ["sex", "Sex"],
    ["", ""],
  ];
  it.each(cases)("humanizeKey(%p) -> %p", (input, expected) => {
    expect(humanizeKey(input)).toBe(expected);
  });
});

describe("backLabel", () => {
  const names = { organ: "Colon", section: "V1_DLPFC_1", species: "🐀 Rat" };

  it("names the section it returns to at cell level", () => {
    expect(backLabel("cell", names)).toBe("Back to V1_DLPFC_1");
  });

  it("names the organ it returns to at section level", () => {
    expect(backLabel("section", names)).toBe("Back to Colon");
  });

  it("names the species it returns to at organ level", () => {
    expect(backLabel("organ", names)).toBe("Back to 🐀 Rat");
  });

  it("hides the control at orbit — nowhere further up to go", () => {
    expect(backLabel("orbit", names)).toBeNull();
  });

  it("falls back to a generic word when the specific name is not loaded yet", () => {
    expect(backLabel("cell", { ...names, section: null })).toBe("Back to the section");
    expect(backLabel("section", { ...names, organ: null })).toBe("Back to the organ");
  });
});

describe("formatExtent", () => {
  const cases: [[number, number, number, number], string][] = [
    [[0, 0, 9103, 6916], "9.10 × 6.91 mm"],
    [[0, 0, 0, 0], "0.00 × 0.00 mm"],
    [[100, 100, 1100, 2100], "1.00 × 2.00 mm"],
    [[0, 0, NaN, 6916], "-"],
    [[0, 0, Infinity, 6916], "-"],
    [[10, 0, 0, 6916], "-"],
  ];
  it.each(cases)("formatExtent(%p) -> %p", (input, expected) => {
    expect(formatExtent(input)).toBe(expected);
  });
});
