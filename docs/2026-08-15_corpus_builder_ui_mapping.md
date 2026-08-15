# Handoff: corpus builder UI — data mapping

Written 2026-08-15, against the design packet at `design_handoff_somics_corpus_builder/`. This is
the packet's Step 1 deliverable: the UI's `Dataset` contract diffed against what the atlas on R2
actually holds, plus what got precomputed instead of built.

## Decisions this reflects

1. **The corpus is the atlas on R2** (`s3://epiblast-public/somics_spatial_atlas`), nothing else.
   `data/datasets.csv` and `data/literature_datasets.csv` are not UI sources.
2. **One card per ingested dataset** — for the LIBD DLPFC pilot that is one card per section, 13
   in total. `scripts/build_corpus_index.py --group-by study` collapses them into 2 study cards
   instead; either grain carries its members under `sections`.
3. **Everything is precomputed** into `data/corpus_index.json` by
   `scripts/build_corpus_index.py`. Hackathon scope: no schema change, no re-ingest, no query layer
   behind the UI. The browser reads a static file.

## What the corpus is

Read from R2 on 2026-08-15:

| | |
|---|---|
| Ingested datasets | **13** (`list_datasets()` returns 26 rows — one per dataset *per feature space*, so summing `n_rows` over it double-counts) |
| Cards | **13** — 12 LIBD DLPFC Visium sections (47,681 spots) and the Xenium colon preview (587,115 cells). Two studies |
| Obs rows | 634,796 |
| Platforms | 2 (Visium, Xenium) · Modalities: 1 (Transcriptomics) · Tissues: 2 · Disease states: 2 |

**The corpus is narrow, and the UI shows it honestly.** Modality has one value; Platform, Tissue,
Disease, and Resolution have two each. Twelve of the thirteen cards are sections of one study, and
they differ only in section id and spot count. The workspace fills out as ingest does; nothing in
it is padded to look busier than the atlas is.

## Field mapping

Everything below is emitted into `corpus_index.json`. Status: **have** = read from the atlas ·
**derive** = computed by the script from atlas columns · **missing** = no source in the atlas.

| UI field | Source | Status |
|---|---|---|
| `id` / `title` | `SpatialDatasetSchema.study_name`, `sample_name` | have |
| `platform` | `obs.technology` enum → display label | derive |
| `modality` | which pointer is populated (`has_gene_expression` / `has_protein_abundance`) | derive — no `modality` column exists in the schema |
| `tissue` | `obs.tissue` (UBERON labels) | have |
| `disease` | `obs.disease`, falling back to `disease_state` for "Healthy" | have |
| `resolution` | platform → tier lookup in the script | derive — not a schema concept |
| `cellCount` / `cellUnit` | obs row count · `obs.spatial_unit` → cells/spots/bins/beads | have |
| `txPerCell` | median `obs.n_counts` | derive |
| `hasImages` | `has_he_crop` / `has_morphology_crop`, plus which kinds | have |
| `downloadable` | `SpatialDatasetSchema.download_url` is non-null (true for all 13) | derive |
| `location` | `data_access_link`, falling back to `download_url` | have |
| `meta.organism` | `obs.organism` | have |
| `meta.donorId` | `DonorSchema.donor_id` via `obs.donor_uid` | have — 3 donors for DLPFC, 1 synthetic for the Xenium colon |
| `meta.panelSize` / `panelName` / `panelVersion` | `PanelSchema.n_targets` / `panel_name` / `version` | have — 425 targets for the Xenium panel; null throughout for Visium (no panel), and `version` is null in the only panel row that exists |
| `meta.sections` | distinct `obs.section_uid` | have |
| `meta.released` | `PublicationSchema.publication_date` | have for DLPFC (2021-02-08); null for the Xenium colon, which is a vendor release with no publication |
| `meta.referenceGenome` | — | **missing** — `GenomicFeatureSchema.ensembl_version` is per-feature, not a dataset's genome |
| `meta.license` | — | **missing** — not in the schema at all |
| `qc[]` | computed, see below | derive |

The two missing fields emit `null` and should render as `—` in the drawer rather than being
invented. License in particular matters for a training corpus and wants a real source eventually.

## QC: what the atlas can actually support

The schema carries **no dataset-level QC**. The QC-adjacent columns are all per-obs (`n_counts`,
`negative_control_counts`, `unassigned_counts`, `segmentation_method`, `passes_qc`). The script
computes what it can and marks the rest `na`:

| Metric | Result |
|---|---|
| Median transcripts/cell | Computed. **Xenium colon = 95 → `fail`** under the packet's ≥ 200 threshold |
| Negative-probe rate | Computed. Xenium colon = **0.010% → `pass`** |
| % transcripts assigned to cells | `na` — not derivable. `unassigned_counts` is per-cell blank/deprecated codewords (3,962 corpus-wide), not off-cell transcripts; that figure lives in the platform's run metrics, which ingest does not read |
| Segmentation quality | `na` — no verdict is stored anywhere, only `segmentation_method` (`nucleus_expansion` / `grid`). The sidebar's "Passes segmentation QC" filter has nothing behind it |
| Signal-to-noise, Spillover | `na` — no proteomics in the atlas. `protein_abundance` and `ProteinSchema` exist but hold zero rows, and `SpatialTechnology` has no CODEX/IMC/MIBI members to ingest one under |

Three judgement calls the script makes, all reversible in one place:

1. **Transcripts/cell is `na` for capture assays.** The packet marks only neg-probe and segmentation
   `na` for Visium, but 3,419 counts per 55 µm spot is not comparable to 95 transcripts per
   segmented cell, and scoring it against ≥ 200 would rate every Visium dataset excellent for the
   wrong reason. The value is still shown, labelled `/ spot`.
2. **The one imaging dataset in the corpus fails the headline metric.** 95 median transcripts is
   ordinary for a 425-plex panel; the ≥ 200 threshold appears to assume a larger panel. As it
   stands the Xenium card — the richest thing in the corpus — carries a red QC strip and the
   landing stat reads "1 / 2 pass all QC". Either the threshold is panel-size-relative or that is
   the intended reading.
3. **`obs.passes_qc` is not wired to anything.** It is the *source's* own filtering verdict and is
   null across both datasets.

Since nothing is stored, the packet's rule that "QC levels are computed at ingest and the UI never
recomputes them" is satisfied in spirit only — the index is computed ahead of time, not at ingest.
If the UI outlives the hackathon, these fields want to move onto `SpatialDatasetSchema` so the
verdict travels with the data instead of with the build script.

## The index

`scripts/build_corpus_index.py` reads the atlas over R2 (default
`s3://epiblast-public/somics_spatial_atlas`, override with `SOMICS_ATLAS_DIR`, credentials reused
from `src/somics/viewer/atlas_source.py`) and writes `data/corpus_index.json`:

```
generatedAt, atlasDir
stats          { datasets, ingestedDatasets, units, sections, platforms, passAllQc }
facets         { modality, platform, tissue, disease, resolution } -> [{ value, count }]
starterPrompts [...]      generated from the corpus, so a click always returns rows
datasets       [ card ]   each with qc[], meta{}, and nested sections[]
```

Facet counts in the file are corpus totals; the UI recomputes them per query with the group's own
filter dropped, which at this size is a loop over two objects rather than a query.

Rerun it after every ingest — the file is a snapshot, and nothing warns you when it goes stale.

## What shipped

`web/` — React 19 + `@czi-sds/components` v24, mounted at `/corpus` by the viewer's FastAPI app
(`/api/corpus` serves the index). Results workspace and landing page both built; see
`web/README.md`. Three decisions made while building, each visible in the UI:

- **"Passes segmentation QC" is rendered disabled**, with the reason on hover, rather than dropped.
  A filter with no data behind it should look unavailable, not absent.
- **The QC toggles that do work are "Passes all QC" and "Low false-detection rate"** — the two
  metrics the atlas can actually support.
- **A transcripts/cell floor excludes spot assays** rather than letting a 2,400-count spot satisfy
  a per-cell threshold.

SDS v24 derives its accent from `colors.indigo` (purple); the packet's blue ramp is SDS's own
`colors.blue`, so `web/src/theme.ts` swaps the ramp rather than restyling components one by one.

## Notes on the packet

- `@czi-sds/components` is at **v24**, peering on `@mui/material ^9` and `react >= 18` — not MUI v5
  as the packet says. Compatible with `viewer/`'s React 19, so one npm workspace works.
- The existing viewer is keyed by **`section_uid`** (`/api/samples/{uid}`), not by dataset. "Open
  viewer" needs a dataset→section mapping; the index nests sections per card, so the DLPFC card can
  offer its 12 sections rather than guessing one.
- Compare / Add to corpus / Export corpus are named but never specified. Export format is a product
  decision (dataset IDs? resolved download URLs? a homeobox query?) and nothing here covers it.
- `skills/query-spatial-atlas` is agent documentation, not a callable parser, and it is obs-level.
  Chat parsing should be a keyword matcher over the facet vocabulary in the index — which covers
  every starter prompt in the packet — with a model call layered on later if wanted.
