import { InputCheckbox } from "@czi-sds/components";

import {
  FACET_LABELS,
  facetCounts,
  hasActiveFilters,
  toggleCount,
  toggleValue,
} from "../filters";
import type { ParsedQuery } from "../query";
import type { CorpusIndex, Dataset, FacetName, Filters, Toggles } from "../types";

const FACET_ORDER: FacetName[] = ["modality", "platform", "tissue", "disease", "resolution"];

/** QC and data-availability filters, which are toggles rather than facets. */
interface ToggleSpec {
  key: keyof Toggles;
  label: string;
  group: string;
  /** Set when the atlas has no source for this filter — shown, but inert. */
  unavailable?: string;
}

const TOGGLES: ToggleSpec[] = [
  { key: "passAllQc", label: "Passes all QC", group: "Quality control" },
  { key: "lowFalseDetection", label: "Low false-detection rate", group: "Quality control" },
  { key: "hasImages", label: "Has images", group: "Data available" },
  { key: "downloadable", label: "Downloadable", group: "Data available" },
];

const UNAVAILABLE_QC = {
  label: "Passes segmentation QC",
  reason: "No segmentation verdict is captured at ingest",
};

function Row({
  label,
  checked,
  count,
  disabled,
  title,
  onToggle,
}: {
  label: string;
  checked: boolean;
  count?: number | string;
  disabled?: boolean;
  title?: string;
  onToggle: () => void;
}) {
  return (
    // The row is the hit target, not just the box — a 264px column of tiny
    // checkboxes is miserable to click otherwise.
    <div
      className={`facet-row${disabled ? " is-disabled" : ""}`}
      onClick={disabled ? undefined : onToggle}
      title={title}
    >
      <span className="facet-label">
        <InputCheckbox
          stage={checked ? "checked" : "unchecked"}
          label={label}
          disabled={disabled}
          onChange={disabled ? undefined : onToggle}
          onClick={(event) => event.stopPropagation()}
        />
      </span>
      <span className="facet-count mono">{count ?? "—"}</span>
    </div>
  );
}

export function Sidebar({
  index,
  query,
  filters,
  toggles,
  onFilters,
  onToggles,
  onReset,
}: {
  index: CorpusIndex;
  query: ParsedQuery | null;
  filters: Filters;
  toggles: Toggles;
  onFilters: (next: Filters) => void;
  onToggles: (next: Toggles) => void;
  onReset: () => void;
}) {
  const datasets: Dataset[] = index.datasets;
  const active = hasActiveFilters(filters, toggles);

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="sidebar-title">Filters</span>
        <button type="button" className="link-button" onClick={onReset} disabled={!active}>
          Reset
        </button>
      </div>

      {FACET_ORDER.map((facet) => {
        const counts = facetCounts(datasets, query, filters, toggles, facet);
        const values = index.facets[facet] ?? [];
        if (!values.length) return null;
        return (
          <div className="facet-group" key={facet}>
            <div className="facet-title">{FACET_LABELS[facet]}</div>
            {values.map(({ value }) => (
              <Row
                key={value}
                label={value}
                checked={filters[facet].includes(value)}
                count={counts.get(value) ?? 0}
                onToggle={() =>
                  onFilters({ ...filters, [facet]: toggleValue(filters[facet], value) })
                }
              />
            ))}
          </div>
        );
      })}

      {["Quality control", "Data available"].map((group) => (
        <div className="facet-group" key={group}>
          <div className="facet-title">{group}</div>
          {TOGGLES.filter((spec) => spec.group === group).map((spec) => (
            <Row
              key={spec.key}
              label={spec.label}
              checked={toggles[spec.key]}
              count={toggleCount(datasets, query, filters, toggles, spec.key)}
              onToggle={() => onToggles({ ...toggles, [spec.key]: !toggles[spec.key] })}
            />
          ))}
          {group === "Quality control" && (
            <Row
              label={UNAVAILABLE_QC.label}
              checked={false}
              disabled
              title={UNAVAILABLE_QC.reason}
              onToggle={() => undefined}
            />
          )}
        </div>
      ))}
    </aside>
  );
}
