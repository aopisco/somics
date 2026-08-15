# paperclip × somics — command summary (for team slides)

*One section ≈ one slide. All timings measured live on 2026-08-15, paperclip 0.7.37.*

## Search 30M+ papers from the terminal — 0.3 s

```bash
paperclip search -s pmc,biorxiv "spatial transcriptomics foundation model" -n 5
```

Titles, authors, DOIs, one-line summaries. Every search auto-saves a result set (`s_…`) for reuse.

## Your own PDFs live in the same index

```bash
paperclip search -s clipboard/somics "spatial omics foundation model"
```

Finds our uploaded key papers — TERRA, VirTues, KRONOS — with the same query interface as the public corpus.

## Papers are a filesystem

```bash
paperclip ls  /clipboard/somics
paperclip cat /clipboard/somics/<doc_id>/meta.json
paperclip cat /clipboard/somics/<doc_id>/content.lines | head
```

Metadata, full text, sections, and figures are all addressable paths.

## `map`: ask one question of every paper — 4 s for 3 papers

```bash
paperclip map --from s_85fd05d2 \
  "What spatial omics platforms does this paper use, and what is the largest dataset it trains on?"
```

Per-paper answers with line-number citations (e.g. "4.1 M pretraining samples, L24, L66").

## Scale it: schema-constrained extraction over 983 papers

```bash
paperclip search -s pmc,biorxiv --tag spatialomics -n 1000 \
  'spatial transcriptomics' 'spatial proteomics' ...        # → s_edf954f9, 983 papers

paperclip map --from s_edf954f9 --output-schema '{...JSON schema...}' \
  'Identify every spatial omics dataset this paper generated or analyzed...'   # → m_4df4abed, ~22 min

paperclip results m_4df4abed --save map-results.txt
```

**Result: 983 papers → 1,028 spatial-omics dataset rows in `data/literature_datasets.csv`** (platform, species, tissue, disease, accession, generated vs reused).

## Wrapped as a Claude skill

`/harvest-datasets "spatial proteomics of the human gut"` — Claude runs the search + extraction end-to-end and opens a PR adding new rows to the inventory.
