import { Button } from "@czi-sds/components";

import { formatCount } from "../qc";
import type { CorpusIndex } from "../types";
import { Logo } from "./Logo";

function Stat({ figure, label, tone }: { figure: string; label: string; tone?: string }) {
  return (
    <div>
      <div className="stat-figure" style={tone ? { color: tone } : undefined}>
        {figure}
      </div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function Landing({
  index,
  value,
  onChange,
  onSubmit,
  onBrowseAll,
}: {
  index: CorpusIndex;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  onBrowseAll: () => void;
}) {
  const { stats } = index;
  const unitNouns = new Set(index.datasets.map((dataset) => dataset.unitNoun));
  return (
    <div className="app">
      <header className="header" style={{ padding: "0 24px" }}>
        <span className="brand">
          <Logo />
          <span className="brand-word">Somics</span>
        </span>
        <button type="button" className="link-button" onClick={onBrowseAll}>
          Browse all datasets
        </button>
      </header>

      <div className="landing-body">
        <div className="landing-inner">
          <span className="eyebrow">
            <span className="eyebrow-dot" />
            Somics corpus builder
          </span>
          <h1 className="landing-h1">Find spatial omics data you can train on</h1>
          <p className="landing-sub">
            Describe the spatial transcriptomic or proteomic data you need. Somics returns matching
            datasets with QC surfaced on every one.
          </p>

          <form
            className="landing-chat"
            onSubmit={(event) => {
              event.preventDefault();
              onSubmit(value);
            }}
          >
            <span className="chat-dot" />
            <input
              value={value}
              placeholder="e.g. subcellular colon cancer, transcripts/cell > 90"
              onChange={(event) => onChange(event.target.value)}
              aria-label="Describe the data you need"
            />
            <Button sdsStyle="solid" sdsType="primary" type="submit">
              Search
            </Button>
          </form>

          {/* Generated from the live corpus, so a click always returns rows. */}
          <div className="starters">
            {index.starterPrompts.map((prompt) => (
              <button
                type="button"
                className="starter-row"
                key={prompt}
                onClick={() => {
                  onChange(prompt);
                  onSubmit(prompt);
                }}
              >
                <span>{prompt}</span>
                <span className="starter-enter">↵</span>
              </button>
            ))}
          </div>

          <div className="explainer">
            <div className="section-label">Why spatial</div>
            <img src="why-spatial.png" alt="" />
          </div>

          <div className="stats">
            <Stat figure={String(stats.datasets)} label="Datasets" />
            <Stat figure={formatCount(stats.units)} label={[...unitNouns].join(" / ")} />
            <Stat figure={String(stats.platforms)} label="Platforms" />
            <Stat
              figure={`${stats.passAllQc} / ${stats.datasets}`}
              label="Pass all QC"
              tone="#238444"
            />
          </div>

          <p className="footnote">
            Somics indexes public spatial transcriptomic and proteomic releases. QC is recomputed
            per platform. Index built {index.generatedAt.slice(0, 10)} from {index.atlasDir}.
          </p>
        </div>
      </div>
    </div>
  );
}
