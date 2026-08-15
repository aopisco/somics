# somics corpus builder

A repository UI for assembling training corpora out of the spatial atlas: chat bar for fuzzy
intent, faceted sidebar for precise filtering, and QC surfaced on every card so a dataset's
trainability reads at a glance.

Built to the design packet in `design_handoff_somics_corpus_builder/` — React 19 +
[`@czi-sds/components`](https://sds.czi.design) v24, served by the same FastAPI process as the 3D
viewer.

## Run it

The UI renders a precomputed index, so build that first:

```bash
uv run python scripts/build_corpus_index.py   # reads the atlas on R2 -> data/corpus_index.json
uv run python -m somics.viewer                # API on http://127.0.0.1:8787
cd web && npm install && npm run dev          # UI on http://127.0.0.1:5274
```

Open http://127.0.0.1:5274. To serve it from the API as a single process instead:

```bash
cd web && npm run build     # -> web/dist, mounted at /corpus
uv run python -m somics.viewer
```

Then http://127.0.0.1:8787/corpus/.

## Where the data comes from

Everything on screen — datasets, facet values and counts, QC verdicts, starter prompts, the
landing stats — comes from `data/corpus_index.json`, written by `scripts/build_corpus_index.py`
from the atlas on R2. There is no sample data in this app, and no query layer behind it: the
corpus is small enough that the whole index is a 12 KB file and every count is a loop over an
array.

**The index is a snapshot.** Rerun the script after every ingest; nothing warns you when it is
stale.

Two consequences worth knowing before reading the code:

- **QC is computed in the build script, not at ingest.** The schema carries no dataset-level QC
  fields. Of the design packet's six metrics, two are computable from the atlas today
  (transcripts/cell, negative-probe rate); the rest render as grey `n/a` chips. See
  `docs/2026-08-15_corpus_builder_ui_mapping.md`.
- **"Passes segmentation QC" is shown but inert**, because no segmentation verdict exists to
  filter on. It is rendered disabled rather than dropped, so the gap is visible instead of silent.

## Layout

```
src/types.ts        the index's shape — the contract this app shares with the build script
src/query.ts        free text -> ParsedQuery + the sentence the SHOWING bar renders
src/filters.ts      filtering, facet counts (own filter dropped), toggle counts
src/theme.ts        SDS light theme with the accent ramp moved onto blue
src/styles.css      the packet's tokens and layout
src/App.tsx         view state, URL sync, the results workspace
src/components/     Sidebar, DatasetCard, DetailDrawer, ChatBar, Landing, Logo
```

`npm test` covers the parser and the filter/count logic — the two places where a silent
mistake would mislead rather than break.

## Chat parsing

`parseQuery` is a keyword matcher over the corpus's own facet vocabulary, plus a small alias table
(`DLPFC`, `CRC`, …) and two numeric patterns for transcripts-per-cell floors. It is deterministic
and runs offline.

Anything it does not recognise becomes `freeText` and the SHOWING bar says `text match "…"` rather
than pretending to have understood — the interpreted line is the product's trust surface. A model
call belongs *on top of* this, with this as the fallback, not instead of it.

## Known gaps

- Compare, Export corpus, and Add to corpus are rendered but not wired — the packet names them
  without specifying behavior, and export format is a product decision.
- Below ~1200px with the drawer open the sidebar should collapse; it does not yet.
- Inter and IBM Plex Mono are requested by the stylesheet but not bundled, so they fall back to
  the system UI and mono faces unless installed locally.
