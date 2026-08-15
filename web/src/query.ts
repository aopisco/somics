/** Free text -> a typed filter, plus the sentence the SHOWING bar renders.
 *
 * Deliberately a keyword matcher over the corpus's own facet vocabulary rather
 * than a model call: it is deterministic, it runs offline, and anything it does
 * not recognise falls through to `freeText` instead of being invented. A model
 * call belongs on top of this, not instead of it — this stays the fallback.
 */

import type { CorpusIndex, FacetName } from "./types";

export interface ParsedQuery {
  platforms: string[];
  modalities: string[];
  tissues: string[];
  diseases: string[];
  resolutions: string[];
  minTxPerCell: number | null;
  requiresImages: boolean;
  excludeFailingQC: boolean;
  freeText: string | null;
  interpretation: string;
}

/** Spellings a researcher types that are not the atlas's own labels. */
const ALIASES: Record<string, string[]> = {
  dlpfc: ["dorsolateral prefrontal cortex"],
  "prefrontal cortex": ["dorsolateral prefrontal cortex"],
  brain: ["dorsolateral prefrontal cortex"],
  crc: ["colon adenocarcinoma"],
  colorectal: ["colon adenocarcinoma"],
  cancer: ["colon adenocarcinoma"],
  tumor: ["colon adenocarcinoma"],
  tumour: ["colon adenocarcinoma"],
  normal: ["Healthy"],
  "10x visium": ["Visium"],
  transcriptomic: ["Transcriptomics"],
  transcriptomics: ["Transcriptomics"],
  proteomic: ["Proteomics"],
  proteomics: ["Proteomics"],
  "single cell": ["Single-cell"],
  spots: ["Spot"],
  cells: ["Subcellular"],
};

const FACET_ORDER: FacetName[] = ["platform", "modality", "tissue", "disease", "resolution"];

const IMAGE_PATTERN = /\b(with images?|has images?|h&e|he stain|morphology|imagery|imaging)\b/i;
const QC_PATTERN = /\b(passing qc|passes qc|pass qc|good qc|high quality|no failing)\b/i;
const TX_PATTERN =
  /(?:transcripts?\s*(?:\/|per)\s*cell|tx\s*\/\s*cell|counts?\s*(?:\/|per)\s*cell)[^0-9]{0,12}(\d+)/i;
const TX_REVERSED = /(?:>|over|above|at least|minimum|min)\s*(\d+)\s*(?:transcripts?|counts?)/i;

function matchFacet(text: string, values: string[]): string[] {
  const hits = new Set<string>();
  for (const value of values) {
    if (text.includes(value.toLowerCase())) hits.add(value);
  }
  for (const [alias, targets] of Object.entries(ALIASES)) {
    if (!text.includes(alias)) continue;
    for (const target of targets) {
      if (values.includes(target)) hits.add(target);
    }
  }
  return [...hits];
}

export function parseQuery(raw: string, index: CorpusIndex): ParsedQuery | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const text = trimmed.toLowerCase();

  const matched: Record<FacetName, string[]> = {
    platform: [],
    modality: [],
    tissue: [],
    disease: [],
    resolution: [],
  };
  for (const facet of FACET_ORDER) {
    const values = (index.facets[facet] ?? []).map((entry) => entry.value);
    matched[facet] = matchFacet(text, values);
  }

  const txMatch = TX_PATTERN.exec(trimmed) ?? TX_REVERSED.exec(trimmed);
  const minTxPerCell = txMatch?.[1] ? Number(txMatch[1]) : null;
  const requiresImages = IMAGE_PATTERN.test(trimmed);
  const excludeFailingQC = QC_PATTERN.test(trimmed);

  const parts: string[] = [];
  for (const facet of FACET_ORDER) parts.push(...matched[facet]);
  if (minTxPerCell !== null) parts.push(`transcripts/cell > ${minTxPerCell}`);
  if (requiresImages) parts.push("has images");
  if (excludeFailingQC) parts.push("no failing QC");

  const recognised = parts.length > 0;
  return {
    platforms: matched.platform,
    modalities: matched.modality,
    tissues: matched.tissue,
    diseases: matched.disease,
    resolutions: matched.resolution,
    minTxPerCell,
    requiresImages,
    excludeFailingQC,
    // Nothing recognised means we say so rather than silently filtering on a
    // guess — the SHOWING bar is the product's trust surface.
    freeText: recognised ? null : trimmed,
    interpretation: recognised ? parts.join(", ") : `text match "${trimmed}"`,
  };
}
