/** The fixed right-hand column: the calm, dense, scannable science half of the viewer. */

import type { JSX, ReactNode } from "react";

import type { LoadPhase } from "../state";
import {
  selectCurrentSample,
  selectOrgan,
  selectOrgans,
  selectSamplesByNode,
  useStore,
} from "../state";
import type { OrganNode, Sample, Species } from "../types";
import "./Panel.css";
import { backLabel, formatCount, formatExtent, humanizeKey } from "./format";

const SPECIES_LABEL: Record<Species, string> = {
  human: "human",
  rat: "rat",
  zebrafish: "zebrafish",
};

/** Same species crumb text as the breadcrumb in `App.tsx`'s `Hud` — kept in sync by eye, since
 * the two live in different components but must read as one vocabulary (see `backLabel`). */
function speciesCrumbLabel(species: Species): string {
  return species === "rat" ? "🐀 Rat" : "🧍 Human";
}

export function Panel(): JSX.Element {
  const store = useStore();
  const organ = selectOrgan(store, store.node);
  const sample = selectCurrentSample(store);

  let body: JSX.Element;
  if (store.sample) {
    body = sample ? (
      <SampleSection sample={sample} bodySpecies={store.species} />
    ) : (
      <p className="panel-empty">This sample is not in the catalog.</p>
    );
  } else if (store.node) {
    body = organ ? (
      <OrganSection
        organ={organ}
        samples={selectSamplesByNode(store)[organ.node_id] ?? []}
        onSelectSample={store.selectSample}
      />
    ) : (
      <p className="panel-empty">This organ has no anatomy on the current body.</p>
    );
  } else {
    body = (
      <IntroSection
        phase={store.catalogPhase}
        organs={selectOrgans(store)}
        samplesByNode={selectSamplesByNode(store)}
        onSelectNode={store.selectNode}
      />
    );
  }

  const back = backLabel(store.lod, {
    organ: organ?.label ?? null,
    section: sample?.section_id ?? null,
    species: speciesCrumbLabel(store.species),
  });

  return (
    <aside className="panel">
      {back && (
        <button className="panel-button panel-back" onClick={() => store.zoomOut()}>
          <span aria-hidden="true">←</span> {back}
        </button>
      )}
      {body}
      <p className="panel-footer">This atlas snapshot is read-only and public.</p>
    </aside>
  );
}

function IntroSection({
  phase,
  organs,
  samplesByNode,
  onSelectNode,
}: {
  phase: LoadPhase;
  organs: OrganNode[];
  samplesByNode: Record<string, Sample[]>;
  onSelectNode: (nodeId: string) => void;
}): JSX.Element {
  if (phase === "loading") {
    return (
      <section className="panel-section">
        <p className="panel-loading">Loading the atlas catalog.</p>
      </section>
    );
  }

  const withSamples = organs.filter((organ) => (samplesByNode[organ.node_id]?.length ?? 0) > 0);
  const totalSamples = withSamples.reduce(
    (sum, organ) => sum + (samplesByNode[organ.node_id]?.length ?? 0),
    0,
  );
  const totalCells = withSamples.reduce(
    (sum, organ) =>
      sum + (samplesByNode[organ.node_id] ?? []).reduce((s, sample) => s + sample.n_cells, 0),
    0,
  );
  const emptyCount = organs.length - withSamples.length;

  return (
    <section className="panel-section">
      <h1 className="panel-title">Somics spatial atlas</h1>
      <p>
        A browsable view of the somics spatial transcriptomics atlas: real tissue sections mapped
        onto a schematic body. Click a glowing organ to see what the atlas holds there.
      </p>
      <p className="panel-stat">
        {formatCount(totalSamples)} sample{totalSamples === 1 ? "" : "s"},{" "}
        {formatCount(totalCells)} cells total
      </p>

      {withSamples.length > 0 ? (
        <ul className="panel-list">
          {withSamples.map((organ) => {
            const samples = samplesByNode[organ.node_id] ?? [];
            const cells = samples.reduce((s, sample) => s + sample.n_cells, 0);
            return (
              <li key={organ.node_id}>
                <button className="panel-row" onClick={() => onSelectNode(organ.node_id)}>
                  <span className="panel-row-label">{organ.label}</span>
                  <span className="panel-row-value">
                    {samples.length} sample{samples.length === 1 ? "" : "s"} ·{" "}
                    {formatCount(cells)} cells
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="panel-empty">No organs have samples yet.</p>
      )}

      <p className="panel-muted">
        {emptyCount} of {organs.length} organs are waiting for data.
      </p>
    </section>
  );
}

function OrganSection({
  organ,
  samples,
  onSelectSample,
}: {
  organ: OrganNode;
  samples: Sample[];
  onSelectSample: (sectionUid: string) => void;
}): JSX.Element {
  return (
    <section className="panel-section">
      <h1 className="panel-title">{organ.label}</h1>
      <p className="panel-muted">{organ.system}</p>

      {samples.length === 0 ? (
        <p className="panel-empty">
          The atlas has no samples for this organ yet — use the back control above to return to
          the whole body.
        </p>
      ) : (
        <ul className="panel-cards">
          {samples.map((sample) => (
            <li key={sample.section_uid}>
              <button
                className="panel-card"
                onClick={() => onSelectSample(sample.section_uid)}
              >
                <span className="panel-card-title">{sample.section_id}</span>
                <span className="panel-card-meta">
                  {sample.technology ?? "unknown technology"} · {formatCount(sample.n_cells)}{" "}
                  cells
                </span>
                {sample.disease && <span className="panel-card-meta">{sample.disease}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SampleSection({
  sample,
  bodySpecies,
}: {
  sample: Sample;
  bodySpecies: Species;
}): JSX.Element {
  const mismatch = sample.species !== bodySpecies;

  return (
    <section className="panel-section">
      <h1 className="panel-title">{sample.sample_name ?? sample.section_id}</h1>

      {mismatch && (
        <p className="panel-callout">
          Species mismatch: this sample&apos;s donor is {SPECIES_LABEL[sample.species]}
          {sample.organism ? ` (${sample.organism})` : ""}, but the body on screen is displaying{" "}
          {SPECIES_LABEL[bodySpecies]} anatomy. The {SPECIES_LABEL[bodySpecies]} organ stands in
          for the {SPECIES_LABEL[sample.species]} one by anatomical homology.
        </p>
      )}

      <dl className="panel-fields">
        <Field label="Organism" value={sample.organism ?? SPECIES_LABEL[sample.species]} />
        <Field label="Tissue" value={sample.tissue} />
        <Field label="Disease" value={joinFields(sample.disease, sample.disease_state)} />
        <Field label="Technology" value={joinFields(sample.technology, sample.assay)} />
        <Field label="Spatial unit" value={sample.spatial_unit} />
        <Field label="Cells" value={formatCount(sample.n_cells)} />
        <Field label="Section extent" value={formatExtent(sample.extent_um)} />
        <Field label="Preservation" value={sample.preservation} />
        <Field
          label="Feature spaces"
          value={sample.feature_spaces.length > 0 ? sample.feature_spaces.join(", ") : null}
        />
      </dl>

      <p className="panel-muted">{modalityLine(sample)}</p>

      <KeyValueTable title="Donor" record={sample.donor} />
      <KeyValueTable title="Panel" record={sample.panel} />

      {sample.dataset_description && (
        <details className="panel-details">
          <summary>Dataset description</summary>
          <p>{sample.dataset_description}</p>
        </details>
      )}

      {sample.data_access_link && (
        <p>
          <a
            className="panel-link"
            href={sample.data_access_link}
            target="_blank"
            rel="noreferrer"
          >
            {sample.accession_database ?? "Data access"}
          </a>
        </p>
      )}
    </section>
  );
}

function Field({ label, value }: { label: string; value: ReactNode | null }): JSX.Element | null {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="panel-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function joinFields(a: string | null, b: string | null): string | null {
  const parts = [a, b].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The deepest zoom level renders real microscopy imagery, which needs a morphology crop. */
function modalityLine(sample: Sample): string {
  const present: string[] = [];
  if (sample.has_gene_expression) present.push("gene expression");
  if (sample.has_morphology_crop) present.push("morphology imagery");
  if (sample.has_he_crop) present.push("hematoxylin and eosin (H&E) imagery");
  if (present.length === 0) return "This sample carries no cell-level imagery or expression data.";
  const line = `Carries ${present.join(", ")}.`;
  return sample.has_morphology_crop
    ? `${line} The cell zoom level works for this sample.`
    : `${line} The cell zoom level needs morphology imagery, which this sample does not have.`;
}

function KeyValueTable({
  title,
  record,
}: {
  title: string;
  record: Record<string, string | number | boolean>;
}): JSX.Element | null {
  const entries = Object.entries(record);
  if (entries.length === 0) return null;
  return (
    <div className="panel-kv">
      <h2 className="panel-subtitle">{title}</h2>
      <table>
        <tbody>
          {entries.map(([key, value]) => (
            <tr key={key}>
              <th>{humanizeKey(key)}</th>
              <td>{String(value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
