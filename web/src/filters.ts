/** Filtering and facet counting over the precomputed index.
 *
 * The corpus is small enough that every count is a loop over an array — there
 * is no query layer behind this, by design.
 */

import type { ParsedQuery } from "./query";
import type { Dataset, FacetName, Filters, QcLevel, Toggles } from "./types";

export const FACET_LABELS: Record<FacetName, string> = {
  modality: "Modality",
  platform: "Platform",
  tissue: "Tissue / organ",
  disease: "Disease state",
  resolution: "Resolution tier",
};

/** Cards carry comma-joined values where a dataset spans several terms. */
export function facetValues(dataset: Dataset, facet: FacetName): string[] {
  switch (facet) {
    case "modality":
      return [dataset.modality];
    case "platform":
      return [dataset.platform];
    case "resolution":
      return [dataset.resolution];
    case "tissue":
    case "disease":
      return dataset[facet]
        .split(",")
        .map((value) => value.trim())
        .filter((value) => value && value !== "—");
  }
}

export function qcLevel(dataset: Dataset, key: string): QcLevel {
  return dataset.qc.find((metric) => metric.key === key)?.level ?? "na";
}

function matchesQuery(dataset: Dataset, query: ParsedQuery | null): boolean {
  if (!query) return true;

  const groups: [string[], FacetName][] = [
    [query.platforms, "platform"],
    [query.modalities, "modality"],
    [query.tissues, "tissue"],
    [query.diseases, "disease"],
    [query.resolutions, "resolution"],
  ];
  for (const [wanted, facet] of groups) {
    if (!wanted.length) continue;
    const present = facetValues(dataset, facet);
    if (!wanted.some((value) => present.includes(value))) return false;
  }

  if (query.requiresImages && !dataset.hasImages) return false;
  if (query.excludeFailingQC && !dataset.passesAllQc) return false;

  if (query.minTxPerCell !== null) {
    const metric = dataset.qc.find((entry) => entry.key === "tx_per_cell");
    // A dataset whose counts are not comparable (spot assays) cannot satisfy a
    // transcripts-per-cell floor, so it drops out rather than sneaking through.
    if (metric?.level === "na" || metric?.numeric === null || metric?.numeric === undefined) {
      return false;
    }
    if (metric.numeric < query.minTxPerCell) return false;
  }

  if (query.freeText) {
    const haystack = [
      dataset.title,
      dataset.study ?? "",
      dataset.platform,
      dataset.tissue,
      dataset.disease,
      dataset.meta.description ?? "",
      dataset.meta.panelName ?? "",
    ]
      .join(" ")
      .toLowerCase();
    if (!haystack.includes(query.freeText.toLowerCase())) return false;
  }

  return true;
}

function matchesToggles(dataset: Dataset, toggles: Toggles): boolean {
  if (toggles.passAllQc && !dataset.passesAllQc) return false;
  if (toggles.lowFalseDetection && qcLevel(dataset, "neg_probe") === "fail") return false;
  if (toggles.hasImages && !dataset.hasImages) return false;
  if (toggles.downloadable && !dataset.downloadable) return false;
  return true;
}

function matchesFilters(dataset: Dataset, filters: Filters, skip?: FacetName): boolean {
  for (const facet of Object.keys(filters) as FacetName[]) {
    if (facet === skip) continue;
    const selected = filters[facet];
    if (!selected.length) continue;
    const present = facetValues(dataset, facet);
    if (!selected.some((value) => present.includes(value))) return false;
  }
  return true;
}

export function applyFilters(
  datasets: Dataset[],
  query: ParsedQuery | null,
  filters: Filters,
  toggles: Toggles,
): Dataset[] {
  return datasets.filter(
    (dataset) =>
      matchesQuery(dataset, query) &&
      matchesFilters(dataset, filters) &&
      matchesToggles(dataset, toggles),
  );
}

/**
 * Counts for one facet group, computed with that group's own filter dropped —
 * so checking "Xenium" does not zero out the other platforms beside it.
 */
export function facetCounts(
  datasets: Dataset[],
  query: ParsedQuery | null,
  filters: Filters,
  toggles: Toggles,
  facet: FacetName,
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const dataset of datasets) {
    if (!matchesQuery(dataset, query)) continue;
    if (!matchesFilters(dataset, filters, facet)) continue;
    if (!matchesToggles(dataset, toggles)) continue;
    for (const value of facetValues(dataset, facet)) {
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }
  }
  return counts;
}

/** Does this dataset satisfy one toggle, on its own? */
export function satisfiesToggle(dataset: Dataset, key: keyof Toggles): boolean {
  switch (key) {
    case "passAllQc":
      return dataset.passesAllQc;
    case "lowFalseDetection":
      return qcLevel(dataset, "neg_probe") !== "fail";
    case "hasImages":
      return dataset.hasImages;
    case "downloadable":
      return dataset.downloadable;
  }
}

/** How many datasets would remain if this toggle were switched on. */
export function toggleCount(
  datasets: Dataset[],
  query: ParsedQuery | null,
  filters: Filters,
  toggles: Toggles,
  key: keyof Toggles,
): number {
  const others: Toggles = { ...toggles, [key]: false };
  return datasets.filter(
    (dataset) =>
      matchesQuery(dataset, query) &&
      matchesFilters(dataset, filters) &&
      matchesToggles(dataset, others) &&
      satisfiesToggle(dataset, key),
  ).length;
}

export function toggleValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((entry) => entry !== value) : [...list, value];
}

export function hasActiveFilters(filters: Filters, toggles: Toggles): boolean {
  return (
    Object.values(filters).some((values) => values.length > 0) ||
    Object.values(toggles).some(Boolean)
  );
}
