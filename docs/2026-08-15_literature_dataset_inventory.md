# Literature-derived spatial omics dataset inventory — build report

*2026-08-15 · built with Claude Code + the paperclip CLI*

## Goal

Create a spreadsheet of all spatial omics datasets found via a literature search, modeled on
the TERRA supplementary table (which is `data/st_corpus.csv`, 456 samples). Result:
**`data/literature_datasets.csv` — 1,028 dataset rows, 678 with a data access link.**

## Sources

| found_via | rows | what it is |
|---|---|---|
| `literature_search` | 981 | structured extraction from a 983-paper paperclip search |
| `VirTues_spora_corpus` | 31 | VirTues' curated corpus from spora.epfl.ch, with direct download links |
| `TERRA_paper` | 8 | TERRA's benchmark/analysis datasets (pretraining corpus lives in `st_corpus.csv`, not duplicated) |
| `KRONOS_paper` | 8 | KRONOS pretraining sources and benchmark cohorts |

## How it was built

### 1. Literature search (paperclip)

Seven queries, results unioned by paper id → **983 unique papers** (search set `s_edf954f9`):
`spatial transcriptomics`, `spatial proteomics`, `spatial transcriptomics foundation model`,
`spatial omics pretrained representation`, `spatial proteomics deep learning cell phenotyping`,
`virtual tissue`, `spatial perturbations`.

### 2. Structured extraction

`paperclip map` over all 983 papers with a strict JSON schema asking, per paper, for every
spatial omics dataset it **generated or analyzed**: name, platform, species, tissue, disease,
sample count, accession/link, origin (generated vs reused). 962 papers extracted cleanly
(21 schema failures); **201 papers contained concrete datasets → 981 dataset rows**.
Paper title/DOI/year joined from each paper's `meta.json`.

### 3. Key papers added manually

- **TERRA** (bioRxiv 10.64898/2026.07.29.741565): fetched into the paperclip clipboard; its
  supplementary Google Sheet was confirmed **byte-identical** to `data/st_corpus.csv`, so the
  456-sample pretraining corpus was not duplicated. Its 8 benchmark/analysis datasets
  (CosMx NSCLC, Xenium melanoma, ISS CARTANA reproductive tract, Xenium skin atlas,
  fetal + adult pancreas Xenium, mouse-brain MERFISH and STARmap) were added as rows.
- **VirTues** (Nature s41586-026-10884-y): its full corpus is published through
  [spora](https://spora.epfl.ch/datasets.html); the structured dataset list (31 IMC/CODEX/
  Orion/MIBI cohorts with technology, patient counts, and data URLs) was parsed from the
  site's data file and added.
- **KRONOS** (arXiv 2506.03373, "A Foundation Model for Spatial Proteomics"): not in the
  paperclip corpus, fetched from arXiv. Added its public pretraining sources (HuBMAP CODEX,
  ImmunoAtlas, IBEX/Zenodo, CellDive/IDR), its private multi-institution pretraining cohorts,
  and its cHL / DLBCL-1 / DLBCL-2 benchmarks.

### 4. Missing-link fill (second pass)

362 rows initially lacked a `data_access_link`; 353 traced to 112 unique papers.

1. Accession-pattern grep (GEO, ArrayExpress, Zenodo, figshare, Dryad, Synapse, dbGaP,
   10x dataset pages, cellxgene, HuBMAP, Broad SCP, Mendeley) over the full text of all 112 papers.
2. Heuristic matching of accessions to dataset rows (token overlap in a narrow window around
   each accession; sole-link/sole-row pairing).
3. **Manual verification of every candidate** against the source paper's text. Only 12 of 38
   candidates were confirmed and kept; the rest were code-only deposits, scRNA-seq *reference*
   accessions cited next to spatial datasets, or tissue mismatches — those rows stay null
   rather than carry a wrong link.

### 5. What's new vs the TERRA sheet (`st_corpus.csv`)

- Only **36 of 421 unique links** in the inventory also appear in the sheet (63 rows, mostly 10x demo datasets); **~91% of linked datasets are new**.
- **Spatial proteomics: 84 rows** — a modality entirely absent from the sheet.
- **~416 rows on platforms the sheet doesn't cover**: Stereo-seq, legacy ST arrays, CODEX, IMC, GeoMx, osmFISH, DBiT-seq, spatial ATAC–RNA, MIBI, Curio Seeker, and more.
- **59 rows beyond human/mouse**: zebrafish, Drosophila, Arabidopsis, axolotl, macaque, rat.

## Caveats

- Rows are claim-level: one row per dataset *per citing paper*, so popular public datasets
  (DLPFC Visium, MERFISH mouse brain, 10x demos) appear multiple times under different source
  papers. Dedup/normalization is a planned follow-up (see the `harvest-datasets` skill notes:
  platform aliases like "10x Visium"/"Visium" currently fragment the same platform).
- ~310 literature rows remain linkless — mostly datasets mentioned without any stated
  accession (49 of the 112 papers contain no data-repository reference at all). Recovering
  those requires tracing each dataset to its original publication.
- ~20 rows are non-spatial companion datasets (scRNA-seq, CITE-seq) kept because papers
  analyzed them alongside spatial data.
- Extraction used an LLM reader (paperclip quick-reader with a locked JSON schema);
  spot-checked but not exhaustively verified row-by-row. Only the 12 second-pass link fills
  were individually verified against paper text.

## Commit trail

- `3c4badb` — Add literature-derived spatial omics dataset inventory (1,028 rows)
- `ec3ceb5` — Fill 12 verified data-access links
- `798ee27` — Add harvest-datasets skill (incremental future harvests)
