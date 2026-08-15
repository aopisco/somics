/** The shape of data/corpus_index.json, as written by scripts/build_corpus_index.py. */

export type QcLevel = "pass" | "warn" | "fail" | "na";

export interface QcMetric {
  key: string;
  /** Chip label, e.g. "tx/cell". */
  short: string;
  /** Full label for the drawer, e.g. "Median transcripts/cell". */
  label: string;
  /** Display value, rendered in mono. */
  value: string;
  numeric: number | null;
  threshold: string;
  level: QcLevel;
}

export interface Section {
  datasetUid: string;
  sampleName: string | null;
  unitCount: number;
  unitNoun: string;
  /** Section ids the 3D viewer addresses this data by. */
  sectionUids: string[];
  medianCounts: number | null;
  downloadUrl: string | null;
}

export interface DatasetMeta {
  organism: string;
  donorIds: string[];
  panelName: string | null;
  panelSize: number | null;
  panelVersion: string | null;
  referenceGenome: string | null;
  license: string | null;
  released: string | null;
  publicationTitle: string | null;
  publicationDoi: string | null;
  accession: string | null;
  accessionDatabase: string | null;
  assays: string[];
  featureSpaces: string[];
  description: string | null;
}

export interface Dataset {
  id: string;
  title: string;
  study: string | null;
  platform: string;
  modality: string;
  tissue: string;
  disease: string;
  resolution: string;
  unitCount: number;
  /** "cells" | "spots" | "bins" | "beads" — what one row of this dataset is. */
  unitNoun: string;
  sectionCount: number;
  datasetCount: number;
  hasImages: boolean;
  imageKinds: string[];
  downloadable: boolean;
  qc: QcMetric[];
  passesAllQc: boolean;
  meta: DatasetMeta;
  location: string | null;
  downloadUrl: string | null;
  sections: Section[];
}

export interface CorpusStats {
  datasets: number;
  ingestedDatasets: number;
  units: number;
  sections: number;
  platforms: number;
  passAllQc: number;
}

export interface FacetValue {
  value: string;
  count: number;
}

export type FacetName = "modality" | "platform" | "tissue" | "disease" | "resolution";

export interface CorpusIndex {
  generatedAt: string;
  atlasDir: string;
  stats: CorpusStats;
  facets: Record<FacetName, FacetValue[]>;
  starterPrompts: string[];
  datasets: Dataset[];
}

/** Sidebar state: one selected-value list per facet group. */
export type Filters = Record<FacetName, string[]>;

export interface Toggles {
  passAllQc: boolean;
  lowFalseDetection: boolean;
  hasImages: boolean;
  downloadable: boolean;
}

export const EMPTY_FILTERS: Filters = {
  modality: [],
  platform: [],
  tissue: [],
  disease: [],
  resolution: [],
};

export const EMPTY_TOGGLES: Toggles = {
  passAllQc: false,
  lowFalseDetection: false,
  hasImages: false,
  downloadable: false,
};
