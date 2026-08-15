import { Button, Tag } from "@czi-sds/components";

import { QC_PALETTE, formatCount } from "../qc";
import type { Dataset } from "../types";

function MetaRow({ label, value, mono }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div className="meta-row">
      <span className="meta-key">{label}</span>
      <span className={`meta-value${mono && value ? " mono" : ""}`}>{value ?? "—"}</span>
    </div>
  );
}

/** The section a card is built from, so a multi-section dataset is inspectable. */
function Sections({ dataset }: { dataset: Dataset }) {
  if (dataset.sections.length < 2) return null;
  return (
    <div className="drawer-section">
      <div className="section-label">Sections ({dataset.sections.length})</div>
      <div className="section-list">
        {dataset.sections.map((section) => (
          <div className="section-list-row" key={section.datasetUid}>
            <span>{section.sampleName ?? section.datasetUid}</span>
            <span className="mono">
              {formatCount(section.unitCount)} {section.unitNoun}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function DetailDrawer({
  dataset,
  onClose,
  onOpenViewer,
}: {
  dataset: Dataset;
  onClose: () => void;
  onOpenViewer: (dataset: Dataset) => void;
}) {
  const { meta } = dataset;
  return (
    <aside className="drawer" aria-label={`${dataset.title} detail`}>
      <div className="drawer-head">
        <div className="drawer-head-row">
          <h2 className="drawer-title">{dataset.title}</h2>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="drawer-tags">
          <Tag label={dataset.platform} sdsStyle="rounded" sdsType="secondary" tagColor="info" />
          <Tag
            label={dataset.modality}
            sdsStyle="rounded"
            sdsType="secondary"
            tagColor="neutral"
          />
          <Tag
            label={dataset.resolution}
            sdsStyle="rounded"
            sdsType="secondary"
            tagColor="neutral"
          />
        </div>
      </div>

      <div className="drawer-section">
        <div className="section-label">Quality control</div>
        <div className="qc-table">
          {dataset.qc.map((metric) => {
            const palette = QC_PALETTE[metric.level];
            return (
              <div className="qc-row" key={metric.key}>
                <span>
                  <div className="qc-row-label">{metric.label}</div>
                  <div className="qc-row-threshold">{metric.threshold}</div>
                </span>
                <span className="qc-row-value">{metric.value}</span>
                <span
                  className="qc-pill"
                  style={{
                    background: palette.bg,
                    borderColor: palette.border,
                    color: palette.text,
                  }}
                >
                  <span className="qc-dot" style={{ background: palette.dot }} />
                  {palette.word}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="drawer-section">
        <div className="section-label">Metadata</div>
        <MetaRow label="Tissue" value={dataset.tissue} />
        <MetaRow label="Disease state" value={dataset.disease} />
        <MetaRow
          label={dataset.unitNoun.replace(/^./, (c) => c.toUpperCase())}
          value={formatCount(dataset.unitCount)}
          mono
        />
        <MetaRow label="Organism" value={meta.organism} />
        <MetaRow label="Donor ID" value={meta.donorIds.join(", ") || null} mono />
        <MetaRow label="Panel" value={meta.panelName} />
        <MetaRow label="Panel size" value={meta.panelSize ? `${meta.panelSize} targets` : null} />
        <MetaRow label="Panel version" value={meta.panelVersion} mono />
        <MetaRow label="Reference genome" value={meta.referenceGenome} mono />
        <MetaRow label="Sections" value={String(dataset.sectionCount)} mono />
        <MetaRow label="Assay" value={meta.assays.join(", ") || null} />
        <MetaRow label="Accession" value={meta.accession} mono />
        <MetaRow label="Source" value={meta.accessionDatabase} />
        <MetaRow label="License" value={meta.license} />
        <MetaRow label="Released" value={meta.released} mono />
        <MetaRow label="Publication" value={meta.publicationTitle} />
        <MetaRow label="DOI" value={meta.publicationDoi} mono />
      </div>

      <Sections dataset={dataset} />

      {meta.description && (
        <div className="drawer-section">
          <div className="section-label">Description</div>
          <p style={{ fontSize: 13, color: "#3b3b3b", margin: 0, lineHeight: 1.5 }}>
            {meta.description}
          </p>
        </div>
      )}

      <div className="drawer-section">
        <div className="section-label">Data location</div>
        <div className="location-box">
          {dataset.location ? (
            <a href={dataset.location} target="_blank" rel="noreferrer">
              {dataset.location}
            </a>
          ) : (
            "—"
          )}
        </div>
        <div className="drawer-actions">
          <Button
            sdsStyle="solid"
            sdsType="primary"
            href={dataset.downloadUrl ?? undefined}
            target="_blank"
            disabled={!dataset.downloadUrl}
          >
            Download
          </Button>
          <Button sdsStyle="outline" sdsType="secondary" onClick={() => onOpenViewer(dataset)}>
            Open viewer
          </Button>
        </div>
      </div>
    </aside>
  );
}
