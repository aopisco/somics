import { describe, expect, it } from "vitest";

import { applyFilters, facetCounts, toggleCount } from "./filters";
import { EMPTY_FILTERS, EMPTY_TOGGLES } from "./types";
import type { Dataset, QcMetric } from "./types";

function qc(level: QcMetric["level"], numeric: number | null = null): QcMetric[] {
  return [
    {
      key: "tx_per_cell",
      short: "tx/cell",
      label: "Median transcripts/cell",
      value: "x",
      numeric,
      threshold: "",
      level,
    },
    {
      key: "neg_probe",
      short: "neg-probe",
      label: "Negative-probe rate",
      value: "x",
      numeric: null,
      threshold: "",
      level: "na",
    },
  ];
}

function dataset(overrides: Partial<Dataset>): Dataset {
  return {
    id: "d",
    title: "d",
    study: null,
    platform: "Xenium",
    modality: "Transcriptomics",
    tissue: "colon",
    disease: "colon adenocarcinoma",
    resolution: "Subcellular",
    unitCount: 10,
    unitNoun: "cells",
    sectionCount: 1,
    datasetCount: 1,
    hasImages: true,
    imageKinds: ["morphology"],
    downloadable: true,
    qc: qc("pass", 300),
    passesAllQc: true,
    meta: {
      organism: "Homo sapiens",
      donorIds: [],
      panelName: null,
      panelSize: null,
      panelVersion: null,
      referenceGenome: null,
      license: null,
      released: null,
      publicationTitle: null,
      publicationDoi: null,
      accession: null,
      accessionDatabase: null,
      assays: [],
      featureSpaces: [],
      description: null,
    },
    location: null,
    downloadUrl: null,
    sections: [],
    ...overrides,
  };
}

const xenium = dataset({ id: "xenium" });
const visium = dataset({
  id: "visium",
  platform: "Visium",
  tissue: "dorsolateral prefrontal cortex",
  disease: "Healthy",
  resolution: "Spot",
  unitNoun: "spots",
  // Spot counts are not comparable to per-cell transcripts, so the metric is na.
  qc: qc("na", 2400),
  passesAllQc: true,
});
const all = [xenium, visium];

const query = {
  platforms: [],
  modalities: [],
  tissues: [],
  diseases: [],
  resolutions: [],
  minTxPerCell: null,
  requiresImages: false,
  excludeFailingQC: false,
  freeText: null,
  interpretation: "",
};

describe("applyFilters", () => {
  it("ANDs the query against the sidebar", () => {
    const filtered = applyFilters(
      all,
      { ...query, platforms: ["Xenium"] },
      { ...EMPTY_FILTERS, tissue: ["colon"] },
      EMPTY_TOGGLES,
    );
    expect(filtered.map((d) => d.id)).toEqual(["xenium"]);
  });

  it("ORs within one sidebar group", () => {
    const filtered = applyFilters(
      all,
      null,
      { ...EMPTY_FILTERS, platform: ["Xenium", "Visium"] },
      EMPTY_TOGGLES,
    );
    expect(filtered).toHaveLength(2);
  });

  it("drops datasets whose counts cannot satisfy a per-cell floor", () => {
    // The Visium spot median is 2400, but it is not a per-cell number — it must
    // not sneak past a transcripts/cell threshold.
    const filtered = applyFilters(all, { ...query, minTxPerCell: 200 }, EMPTY_FILTERS, EMPTY_TOGGLES);
    expect(filtered.map((d) => d.id)).toEqual(["xenium"]);
  });

  it("honours toggles", () => {
    const failing = dataset({ id: "bad", passesAllQc: false });
    const filtered = applyFilters([...all, failing], null, EMPTY_FILTERS, {
      ...EMPTY_TOGGLES,
      passAllQc: true,
    });
    expect(filtered.map((d) => d.id)).toEqual(["xenium", "visium"]);
  });
});

describe("facetCounts", () => {
  it("drops the group's own filter so its other values stay selectable", () => {
    const counts = facetCounts(
      all,
      null,
      { ...EMPTY_FILTERS, platform: ["Xenium"] },
      EMPTY_TOGGLES,
      "platform",
    );
    expect(counts.get("Visium")).toBe(1);
    expect(counts.get("Xenium")).toBe(1);
  });

  it("still applies the other groups", () => {
    const counts = facetCounts(
      all,
      null,
      { ...EMPTY_FILTERS, tissue: ["colon"] },
      EMPTY_TOGGLES,
      "platform",
    );
    expect(counts.get("Visium")).toBeUndefined();
  });
});

describe("toggleCount", () => {
  it("counts what would survive switching the toggle on", () => {
    const failing = dataset({ id: "bad", passesAllQc: false });
    expect(toggleCount([...all, failing], null, EMPTY_FILTERS, EMPTY_TOGGLES, "passAllQc")).toBe(2);
  });
});
