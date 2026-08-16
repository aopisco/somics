/**
 * The spatial information for whatever is selected: the calm, dense, scannable science half of
 * the viewer.
 *
 * It used to be a fixed right-hand column. The user asked for it as a floating window locked to
 * the frame instead, so this file is now only the contents; `FloatingPanel` is the window, and the
 * heading each section used to carry is the window's title bar.
 */

import type { JSX, ReactNode } from "react";
import { useEffect, useRef } from "react";

import type { LoadPhase } from "../state";
import {
  selectCurrentSample,
  selectOrgan,
  selectOrgans,
  selectSamplesByNode,
  useStore,
} from "../state";
import { SPECIES_CRUMB } from "../types";
import type { OrganNode, Sample, Species } from "../types";
import { FloatingPanel } from "./FloatingPanel";
import "./Panel.css";
import { backLabel, formatCount, formatExtent, humanizeKey } from "./format";
import { Morphology } from "./Morphology";
import { SectionView } from "./SectionView";

const SPECIES_LABEL: Record<Species, string> = {
  human: "human",
  rat: "rat",
  zebrafish: "zebrafish",
};

export function Panel(): JSX.Element {
  const store = useStore();
  const organ = selectOrgan(store, store.node);
  const sample = selectCurrentSample(store);

  // A new selection is a new document, so it starts at the top. Without this, switching between two
  // of the twelve brain sections keeps the old scroll offset and the plot — the whole point of the
  // panel, and the first thing in it — opens below the fold.
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [store.sample, store.node]);

  let title: string;
  let body: JSX.Element;
  if (store.sample) {
    title = sample?.sample_name ?? sample?.section_id ?? "Unknown sample";
    body = sample ? (
      <SampleSection sample={sample} bodySpecies={store.species} focusUm={store.focusUm} />
    ) : (
      <p className="panel-empty">This sample is not in the catalog.</p>
    );
  } else if (store.node) {
    title = organ?.label ?? store.node;
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
    title = "Somics spatial atlas";
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
    species: SPECIES_CRUMB[store.species],
  });

  return (
    <FloatingPanel title={title}>
      <div className="panel" ref={bodyRef}>
        {back && (
          <button className="panel-button panel-back" onClick={() => store.zoomOut()}>
            <span aria-hidden="true">←</span> {back}
          </button>
        )}
        {body}
        <p className="panel-footer">This atlas snapshot is read-only and public.</p>
      </div>
    </FloatingPanel>
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
  const allSamples = withSamples.flatMap((organ) => samplesByNode[organ.node_id] ?? []);
  const totalCells = allSamples.reduce((sum, sample) => sum + sample.n_cells, 0);
  const emptyCount = organs.length - withSamples.length;

  return (
    <section className="panel-section">
      <p>
        A browsable view of the somics spatial transcriptomics atlas: real tissue sections mapped
        onto a schematic body. Click a glowing organ to see what the atlas holds there.
      </p>
      <p className="panel-stat">
        {formatCount(totalSamples)} sample{totalSamples === 1 ? "" : "s"},{" "}
        {formatCount(totalCells)} {unitNoun(allSamples)} total
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
                    {formatCount(cells)} {unitNoun(samples)}
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
                  {unitNoun([sample])}
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
  focusUm,
}: {
  sample: Sample;
  bodySpecies: Species;
  focusUm: [number, number] | null;
}): JSX.Element {
  const mismatch = sample.species !== bodySpecies;

  return (
    <section className="panel-section">
      {mismatch && (
        <p className="panel-callout">
          Species mismatch: this sample&apos;s donor is {SPECIES_LABEL[sample.species]}
          {sample.organism ? ` (${sample.organism})` : ""}, but the body on screen is displaying{" "}
          {SPECIES_LABEL[bodySpecies]} anatomy. The {SPECIES_LABEL[bodySpecies]} organ stands in
          for the {SPECIES_LABEL[sample.species]} one by anatomical homology.
        </p>
      )}

      {/* The measured points, flat and in screen space. They used to be a THREE.Points cloud the
          camera flew into; the user asked twice for them here instead. */}
      <SectionView unit={unitNoun([sample])} />

      <Morphology sample={sample} focusUm={focusUm} />

      <dl className="panel-fields">
        <Field label="Section" value={sample.section_id} />
        <Field label="Organism" value={sample.organism ?? SPECIES_LABEL[sample.species]} />
        <Field label="Tissue" value={sample.tissue} />
        <Field label="Disease" value={joinFields(sample.disease, sample.disease_state)} />
        <Field label="Technology" value={joinFields(sample.technology, sample.assay)} />
        <Field label="Spatial unit" value={sample.spatial_unit} />
        <Field label={humanizeKey(unitNoun([sample]))} value={formatCount(sample.n_cells)} />
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

/**
 * What `n_cells` actually counts. Visium resolves ~55 µm spots and Xenium resolves cells, so the
 * same field means different things three orders of magnitude apart; calling a spot a cell reads
 * as a hundredfold error. Across a mixed set the generic "cells" is the honest fallback.
 */
function unitNoun(samples: Sample[]): string {
  return samples.length > 0 && samples.every((s) => s.spatial_unit === "spot") ? "spots" : "cells";
}

function joinFields(a: string | null, b: string | null): string | null {
  const parts = [a, b].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** What the atlas actually holds for this sample, so an empty imagery box is explained. */
function modalityLine(sample: Sample): string {
  const present: string[] = [];
  if (sample.has_gene_expression) present.push("gene expression");
  if (sample.has_morphology_crop) present.push("morphology imagery");
  if (sample.has_he_crop) present.push("hematoxylin and eosin (H&E) imagery");
  if (present.length === 0) return "This sample carries no cell-level imagery or expression data.";
  const line = `Carries ${present.join(", ")}.`;
  return sample.has_morphology_crop || sample.has_he_crop
    ? `${line} The imagery above is cropped from it.`
    : `${line} There is no imagery to crop, so only the points are shown.`;
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
