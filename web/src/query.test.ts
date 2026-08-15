import { describe, expect, it } from "vitest";

import { parseQuery } from "./query";
import type { CorpusIndex } from "./types";

const index = {
  facets: {
    modality: [{ value: "Transcriptomics", count: 13 }],
    platform: [
      { value: "Visium", count: 12 },
      { value: "Xenium", count: 1 },
    ],
    tissue: [
      { value: "colon", count: 1 },
      { value: "dorsolateral prefrontal cortex", count: 12 },
    ],
    disease: [
      { value: "Healthy", count: 12 },
      { value: "colon adenocarcinoma", count: 1 },
    ],
    resolution: [
      { value: "Spot", count: 12 },
      { value: "Subcellular", count: 1 },
    ],
  },
} as unknown as CorpusIndex;

describe("parseQuery", () => {
  it("returns null for empty input", () => {
    expect(parseQuery("   ", index)).toBeNull();
  });

  it("matches facet values verbatim", () => {
    const parsed = parseQuery("Xenium colon", index)!;
    expect(parsed.platforms).toEqual(["Xenium"]);
    expect(parsed.tissues).toEqual(["colon"]);
    expect(parsed.freeText).toBeNull();
  });

  it("resolves the abbreviations researchers actually type", () => {
    expect(parseQuery("DLPFC", index)!.tissues).toEqual(["dorsolateral prefrontal cortex"]);
    expect(parseQuery("CRC", index)!.diseases).toEqual(["colon adenocarcinoma"]);
  });

  it("reads a transcripts-per-cell floor in either phrasing", () => {
    expect(parseQuery("transcripts/cell > 200", index)!.minTxPerCell).toBe(200);
    expect(parseQuery("at least 150 transcripts", index)!.minTxPerCell).toBe(150);
  });

  it("picks up the image and QC intents", () => {
    const parsed = parseQuery("colon with images, passing QC", index)!;
    expect(parsed.requiresImages).toBe(true);
    expect(parsed.excludeFailingQC).toBe(true);
  });

  it("says so instead of inventing facets when nothing is recognised", () => {
    const parsed = parseQuery("zebrafish", index)!;
    expect(parsed.freeText).toBe("zebrafish");
    expect(parsed.interpretation).toBe('text match "zebrafish"');
  });

  it("always produces an interpretation to show", () => {
    expect(parseQuery("subcellular colon", index)!.interpretation).toContain("colon");
  });
});
