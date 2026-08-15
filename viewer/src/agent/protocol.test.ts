import { describe, expect, it } from "vitest";

import { BUDGET_RANGE, PIXEL_RANGE } from "../types";
import { parseControlMessage, sanitizePatch } from "./protocol";

describe("parseControlMessage", () => {
  it("parses a valid message", () => {
    const raw = JSON.stringify({
      revision: 3,
      patch: { species: "human" },
      note: "flying in",
      actor: "scout",
    });
    expect(parseControlMessage(raw)).toEqual({
      revision: 3,
      patch: { species: "human" },
      note: "flying in",
      actor: "scout",
    });
  });

  it("rejects invalid JSON", () => {
    expect(parseControlMessage("{not json")).toBeNull();
  });

  it("rejects a JSON array", () => {
    expect(parseControlMessage("[1,2,3]")).toBeNull();
  });

  it("rejects a JSON string", () => {
    expect(parseControlMessage('"hello"')).toBeNull();
  });

  it("rejects a missing revision", () => {
    expect(parseControlMessage(JSON.stringify({ patch: {} }))).toBeNull();
  });

  it("rejects a non-numeric revision", () => {
    expect(parseControlMessage(JSON.stringify({ revision: "3", patch: {} }))).toBeNull();
  });

  it("defaults note and actor to null when absent", () => {
    expect(parseControlMessage(JSON.stringify({ revision: 1, patch: {} }))).toEqual({
      revision: 1,
      patch: {},
      note: null,
      actor: null,
    });
  });
});

describe("sanitizePatch", () => {
  it("drops unknown keys", () => {
    expect(sanitizePatch({ species: "rat", bogus: "nope" })).toEqual({ species: "rat" });
  });

  it("drops wrong-typed values while good neighbours survive", () => {
    expect(sanitizePatch({ species: 42, node: "colon" })).toEqual({ node: "colon" });
  });

  it.each(["human", "rat", "zebrafish"] as const)("accepts species %s", (value) => {
    expect(sanitizePatch({ species: value })).toEqual({ species: value });
  });
  it("rejects a bad species value", () => {
    expect(sanitizePatch({ species: "cat" })).toEqual({});
  });

  it.each(["orbit", "organ", "section", "cell"] as const)("accepts lod %s", (value) => {
    expect(sanitizePatch({ lod: value })).toEqual({ lod: value });
  });
  it("rejects a bad lod value", () => {
    expect(sanitizePatch({ lod: "moon" })).toEqual({});
  });

  it.each(["counts", "gene"] as const)("accepts paint %s", (value) => {
    expect(sanitizePatch({ paint: value })).toEqual({ paint: value });
  });
  it("rejects a bad paint value", () => {
    expect(sanitizePatch({ paint: "rainbow" })).toEqual({});
  });

  it("clamps budget above the range", () => {
    expect(sanitizePatch({ budget: BUDGET_RANGE[1] + 1_000_000 })).toEqual({
      budget: BUDGET_RANGE[1],
    });
  });
  it("clamps budget below the range", () => {
    expect(sanitizePatch({ budget: BUDGET_RANGE[0] - 1_000_000 })).toEqual({
      budget: BUDGET_RANGE[0],
    });
  });
  it("rejects a non-numeric budget", () => {
    expect(sanitizePatch({ budget: "lots" })).toEqual({});
  });

  it("clamps pixel above the range", () => {
    expect(sanitizePatch({ pixel: PIXEL_RANGE[1] + 5 })).toEqual({ pixel: PIXEL_RANGE[1] });
  });
  it("clamps pixel below the range", () => {
    expect(sanitizePatch({ pixel: PIXEL_RANGE[0] - 5 })).toEqual({ pixel: PIXEL_RANGE[0] });
  });
  it("rejects a non-numeric pixel", () => {
    expect(sanitizePatch({ pixel: "lots" })).toEqual({});
  });

  it("accepts a well-formed camera", () => {
    const camera = { position: [1, 2, 3], target: [0, 0, 0] };
    expect(sanitizePatch({ camera })).toEqual({ camera });
  });
  it("drops a camera with a 2-element position", () => {
    expect(sanitizePatch({ camera: { position: [1, 2], target: [0, 0, 0] } })).toEqual({});
  });
  it("drops a camera with a non-finite component", () => {
    expect(
      sanitizePatch({ camera: { position: [1, Infinity, 3], target: [0, 0, 0] } }),
    ).toEqual({});
  });
  it("drops a camera missing target", () => {
    expect(sanitizePatch({ camera: { position: [1, 2, 3] } })).toEqual({});
  });
  it("preserves an explicit null camera", () => {
    expect(sanitizePatch({ camera: null })).toEqual({ camera: null });
  });

  it("accepts a well-formed focusUm", () => {
    expect(sanitizePatch({ focusUm: [10, 20] })).toEqual({ focusUm: [10, 20] });
  });
  it("drops a focusUm with the wrong length", () => {
    expect(sanitizePatch({ focusUm: [10, 20, 30] })).toEqual({});
  });
  it("drops a focusUm with a non-finite component", () => {
    expect(sanitizePatch({ focusUm: [10, NaN] })).toEqual({});
  });
  it("preserves an explicit null focusUm", () => {
    expect(sanitizePatch({ focusUm: null })).toEqual({ focusUm: null });
  });

  it("preserves explicit nulls for the other nullable fields", () => {
    expect(sanitizePatch({ node: null, sample: null, gene: null })).toEqual({
      node: null,
      sample: null,
      gene: null,
    });
  });

  it("does not mutate the input object", () => {
    const input = { species: "human", camera: { position: [1, 2, 3], target: [0, 0, 0] } };
    const snapshot = JSON.stringify(input);
    sanitizePatch(input);
    expect(JSON.stringify(input)).toBe(snapshot);
  });

  it("returns a fresh object, not the input reference", () => {
    const input = { species: "human" as const };
    expect(sanitizePatch(input)).not.toBe(input);
  });
});
