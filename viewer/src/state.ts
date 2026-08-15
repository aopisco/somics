/** The single store every part of the viewer reads. URL state is a projection of it. */

import { create } from "zustand";

import { fetchAnatomy, fetchGeneValues, fetchGenes, fetchPoints, fetchSamples } from "./api";
import {
  DEFAULT_STATE,
  type Anatomy,
  type CameraState,
  type GeneValues,
  type Lod,
  type OrganNode,
  type Paint,
  type PointCloud,
  type Sample,
  type Species,
  type ViewerState,
} from "./types";

export type LoadPhase = "idle" | "loading" | "ready" | "error";

interface Store extends ViewerState {
  anatomy: Anatomy | null;
  samples: Sample[];
  catalogPhase: LoadPhase;
  catalogError: string | null;

  points: PointCloud | null;
  pointsPhase: LoadPhase;

  geneList: string[];
  geneValues: GeneValues | null;
  genePhase: LoadPhase;

  /** Bumped on every explicit navigation so the camera rig re-runs its tween. */
  flyRequest: number;
  hoveredNode: string | null;

  loadCatalog: () => Promise<void>;
  hydrate: (state: ViewerState) => void;
  setSpecies: (species: Species) => void;
  setHoveredNode: (nodeId: string | null) => void;
  selectNode: (nodeId: string | null) => void;
  selectSample: (sectionUid: string | null) => void;
  setLod: (lod: Lod) => void;
  setCamera: (camera: CameraState) => void;
  setFocusUm: (focusUm: [number, number] | null) => void;
  setPaint: (paint: Paint) => void;
  setGene: (gene: string | null) => void;
  setBudget: (budget: number) => void;
  setPixel: (pixel: number) => void;
  setSound: (sound: boolean) => void;
  zoomOut: () => void;
}

/** Fields the URL owns, split out so hydrate can replace them wholesale. */
const VIEWER_KEYS = [
  "species",
  "node",
  "sample",
  "lod",
  "gene",
  "paint",
  "camera",
  "focusUm",
  "budget",
  "pixel",
  "sound",
] as const satisfies readonly (keyof ViewerState)[];

export const useStore = create<Store>((set, get) => ({
  ...DEFAULT_STATE,
  anatomy: null,
  samples: [],
  catalogPhase: "idle",
  catalogError: null,
  points: null,
  pointsPhase: "idle",
  geneList: [],
  geneValues: null,
  genePhase: "idle",
  flyRequest: 0,
  hoveredNode: null,

  loadCatalog: async () => {
    if (get().catalogPhase === "loading") return;
    set({ catalogPhase: "loading", catalogError: null });
    try {
      const [anatomy, samples] = await Promise.all([fetchAnatomy(), fetchSamples()]);
      set({ anatomy, samples, catalogPhase: "ready" });
      const { sample } = get();
      if (sample) void loadPoints(set, get, sample);
    } catch (error) {
      set({ catalogPhase: "error", catalogError: String(error) });
    }
  },

  hydrate: (state) => {
    const patch: Partial<Store> = {};
    for (const key of VIEWER_KEYS) patch[key] = state[key] as never;
    set({ ...patch, flyRequest: get().flyRequest + 1 });
  },

  setSpecies: (species) => set({ species }),
  setHoveredNode: (hoveredNode) => set({ hoveredNode }),

  selectNode: (node) =>
    set((s) => ({
      node,
      sample: null,
      points: null,
      pointsPhase: "idle",
      geneValues: null,
      lod: node ? "organ" : "orbit",
      flyRequest: s.flyRequest + 1,
    })),

  selectSample: (sectionUid) => {
    const sample = get().samples.find((row) => row.section_uid === sectionUid);
    set((s) => ({
      sample: sectionUid,
      node: sample?.node_id ?? s.node,
      species: sample?.species ?? s.species,
      lod: sectionUid ? "section" : s.lod,
      focusUm: null,
      points: null,
      pointsPhase: sectionUid ? "loading" : "idle",
      geneValues: null,
      genePhase: "idle",
      geneList: [],
      flyRequest: s.flyRequest + 1,
    }));
    if (sectionUid) {
      void loadPoints(set, get, sectionUid);
      void fetchGenes(sectionUid)
        .then((genes) => set({ geneList: genes }))
        .catch(() => set({ geneList: [] }));
    }
  },

  setLod: (lod) => set({ lod }),
  setCamera: (camera) => set({ camera }),
  setFocusUm: (focusUm) => set({ focusUm }),

  setPaint: (paint) => {
    set({ paint });
    const { sample, gene, geneValues, budget } = get();
    if (paint === "gene" && sample && gene && !geneValues) {
      void loadGene(set, sample, gene, budget);
    }
  },

  setGene: (gene) => {
    set({ gene, geneValues: null, genePhase: gene ? "loading" : "idle" });
    const { sample, budget } = get();
    if (gene && sample) {
      set({ paint: "gene" });
      void loadGene(set, sample, gene, budget);
    }
  },

  setBudget: (budget) => {
    set({ budget, geneValues: null });
    const { sample, gene, paint } = get();
    if (sample) {
      void loadPoints(set, get, sample);
      if (paint === "gene" && gene) void loadGene(set, sample, gene, budget);
    }
  },

  setPixel: (pixel) => set({ pixel }),
  setSound: (sound) => set({ sound }),

  zoomOut: () =>
    set((s) => {
      if (s.lod === "cell") return { lod: "section", flyRequest: s.flyRequest + 1 };
      if (s.lod === "section") {
        return {
          lod: "organ",
          sample: null,
          points: null,
          pointsPhase: "idle",
          geneValues: null,
          flyRequest: s.flyRequest + 1,
        };
      }
      return { lod: "orbit", node: null, flyRequest: s.flyRequest + 1 };
    }),
}));

type Set = (patch: Partial<Store> | ((s: Store) => Partial<Store>)) => void;
type Get = () => Store;

async function loadPoints(set: Set, get: Get, sectionUid: string) {
  set({ pointsPhase: "loading" });
  try {
    const points = await fetchPoints(sectionUid, get().budget);
    // A newer selection may have landed while this was in flight.
    if (get().sample !== sectionUid) return;
    set({ points, pointsPhase: "ready" });
  } catch {
    if (get().sample === sectionUid) set({ pointsPhase: "error" });
  }
}

async function loadGene(set: Set, sectionUid: string, gene: string, budget: number) {
  set({ genePhase: "loading" });
  try {
    set({ geneValues: await fetchGeneValues(sectionUid, gene, budget), genePhase: "ready" });
  } catch {
    set({ genePhase: "error" });
  }
}

/** The URL-owned slice of the store. */
export function viewerState(store: Store | ViewerState): ViewerState {
  const source = store as ViewerState;
  return {
    species: source.species,
    node: source.node,
    sample: source.sample,
    lod: source.lod,
    gene: source.gene,
    paint: source.paint,
    camera: source.camera,
    focusUm: source.focusUm,
    budget: source.budget,
    pixel: source.pixel,
    sound: source.sound,
  };
}

/** Shared so the "no anatomy yet" branch keeps a stable identity across calls. */
const NO_ORGANS: OrganNode[] = [];

export const selectOrgans = (store: Store): OrganNode[] =>
  store.anatomy?.bodies[store.species]?.organs ?? NO_ORGANS;

export const selectOrgan = (store: Store, nodeId: string | null): OrganNode | null =>
  nodeId ? (selectOrgans(store).find((organ) => organ.node_id === nodeId) ?? null) : null;

export const selectCurrentSample = (store: Store): Sample | null =>
  store.sample ? (store.samples.find((row) => row.section_uid === store.sample) ?? null) : null;

/**
 * Grouping is keyed on the `samples` array identity rather than recomputed per
 * call. zustand v5 reads a selector through `useSyncExternalStore`, which treats
 * a fresh object as a changed snapshot: rebuilding this record on every call made
 * every read report a change, which re-rendered, which read again — an infinite
 * loop that unmounted the whole tree ("Maximum update depth exceeded"). The store
 * replaces `samples` wholesale, so its identity is a sound cache key.
 */
const samplesByNodeCache = new WeakMap<Sample[], Record<string, Sample[]>>();

/** section_uids grouped by the organ they pin to, for badge counts on the body. */
export function selectSamplesByNode(store: Store): Record<string, Sample[]> {
  const cached = samplesByNodeCache.get(store.samples);
  if (cached) return cached;

  const grouped: Record<string, Sample[]> = {};
  for (const sample of store.samples) {
    if (!sample.node_id) continue;
    (grouped[sample.node_id] ??= []).push(sample);
  }
  samplesByNodeCache.set(store.samples, grouped);
  return grouped;
}
