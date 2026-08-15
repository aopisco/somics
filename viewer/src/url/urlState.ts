/**
 * Codec between ViewerState and the URL hash fragment, plus the hook that keeps
 * the two in sync. The hash is the shareable link: whatever a viewer changed
 * that differs from DEFAULT_STATE round-trips through it.
 */

import { useEffect, useRef } from "react";

import { sameGeometry } from "../panel/geometry";
import { useStore, viewerState } from "../state";
import {
  BUDGET_RANGE,
  DEFAULT_STATE,
  LODS,
  PANEL_ANCHORS,
  PANEL_HEIGHT_RANGE,
  PANEL_WIDTH_RANGE,
  PIXEL_RANGE,
  SPECIES,
} from "../types";
import type {
  CameraState,
  Lod,
  Paint,
  PanelAnchor,
  PanelGeometry,
  Species,
  Vec3,
  ViewerState,
} from "../types";

// Kept in sync with the `Paint` union in types.ts by hand — it has no exported value
// array (unlike `LODS` and `SPECIES`) to validate against.
const PAINT_VALUES: readonly Paint[] = ["counts", "gene"];

const WRITE_THROTTLE_MS = 250; // ~4 writes/sec

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function clamp(value: number, [lo, hi]: [number, number]): number {
  return Math.min(hi, Math.max(lo, value));
}

function parseVec3(raw: string | undefined): Vec3 | null {
  if (raw === undefined) return null;
  const parts = raw.split(",");
  if (parts.length !== 3) return null;
  const nums = parts.map(Number);
  if (nums.some((n) => !Number.isFinite(n))) return null;
  return [nums[0], nums[1], nums[2]];
}

function parseVec2(raw: string | undefined): [number, number] | null {
  if (raw === undefined) return null;
  const parts = raw.split(",");
  if (parts.length !== 2) return null;
  const nums = parts.map(Number);
  if (nums.some((n) => !Number.isFinite(n))) return null;
  return [nums[0], nums[1]];
}

/**
 * `anchor,dx,dy,width,height` -> the floating panel's place on the frame.
 *
 * Anything structurally wrong — wrong arity, unknown anchor, a non-number — falls back to the
 * default position wholesale rather than to a half-parsed box. Out-of-range sizes are clamped
 * instead, matching how `b` and `px` treat a number that is merely too big.
 */
function parsePanelGeometry(raw: string | undefined): PanelGeometry {
  if (raw === undefined) return DEFAULT_STATE.panelGeom;
  const parts = raw.split(",");
  if (parts.length !== 5) return DEFAULT_STATE.panelGeom;

  const [anchor, ...rest] = parts;
  if (!(PANEL_ANCHORS as readonly string[]).includes(anchor)) return DEFAULT_STATE.panelGeom;

  const nums = rest.map(Number);
  if (nums.some((n) => !Number.isFinite(n))) return DEFAULT_STATE.panelGeom;
  const [dx, dy, width, height] = nums;

  return {
    anchor: anchor as PanelAnchor,
    // A negative offset would park the panel off the frame edge it is anchored to.
    dx: Math.max(0, dx),
    dy: Math.max(0, dy),
    width: clamp(width, PANEL_WIDTH_RANGE),
    height: clamp(height, PANEL_HEIGHT_RANGE),
  };
}

/** Strips a leading URL/query down to its fragment; tolerates missing "#". */
function extractFragment(input: string): string {
  const idx = input.indexOf("#");
  return idx === -1 ? input : input.slice(idx + 1);
}

function parseParams(fragment: string): Map<string, string> {
  const params = new Map<string, string>();
  if (!fragment) return params;
  for (const pair of fragment.split("&")) {
    if (!pair) continue;
    const eq = pair.indexOf("=");
    if (eq === -1) continue;
    try {
      params.set(pair.slice(0, eq), decodeURIComponent(pair.slice(eq + 1)));
    } catch {
      // Malformed percent-encoding: treat this key as absent.
    }
  }
  return params;
}

export function encodeState(state: ViewerState): string {
  const parts: string[] = [];

  if (state.species !== DEFAULT_STATE.species) parts.push(`sp=${encodeURIComponent(state.species)}`);
  if (state.node !== null) parts.push(`n=${encodeURIComponent(state.node)}`);
  if (state.sample !== null) parts.push(`s=${encodeURIComponent(state.sample)}`);
  if (state.lod !== DEFAULT_STATE.lod) parts.push(`lod=${encodeURIComponent(state.lod)}`);
  if (state.gene !== null) parts.push(`g=${encodeURIComponent(state.gene)}`);
  if (state.paint !== DEFAULT_STATE.paint) parts.push(`p=${encodeURIComponent(state.paint)}`);
  if (state.budget !== DEFAULT_STATE.budget) parts.push(`b=${Math.round(state.budget)}`);
  if (state.pixel !== DEFAULT_STATE.pixel) parts.push(`px=${round(state.pixel, 3)}`);
  if (state.camera !== null) {
    parts.push(`cam=${state.camera.position.map((v) => round(v, 3)).join(",")}`);
    parts.push(`tgt=${state.camera.target.map((v) => round(v, 3)).join(",")}`);
  }
  if (state.focusUm !== null) parts.push(`f=${state.focusUm.map((v) => round(v, 1)).join(",")}`);
  if (state.sound !== DEFAULT_STATE.sound) parts.push(`snd=${state.sound ? "1" : "0"}`);
  if (state.panelOpen !== DEFAULT_STATE.panelOpen) parts.push(`wo=${state.panelOpen ? "1" : "0"}`);
  if (!sameGeometry(state.panelGeom, DEFAULT_STATE.panelGeom)) {
    const g = state.panelGeom;
    const box = [g.dx, g.dy, g.width, g.height].map((v) => Math.round(v)).join(",");
    parts.push(`w=${g.anchor},${box}`);
  }

  return `#${parts.join("&")}`;
}

export function decodeState(hash: string): ViewerState {
  const params = parseParams(extractFragment(hash));

  const sp = params.get("sp");
  const species = sp !== undefined && (SPECIES as readonly string[]).includes(sp) ? (sp as Species) : DEFAULT_STATE.species;

  const lod = params.get("lod");
  const resolvedLod = lod !== undefined && (LODS as readonly string[]).includes(lod) ? (lod as Lod) : DEFAULT_STATE.lod;

  const p = params.get("p");
  const paint = p !== undefined && (PAINT_VALUES as readonly string[]).includes(p) ? (p as Paint) : DEFAULT_STATE.paint;

  const budgetRaw = Number(params.get("b"));
  const budget = params.has("b") && Number.isFinite(budgetRaw) ? clamp(budgetRaw, BUDGET_RANGE) : DEFAULT_STATE.budget;

  const pixelRaw = Number(params.get("px"));
  const pixel = params.has("px") && Number.isFinite(pixelRaw) ? clamp(pixelRaw, PIXEL_RANGE) : DEFAULT_STATE.pixel;

  const camPosition = parseVec3(params.get("cam") ?? undefined);
  const camTarget = parseVec3(params.get("tgt") ?? undefined);
  const camera: CameraState | null = camPosition && camTarget ? { position: camPosition, target: camTarget } : null;

  const focusUm = parseVec2(params.get("f") ?? undefined);

  return {
    species,
    node: params.get("n") ?? null,
    sample: params.get("s") ?? null,
    lod: resolvedLod,
    gene: params.get("g") ?? null,
    paint,
    camera,
    focusUm,
    budget,
    pixel,
    sound: params.get("snd") === "1",
    // Open unless the hash says otherwise, so a bare link still shows the atlas.
    panelOpen: params.get("wo") !== "0",
    panelGeom: parsePanelGeometry(params.get("w")),
  };
}

/**
 * Keeps `window.location.hash` and the store's URL-owned fields in sync.
 *
 * This sits in a feedback loop: store change -> encode -> replaceState ->
 * hashchange -> decode -> hydrate -> store change. `replaceState` does not
 * itself fire `hashchange`, but we still track the last string we wrote and
 * ignore a `hashchange` that matches it, so this stays correct even if that
 * browser behavior is ever relied on differently (e.g. a future refactor
 * swaps in `pushState`-adjacent APIs).
 */
export function useUrlSync(): void {
  const lastWrittenRef = useRef<string | null>(null);

  useEffect(() => {
    const decoded = decodeState(window.location.hash);
    if (encodeState(decoded) !== "#") useStore.getState().hydrate(decoded);

    let pending: ReturnType<typeof setTimeout> | null = null;

    const writeUrl = () => {
      pending = null;
      const encoded = encodeState(viewerState(useStore.getState()));
      const current = window.location.hash === "" ? "#" : window.location.hash;
      if (encoded === current) return;
      lastWrittenRef.current = encoded;
      history.replaceState(null, "", encoded);
    };

    const unsubscribe = useStore.subscribe(() => {
      if (pending !== null) return;
      pending = setTimeout(writeUrl, WRITE_THROTTLE_MS);
    });

    const onHashChange = () => {
      const current = window.location.hash === "" ? "#" : window.location.hash;
      if (current === lastWrittenRef.current) return; // our own replaceState echo
      useStore.getState().hydrate(decodeState(current));
    };
    window.addEventListener("hashchange", onHashChange);

    return () => {
      unsubscribe();
      window.removeEventListener("hashchange", onHashChange);
      if (pending !== null) clearTimeout(pending);
    };
  }, []);
}
