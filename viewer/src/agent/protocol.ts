/** Wire format for the agent control channel, and the sanitizer that guards the UI from it. */

import {
  BUDGET_RANGE,
  PIXEL_RANGE,
  SPECIES,
  type Lod,
  type Paint,
  type Species,
  type ViewerState,
} from "../types";

export interface ControlMessage {
  revision: number;
  patch: Partial<ViewerState>;
  note: string | null;
  actor: string | null;
}

const LOD_VALUES: readonly Lod[] = ["orbit", "organ", "section", "cell"];
const PAINT_VALUES: readonly Paint[] = ["counts", "gene"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isVec3(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every(isFiniteNumber);
}

function isVec2(value: unknown): value is [number, number] {
  return Array.isArray(value) && value.length === 2 && value.every(isFiniteNumber);
}

export function parseControlMessage(raw: string): ControlMessage | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (!isFiniteNumber(parsed.revision)) return null;
  const patch = isRecord(parsed.patch) ? (parsed.patch as Partial<ViewerState>) : {};
  const note = typeof parsed.note === "string" ? parsed.note : null;
  const actor = typeof parsed.actor === "string" ? parsed.actor : null;
  return { revision: parsed.revision, patch, note, actor };
}

export function sanitizePatch(patch: unknown): Partial<ViewerState> {
  if (!isRecord(patch)) return {};
  const out: Partial<ViewerState> = {};

  if (patch.species !== undefined) {
    if (SPECIES.includes(patch.species as Species)) out.species = patch.species as Species;
  }
  if (patch.lod !== undefined) {
    if (LOD_VALUES.includes(patch.lod as Lod)) out.lod = patch.lod as Lod;
  }
  if (patch.paint !== undefined) {
    if (PAINT_VALUES.includes(patch.paint as Paint)) out.paint = patch.paint as Paint;
  }
  if (patch.node !== undefined) {
    if (patch.node === null || typeof patch.node === "string") out.node = patch.node;
  }
  if (patch.sample !== undefined) {
    if (patch.sample === null || typeof patch.sample === "string") out.sample = patch.sample;
  }
  if (patch.gene !== undefined) {
    if (patch.gene === null || typeof patch.gene === "string") out.gene = patch.gene;
  }
  if (patch.sound !== undefined) {
    if (typeof patch.sound === "boolean") out.sound = patch.sound;
  }
  if (patch.budget !== undefined) {
    if (isFiniteNumber(patch.budget)) {
      out.budget = Math.min(BUDGET_RANGE[1], Math.max(BUDGET_RANGE[0], patch.budget));
    }
  }
  if (patch.pixel !== undefined) {
    if (isFiniteNumber(patch.pixel)) {
      out.pixel = Math.min(PIXEL_RANGE[1], Math.max(PIXEL_RANGE[0], patch.pixel));
    }
  }
  if (patch.camera !== undefined) {
    if (patch.camera === null) {
      out.camera = null;
    } else if (
      isRecord(patch.camera) &&
      isVec3(patch.camera.position) &&
      isVec3(patch.camera.target)
    ) {
      out.camera = { position: patch.camera.position, target: patch.camera.target };
    }
  }
  if (patch.focusUm !== undefined) {
    if (patch.focusUm === null) {
      out.focusUm = null;
    } else if (isVec2(patch.focusUm)) {
      out.focusUm = patch.focusUm;
    }
  }

  return out;
}
