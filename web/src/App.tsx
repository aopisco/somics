import { useEffect, useMemo, useState } from "react";
import { Button } from "@czi-sds/components";

import { ChatBar } from "./components/ChatBar";
import { DatasetCard } from "./components/DatasetCard";
import { DetailDrawer } from "./components/DetailDrawer";
import { Landing } from "./components/Landing";
import { Logo } from "./components/Logo";
import { Sidebar } from "./components/Sidebar";
import { applyFilters } from "./filters";
import { parseQuery, type ParsedQuery } from "./query";
import { formatCount } from "./qc";
import { EMPTY_FILTERS, EMPTY_TOGGLES } from "./types";
import type { CorpusIndex, Dataset, Filters, Toggles } from "./types";

type View = "home" | "results";

/** The query and the open dataset live in the URL, so a link reopens the view. */
function readHash(): { view: View; q: string; selected: string | null } {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const q = params.get("q") ?? "";
  const selected = params.get("d");
  const view = params.get("v") === "results" || q || selected ? "results" : "home";
  return { view, q, selected };
}

function writeHash(view: View, q: string, selected: string | null) {
  const params = new URLSearchParams();
  if (view === "results") params.set("v", "results");
  if (q) params.set("q", q);
  if (selected) params.set("d", selected);
  const hash = params.toString();
  const next = `${window.location.pathname}${hash ? `#${hash}` : ""}`;
  window.history.replaceState(null, "", next);
}

export function App() {
  const initial = readHash();
  const [index, setIndex] = useState<CorpusIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(initial.view);
  const [chatValue, setChatValue] = useState(initial.q);
  const [submitted, setSubmitted] = useState(initial.q);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [toggles, setToggles] = useState<Toggles>(EMPTY_TOGGLES);
  const [selected, setSelected] = useState<string | null>(initial.selected);
  const [pages, setPages] = useState<Record<string, string>>({});

  useEffect(() => {
    // Which datasets have a precomputed viewer page. Absent is not an error:
    // the pages are built by their own script, and until they are, the card's
    // viewer button simply stays disabled.
    fetch("/api/dataset-pages")
      .then((response) => (response.ok ? response.json() : { pages: {} }))
      .then((manifest: { pages?: Record<string, string> }) => setPages(manifest.pages ?? {}))
      .catch(() => setPages({}));
  }, []);

  useEffect(() => {
    fetch("/api/corpus")
      .then((response) => {
        if (!response.ok) throw new Error(`corpus index unavailable (${response.status})`);
        return response.json() as Promise<CorpusIndex>;
      })
      .then(setIndex)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  useEffect(() => {
    writeHash(view, submitted, selected);
  }, [view, submitted, selected]);

  // Hand-edited or externally set hashes drive the app too, so a pasted link
  // reopens the view without a reload.
  useEffect(() => {
    const onHashChange = () => {
      const next = readHash();
      setView(next.view);
      setSubmitted(next.q);
      setChatValue(next.q);
      setSelected(next.selected);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const query: ParsedQuery | null = useMemo(
    () => (index ? parseQuery(submitted, index) : null),
    [submitted, index],
  );

  const visible = useMemo(
    () => (index ? applyFilters(index.datasets, query, filters, toggles) : []),
    [index, query, filters, toggles],
  );

  const selectedDataset = visible.find((dataset) => dataset.id === selected) ?? null;

  if (error) {
    return (
      <div className="status-banner">
        {error}. Run <code>&nbsp;uv run python scripts/build_corpus_index.py</code>.
      </div>
    );
  }
  if (!index) return <div className="status-banner">Loading corpus…</div>;

  const submit = (value: string) => {
    setSubmitted(value.trim());
    setSelected(null);
    setView("results");
  };

  const openViewer = (dataset: Dataset) => {
    // The dataset's precomputed page: section imagery, spatial maps, and
    // feature summaries, all rendered ahead of time by
    // scripts/build_dataset_pages.py and served as static files.
    const slug = pages[dataset.id];
    if (slug) window.open(`/datasets/${slug}/`, "_blank", "noreferrer");
  };

  if (view === "home") {
    return (
      <Landing
        index={index}
        value={chatValue}
        onChange={setChatValue}
        onSubmit={submit}
        onBrowseAll={() => submit("")}
      />
    );
  }

  return (
    <div className="workspace">
      <header className="header">
        <button
          type="button"
          className="brand"
          onClick={() => {
            setView("home");
            setSelected(null);
          }}
        >
          <Logo />
          <span className="brand-word">Somics</span>
          <span className="brand-slash">/</span>
          <span className="brand-sub">Corpus builder</span>
        </button>
        <div className="header-right">
          <span className="header-count">
            {visible.length} / {index.datasets.length} datasets
          </span>
          <Button sdsStyle="minimal" sdsType="primary" disabled={visible.length < 2}>
            Compare
          </Button>
          <Button sdsStyle="solid" sdsType="primary" disabled={visible.length === 0}>
            Export corpus
          </Button>
        </div>
      </header>

      <div className="workspace-body">
        <Sidebar
          index={index}
          query={query}
          filters={filters}
          toggles={toggles}
          onFilters={setFilters}
          onToggles={setToggles}
          onReset={() => {
            setFilters(EMPTY_FILTERS);
            setToggles(EMPTY_TOGGLES);
          }}
        />

        <main className="results">
          {query && (
            <div className="showing-bar">
              <span className="showing-label">Showing</span>
              <span className="showing-text">{query.interpretation}</span>
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  setSubmitted("");
                  setChatValue("");
                }}
              >
                Clear
              </button>
            </div>
          )}

          {visible.length === 0 ? (
            <div className="empty">
              No datasets match. Try relaxing a QC threshold or clearing the query.
            </div>
          ) : (
            <div className="grid">
              {visible.map((dataset) => (
                <DatasetCard
                  key={dataset.id}
                  dataset={dataset}
                  selected={dataset.id === selected}
                  onSelect={() => setSelected(dataset.id === selected ? null : dataset.id)}
                />
              ))}
            </div>
          )}

          <p className="footnote">
            {formatCount(index.stats.units)} spatial units across {index.stats.sections} sections,
            indexed {index.generatedAt.slice(0, 10)}.
          </p>
        </main>
      </div>

      {selectedDataset && (
        <DetailDrawer
          dataset={selectedDataset}
          onClose={() => setSelected(null)}
          onOpenViewer={openViewer}
          hasViewer={Boolean(pages[selectedDataset.id])}
        />
      )}

      <ChatBar
        value={chatValue}
        suggestions={index.starterPrompts}
        drawerOpen={Boolean(selectedDataset)}
        onChange={setChatValue}
        onSubmit={submit}
      />
    </div>
  );
}
