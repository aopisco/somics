import { describe, expect, it } from "vitest";

import { DEFAULT_STATE } from "../types";
import type { ViewerState } from "../types";
import { decodeState, encodeState } from "./urlState";

const FULL_STATE: ViewerState = {
  species: "human",
  node: "colon",
  sample: "183c734af72b51e0",
  lod: "section",
  gene: "EPCAM",
  paint: "gene",
  camera: { position: [1.24, 3.51, 5.62], target: [0, 1.2, 0] },
  focusUm: [4679.2, 3384.6],
  budget: 123456,
  pixel: 0.75,
  sound: true,
};

describe("encodeState / decodeState round trip", () => {
  it("round-trips a state with every field set to a non-default value", () => {
    expect(decodeState(encodeState(FULL_STATE))).toEqual(FULL_STATE);
  });

  it("omits every key for DEFAULT_STATE", () => {
    expect(encodeState(DEFAULT_STATE)).toBe("#");
  });

  it("is stable: encoding the same state twice gives an identical string", () => {
    expect(encodeState(FULL_STATE)).toBe(encodeState(FULL_STATE));
  });

  it("rounds floats instead of dumping full precision", () => {
    const noisy: ViewerState = {
      ...DEFAULT_STATE,
      camera: { position: [1.241234567891, 3.5, 5.62], target: [0, 1.2000000003, 0] },
      focusUm: [4679.23456, 3384.61119],
      pixel: 0.40000000012,
    };
    const encoded = encodeState(noisy);
    expect(encoded).not.toMatch(/\.\d{4,}/);
  });
});

describe("decodeState tolerance", () => {
  it("returns DEFAULT_STATE for an empty string", () => {
    expect(decodeState("")).toEqual(DEFAULT_STATE);
  });

  it("returns DEFAULT_STATE for a bare '#'", () => {
    expect(decodeState("#")).toEqual(DEFAULT_STATE);
  });

  it("returns DEFAULT_STATE for garbage input", () => {
    expect(decodeState("garbage")).toEqual(DEFAULT_STATE);
  });

  it("decodes a full URL string", () => {
    const decoded = decodeState("https://example.com/app#sp=human&n=colon&lod=cell");
    expect(decoded.species).toBe("human");
    expect(decoded.node).toBe("colon");
    expect(decoded.lod).toBe("cell");
  });

  it("decodes a hash without the leading '#'", () => {
    const decoded = decodeState("sp=human&n=colon");
    expect(decoded.species).toBe("human");
    expect(decoded.node).toBe("colon");
  });

  it("ignores unknown keys without corrupting neighbours", () => {
    const decoded = decodeState("sp=human&zzz=123&n=colon");
    expect(decoded.species).toBe("human");
    expect(decoded.node).toBe("colon");
  });

  it("falls back to the default species while decoding other fields", () => {
    const decoded = decodeState("sp=axolotl&n=colon");
    expect(decoded.species).toBe(DEFAULT_STATE.species);
    expect(decoded.node).toBe("colon");
  });

  it("falls back to the default lod while decoding other fields", () => {
    const decoded = decodeState("lod=galaxy&g=EPCAM");
    expect(decoded.lod).toBe(DEFAULT_STATE.lod);
    expect(decoded.gene).toBe("EPCAM");
  });

  it("falls back to the default paint while decoding other fields", () => {
    const decoded = decodeState("p=rainbow&b=5000");
    expect(decoded.paint).toBe(DEFAULT_STATE.paint);
    expect(decoded.budget).toBe(5000);
  });

  it("clamps budget above the range", () => {
    expect(decodeState("b=999999999").budget).toBe(400_000);
  });

  it("clamps budget below the range", () => {
    expect(decodeState("b=1").budget).toBe(1_000);
  });

  it("falls back to default budget for non-numeric input", () => {
    expect(decodeState("b=abc").budget).toBe(DEFAULT_STATE.budget);
  });

  it("clamps pixel above the range", () => {
    expect(decodeState("px=5").pixel).toBe(1);
  });

  it("clamps pixel below the range", () => {
    expect(decodeState("px=0.001").pixel).toBe(0.15);
  });

  it("falls back to default pixel for non-numeric input", () => {
    expect(decodeState("px=abc").pixel).toBe(DEFAULT_STATE.pixel);
  });

  it("yields a null camera when cam has the wrong number of components", () => {
    expect(decodeState("cam=1,2&tgt=1,2,3").camera).toBeNull();
  });

  it("yields a null camera when tgt has the wrong number of components", () => {
    expect(decodeState("cam=1,2,3&tgt=1,2").camera).toBeNull();
  });
});
