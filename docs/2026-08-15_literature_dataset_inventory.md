# Literature-derived spatial omics dataset inventory — build report

*Started 2026-08-15, last updated 2026-08-16 · built with Claude Code + the paperclip CLI*

## What this is

Three tables, built from the literature, fed in order:

| File | Grain | Rows |
|---|---|---|
| `data/literature_datasets.csv` | claim-level: one row per (dataset × source paper) | 2,180 from 385 papers |
| `data/datasets.csv` | curated: **one row per dataset**, keyed to its **original publication** | 1,604 |
| `data/model_dataset_usage.csv` | many-to-many: which paper/model uses which dataset | 3,478 across 816 papers |

Separately, `data/st_corpus.csv` (455 samples) is the TERRA supplementary table, maintained
by hand and byte-identical to the sheet it came from. It is *not* produced by this pipeline.

### The rule that shapes `datasets.csv`

A dataset's reference is the paper that **first released the data** — not the paper that
analyzed it. When data debuted in a model paper (TERRA's in-house Xenium pancreas, KRONOS's
private cohorts), that model paper *is* the original reference and
`first_published_by_model_paper = yes` (532 rows). Vendor datasets (10x, Bruker) carry the
vendor page. This is why the curated table needs the citation-tracing step below: the
analyzing paper cites datasets by reference number, and the reference list is where the
original lives.

`data_access_link` is the landing page or accession; `download_url` is a URL you can hand to
curl (st_corpus.csv semantics).

## Current state

**`data/datasets.csv`** — 1,604 datasets, 17 columns
- 986 with a resolved original-publication DOI/link
- 532 first published by the analyzing paper itself
- 839 with a `data_access_link`; 288 with a direct `download_url`
- 69 carry a `perturbation` annotation
- modality: 875 spatial transcriptomics, 119 spatial proteomics, 610 unclassified

**`data/literature_datasets.csv`** — 2,180 claim rows from 385 source papers.
1,028 of them carry a `data_downloadable` verdict (572 yes / 66 no / 38 unverified /
352 no link); the 1,152 rows added after that pass have not been verified — see Caveats.

## How it was built

### 1. Literature search

Seven seed queries unioned by paper id → **983 unique papers** (`s_edf954f9`): spatial
transcriptomics, spatial proteomics, spatial transcriptomics foundation model, spatial omics
pretrained representation, spatial proteomics deep learning cell phenotyping, virtual tissue,
spatial perturbations.

Later sweeps added perturbation vocabulary the seed queries missed entirely — see §5.

### 2. Claim-level extraction

`paperclip map` with a strict JSON schema, per paper: every spatial omics dataset the paper
**generated or analyzed**, with platform, species, tissue, disease, sample count,
accession, and origin (generated vs reused). 962/983 extracted cleanly on the first pass;
201 papers contained concrete datasets.

### 3. Key papers added by hand

- **TERRA** (bioRxiv 10.64898/2026.07.29.741565) — its supplementary sheet is byte-identical
  to `st_corpus.csv`, so the 456-sample pretraining corpus is *not* duplicated here; only its
  8 benchmark/analysis datasets are.
- **VirTues** (Nature s41586-026-10884-y) — its corpus is published through
  [spora](https://spora.epfl.ch/datasets.html); 31 IMC/CODEX/Orion/MIBI cohorts parsed from
  the site's structured data file, each already paired with its original paper.
- **KRONOS** (arXiv 2506.03373) — not in the paperclip corpus; fetched from arXiv. Public
  pretraining sources (HuBMAP, ImmunoAtlas, IBEX, Cell DIVE), private cohorts, and the
  cHL / DLBCL-1 / DLBCL-2 benchmarks.

### 4. Tracing datasets to their original publications

The step that makes `datasets.csv` possible. A second map pass asks each paper to expand its
dataset citations from its **own reference list** into (first author, title, journal, year),
flagging in-house data as first-published-by-this-paper. Claims are then deduped by
(original publication × platform family) and the cited originals resolved to DOIs through
Crossref with title-match verification.

Automated by **`scripts/trace_originals.py`**, which merges into both curated tables and
never modifies existing rows (new claims match into existing datasets by DOI).

### 5. Perturbation sweeps

The seed vocabulary missed perturbation work almost completely. A targeted probe returned
54 papers of which **47 were unmined**; a wider ten-query sweep returned 246 of which **182
were unmined**. Together these added ~1,100 claim rows covering CRISPR screens, drug and
treatment series, immunotherapy response, injury time courses, transgenic models, and
optical pooled screening (PerturbView, CRISPRmap, CellPaint-POSH, Spatial Perturb-Seq,
PerturbSpace, Perturb-FISH, SpatialProp).

### 6. Link verification and download URLs

- **`scripts/verify_downloads.py`** probes each unique link and writes a `data_downloadable`
  verdict: Zenodo through the records API, GEO/ArrayExpress/ENA/PRIDE/BioStudies through
  their APIs, everything else HEAD with a ranged-GET fallback. Bot-blocked hosts (10x
  Cloudflare 403s) are recorded as `unverified`, not as failures.
- **`scripts/resolve_download_urls.py`** fills `download_url`: Zenodo (single file → content
  URL; ≤300 MB record → files-archive zip; larger → largest file), GEO bulk download, Dryad,
  and direct-file passthrough. Every URL is probed before it is written.

Spot-validated by actually downloading: spatialLIBD DLPFC h5 (4,226 spots × 33,538 genes) and
a Zenodo Stereo-seq h5ad (53,310 cells with spatial coords) both load cleanly.

## What's new relative to the TERRA sheet

Measured against `st_corpus.csv` at the 1,028-row mark: only **36 of 421 unique links** were
shared (≈91% of linked datasets new), spatial proteomics was **entirely absent** from the
sheet, ~416 rows sat on platforms it doesn't cover (Stereo-seq, legacy ST, CODEX, IMC, GeoMx,
osmFISH, DBiT-seq, MIBI…), and 59 rows were non-human/mouse. The inventory has since grown
by a further ~1,150 claim rows, so the gap is wider now.

## Caveats

- **Verification coverage is partial.** `data_downloadable` was computed when the table held
  1,028 rows; the 1,152 rows added since are blank. Re-run `verify_downloads.py` to refresh.
- **618 curated rows lack an original-publication link** — Crossref couldn't confidently match
  the citation, or the source is a vendor/portal resource. Flagged in `notes`, never guessed.
- **610 rows have no modality** — the platform string didn't match either pattern. Blank is
  deliberate; a guess would be a claim the table can't support.
- **The `perturbation` column is partial (69 rows).** The trace schema doesn't carry the
  field, so values were back-filled from claim rows by source paper + name-token overlap.
  Adding `perturbation` to the trace schema would make it reliable.
- **Dedup is conservative** (same original publication + platform family); near-duplicates
  from differently-worded citations may remain.
- **Extraction is LLM-based.** Spot-checked throughout, not verified row by row. The one
  exhaustively verified pass was the 38 second-pass link candidates, of which only the 12
  confirmed correct were kept.
- **Two recurring extraction failures** are documented in the harvest-datasets skill: a
  modeling paper extracting zero datasets usually means paperclip dropped its dataset *table
  body* (hit on DRIFT and SpatialProp; recover by fetching the PDF), and perturbation
  datasets need their own search vocabulary.

## Reproducing / extending

`.claude/skills/harvest-datasets/SKILL.md` is the operating manual — search, dedup against
known paper ids, extract, trace originals, merge, resolve downloads, commit with the queries
and result IDs in the message. It also records the paperclip limits worth knowing: chunk maps
over ~250 papers, recover failures with `map --resume --retry-failed`, and don't try to feed
a `sql`-saved result set to `map --from`.

Scripts: `scripts/trace_originals.py`, `scripts/verify_downloads.py`,
`scripts/resolve_download_urls.py`, `.claude/skills/harvest-datasets/scripts/append_datasets.py`.
