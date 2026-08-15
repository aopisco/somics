import { describe, expect, it } from "vitest";

import {
  DEFAULT_STATE,
  PANEL_ANCHORS,
  PANEL_HEIGHT_RANGE,
  PANEL_WIDTH_RANGE,
  SPECIES,
} from "../types";
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
  panelOpen: false,
  panelGeom: { anchor: "bl", dx: 40, dy: 72, width: 420, height: 300 },
};

describe("encodeState / decodeState round trip", () => {
  it("round-trips a state with every field set to a non-default value", () => {
    expect(decodeState(encodeState(FULL_STATE))).toEqual(FULL_STATE);
  });

  it.each(SPECIES)("round-trips species %s", (species) => {
    const encoded = encodeState({ ...DEFAULT_STATE, species });
    expect(decodeState(encoded).species).toBe(species);
  });

  it("puts a non-default species in the hash under sp", () => {
    expect(encodeState({ ...DEFAULT_STATE, species: "zebrafish" })).toBe("#sp=zebrafish");
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

  it("decodes the zebrafish body from a hash", () => {
    const decoded = decodeState("#sp=zebrafish&lod=orbit");
    expect(decoded.species).toBe("zebrafish");
    expect(decoded.lod).toBe("orbit");
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

describe("floating panel geometry in the hash", () => {
  const geom = (over: Partial<ViewerState["panelGeom"]> = {}) => ({
    ...DEFAULT_STATE.panelGeom,
    ...over,
  });

  it("omits the panel keys when it is open at its default place", () => {
    expect(encodeState(DEFAULT_STATE)).toBe("#");
  });

  it("writes wo=0 only when the panel is closed", () => {
    expect(encodeState({ ...DEFAULT_STATE, panelOpen: false })).toBe("#wo=0");
    expect(encodeState({ ...DEFAULT_STATE, panelOpen: true })).toBe("#");
  });

  it("writes the geometry as anchor,dx,dy,width,height", () => {
    const state = { ...DEFAULT_STATE, panelGeom: geom({ anchor: "bl", dx: 12, dy: 34 }) };
    expect(encodeState(state)).toBe("#w=bl,12,34,380,560");
  });

  it("rounds sub-pixel drag noise out of the hash", () => {
    const state = { ...DEFAULT_STATE, panelGeom: geom({ dx: 12.4999, width: 380.5 }) };
    expect(encodeState(state)).toBe("#w=tr,12,24,381,560");
  });

  it.each(PANEL_ANCHORS)("round-trips the %s anchor", (anchor) => {
    const panelGeom = geom({ anchor, dx: 5, dy: 6 });
    expect(decodeState(encodeState({ ...DEFAULT_STATE, panelGeom })).panelGeom).toEqual(panelGeom);
  });

  it("defaults to open when wo is absent", () => {
    expect(decodeState("#n=colon").panelOpen).toBe(true);
  });

  it("treats any wo value other than 0 as open", () => {
    expect(decodeState("#wo=yes").panelOpen).toBe(true);
    expect(decodeState("#wo=1").panelOpen).toBe(true);
  });

  it("falls back to the default geometry when w is absent", () => {
    expect(decodeState("#n=colon").panelGeom).toEqual(DEFAULT_STATE.panelGeom);
  });

  it.each(["w=tr,1,2,3", "w=tr,1,2,300,400,500", "w=middle,1,2,300,400", "w=tr,x,2,300,400", "w="])(
    "falls back to the default geometry for malformed %s",
    (fragment) => {
      expect(decodeState(`#${fragment}`).panelGeom).toEqual(DEFAULT_STATE.panelGeom);
    },
  );

  it("keeps decoding neighbouring keys when the geometry is malformed", () => {
    const decoded = decodeState("#w=nonsense&n=colon&wo=0");
    expect(decoded.panelGeom).toEqual(DEFAULT_STATE.panelGeom);
    expect(decoded.node).toBe("colon");
    expect(decoded.panelOpen).toBe(false);
  });

  it("clamps a width and height outside the allowed range", () => {
    const wide = decodeState("#w=tr,0,0,99999,99999").panelGeom;
    expect(wide.width).toBe(PANEL_WIDTH_RANGE[1]);
    expect(wide.height).toBe(PANEL_HEIGHT_RANGE[1]);

    const tiny = decodeState("#w=tr,0,0,1,1").panelGeom;
    expect(tiny.width).toBe(PANEL_WIDTH_RANGE[0]);
    expect(tiny.height).toBe(PANEL_HEIGHT_RANGE[0]);
  });

  it("floors a negative offset at the frame edge instead of parking off-screen", () => {
    expect(decodeState("#w=tr,-500,-500,380,560").panelGeom).toMatchObject({ dx: 0, dy: 0 });
  });
});
