import { Tag } from "@czi-sds/components";

import { QC_PALETTE, formatCount } from "../qc";
import type { Dataset } from "../types";

/** One attribute in the card's two-column grid. */
function Attribute({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="attribute-key">{label}</div>
      <div className={`attribute-value${mono ? " mono" : ""}`} title={value}>
        {value}
      </div>
    </div>
  );
}

export function DatasetCard({
  dataset,
  selected,
  onSelect,
}: {
  dataset: Dataset;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`card${selected ? " is-selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      {/* One segment per metric: dataset health before you read a word. */}
      <div className="qc-strip">
        {dataset.qc.map((metric) => (
          <span
            key={metric.key}
            className="qc-strip-segment"
            style={{ background: QC_PALETTE[metric.level].dot }}
          />
        ))}
      </div>

      <div className="card-body">
        <div className="card-head">
          <h3 className="card-title">{dataset.title}</h3>
          <span className="card-tag">
            <Tag
              label={dataset.platform}
              sdsStyle="rounded"
              sdsType="secondary"
              tagColor="info"
              sx={{
                maxWidth: "none",
                "& .MuiChip-label": { overflow: "visible", textOverflow: "clip" },
              }}
            />
          </span>
        </div>

        <div className="attributes">
          <Attribute label="Modality" value={dataset.modality} />
          <Attribute label="Tissue" value={dataset.tissue} />
          <Attribute label="Disease" value={dataset.disease} />
          <Attribute label="Resolution" value={dataset.resolution} />
          <Attribute
            label="Size"
            value={`${formatCount(dataset.unitCount)} ${dataset.unitNoun}`}
            mono
          />
          <Attribute
            label="Images"
            value={dataset.hasImages ? dataset.imageKinds.join(", ") : "none"}
          />
        </div>

        <div className="qc-chips">
          {dataset.qc.map((metric) => {
            const palette = QC_PALETTE[metric.level];
            return (
              <span
                key={metric.key}
                className="qc-chip"
                style={{ background: palette.bg, borderColor: palette.border, color: palette.text }}
                title={`${metric.label} — ${metric.threshold}`}
              >
                <span className="qc-dot" style={{ background: palette.dot }} />
                <span className="qc-chip-label">{metric.short}</span>
                <span className="qc-chip-value">{metric.value}</span>
              </span>
            );
          })}
        </div>
      </div>
    </button>
  );
}
