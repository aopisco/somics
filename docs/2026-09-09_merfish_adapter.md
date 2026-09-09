# MERFISH / MERSCOPE adapter — sources, layout, decisions

*2026-09-09. Companion to `docs/2026-09-06_xenium_ingest.md`; same pipeline shape.*

## Where the data is, and where it is not

The registry holds 117 MERFISH/MERSCOPE rows and the bucket 13 staged prefixes,
but **none is a MERSCOPE vendor bundle**: what was staged is GEO RAW tarballs,
two MERlin source-code zips, a figure PDF, an `.rds` and an `.h5ad`. Vizgen's
own showcase buckets (`gs://vz-ffpe-showcase`, `gs://vz-liver-showcase`,
`gs://public-datasets-vizgen-merfish`) answer **403 to anonymous callers on
both list and get**; access goes through Vizgen's data-release form and a
Google account, and no `gcloud` is installed here. The DCA staging bucket has
Xenium only.

The open source with real volume is the **Allen Brain Cell Atlas**
(`s3://allen-brain-cell-atlas`, us-west-2, public, also over plain HTTPS):

| release | instrument | sections | cells (approx) | genes | raw h5ad |
|---|---|---|---|---|---|
| `MERFISH-C57BL6J-638850` (Yao 2023) | Vizgen MERSCOPE | 59 | ~4M | 500 (+50 blanks) | 7.6 GB |
| `Zhuang-ABCA-1..4` (Zhang 2023) | Zhuang lab MERFISH | ~150 across 4 animals | ~9M | 1,122 | 1.2 / 0.5 / 0.7 / 0.06 GB |
| `HMBA-MERSCOPE-H22.30.001-BG` (human basal ganglia, 2025) | Vizgen MERSCOPE | tens | — | ~300 | 0.67 GB |
| `HMBA-MERSCOPE-QM23.50.001-BG` (macaque) | Vizgen MERSCOPE | — | — | — | 0.72 GB, **deferred** |

Each release is one AnnData of raw counts (`obs` index = `cell_label`, `var`
index = Ensembl id with `gene_symbol`, `X` float64 holding integers, 50
`Blank-N` rows on 638850) plus `cell_metadata.csv` (section label, donor,
x/y/z in **mm** on the mouse releases; `x_experiment`/`y_experiment` in **um**,
`qc_pass` and `segmentation_job_id = sis_cellpose_v1` on HMBA) and `gene.csv`.
**No per-section imagery is published** — only CCF-resampled volumes — so these
sections are expression-only. The schema allows that (`he_crop` /
`morphology_crop` default null; `x_px`, `y_px`, `pixel_size_um` null).

## The adapter

| script | role |
|---|---|
| `scripts/build_merfish_package.py` | per section: 10x-format `cell_feature_matrix.h5` (CSR arrays written at the transposed shape, exactly what `somics.ingest.read_10x_h5_csr` reads), `<section>_obs.csv`, `cell_feature_matrix_var.csv` in 10x's feature vocabulary (`Gene Expression` / `Blank Codeword`). Two source layouts: `allen_abc` (a release, sections derived from `cell_metadata.csv`) and `merscope_outs` (a Vizgen bundle: `cell_by_gene.csv` + `cell_metadata.csv`, one section) for when a vendor bundle is staged. |
| `scripts/assemble_merfish_collection.py` | registries from the builder's `sample_geometry.json` (sections and the donor come from the release, not the spec); no `sectionimage_registry.csv` unless a section has an image |
| `scripts/harmonize_merfish_package.py` | synthesises the per-section entries the Xenium harmonizer expects and calls `harmonize_xenium_package.harmonize_sample` verbatim |
| `scripts/run_merfish_pipeline.sh` | the Xenium runner without the image library table and, with a single feature space, without the `materialize_bare_obs` bracket |
| `scripts/ingest_tenx_xenium_ec2.sh` | now takes `SOMICS_FAMILY`, `SOMICS_BUILDER`, `SOMICS_RUNNER`, `SOMICS_RAW_INCLUDE`; a spec without `samples` is skip-if-present by the `study.` prefix of its section ids |
| `specs/merfish/*.json` | 638850, Zhuang ABCA-1..4, HMBA human |

Decisions:

- **Cell ids are namespaced** `<section>:<cell_label>`, in obs and in the h5
  barcodes. Allen labels are 19-digit (638850) to 39-digit (Zhuang) integers;
  the CSV staging would type them as ints and mangle the long ones.
- **`section_id` is the release's own section label** (`C57BL6J-638850.37`),
  stable across rebuilds; `section_index` is the sorted position.
- **Blanks are `negative_control_counts`**, flagged `is_control` on the
  feature axis via the Xenium harmonizer's `Blank Codeword` mapping.
- **`segmentation_method`**: `unknown` on the mouse releases (not in the
  release metadata); `cellpose` on HMBA because every cell records
  `sis_cellpose_v1`.
- **Macaque deferred**: gene resolution of macaque Ensembl ids against the
  reference cache is unverified, and a miss falls through to gget's Ensembl
  MySQL on port 5306, which hangs (CLAUDE.md).
- **Verified locally** before any EC2 run: the builder's h5 for section
  `C57BL6J-638850.01` round-trips through `read_10x_h5_csr` identical to the
  Allen per-section h5ad (13,108 cells x 550 features in the test split);
  `n_counts`/`n_genes` match. polycomb's skills are not installed on the
  laptop, so staging/harmonize/finalize are exercised only on EC2.

## Runs

- **Smoke, 2026-09-09 03:48Z** (`somics-merfish-smoke`, `i-0537c5b983eeb8298`):
  638850 alone into a throwaway atlas from the rebuild base, prefix under
  `s3://somics-dev/ingest/merfish/atlas/2026-09-09T03-48-10Z`. **Passed**:
  fetch 207 s, raw staging 31 s, build + ingest 835 s for the whole release;
  59 sections, 3,938,808 cells, 500 genes + 50 blanks, x 0.46–10.6 mm,
  y 1.4–9.3 mm; the repair check read every pointer column of every section
  under a filter (6,411,924 obs rows = 2.47M base + 3.94M MERFISH) with
  nothing to repair, i.e. the expression-only sections with null crop pointers
  are queryable. Prefix deleted afterwards.
- **Production**: `ingest_tenx_xenium_ec2.sh` with the MERFISH exports from
  the newest `_DONE` prefix once Xenium run 5 and the final protein pass land;
  `SOMICS_SPEC_DIRS=specs/merfish`, no `SOMICS_ONLY`. Expect ~13M cells over
  ~250 sections, which roughly doubles the atlas's Xenium-class cell count.

## Registry follow-ups

Six registry rows describe the one Allen 638850 dataset (`yao2023_merfish`,
`yao2023_merscope`, `yao2023_vizgen_mersc`, `aibs_merfish_dat2023_vizgen_mersc`,
`aibs_merfish_dat_merfish`, `merfish_dataset_2023_merfish_2`) and three the
Zhuang data (`zhang2023_whole_mouse_brain_merfish`, `zhang2023_merfish`,
`zhuang_lab_merfi_merfish`); `rhesus_macaque_b2025_merscope` is recorded as
human although the release covers human and macaque. Fold them when the
`in_atlas` column lands; the spec `dataset_key`s above name the rows to keep.

## seqFISH, for when this is done

Assessed 2026-09-08. 43 registry rows; **6 buildable today, all HuBMAP (Cai
lab): 3 small intestine, 3 spleen.** Layout per dataset: one dense
genes x cells CSV per field of view (~50 genes incl. blanks), one
`cell_centroids.csv` (FieldID, CellID, X, Y, Z in **FOV-local pixels**, 0–2048
at 0.112 um/px), a DAPI and a segmentation mask per FOV, raw hyb-cycle stacks
(the bulk). Two thresholds (`data/`, `data/low_threshold/`). 9 of the 18 HuBMAP
rows are the indexed-but-404 datasets; 3 heart datasets have no count
matrices. Of the 3 staged literature prefixes, two hold the wrong artifact
(`eng2019_seqfish` fetched a BMP-pathway spreadsheet from a mismatched GSE;
`xia2019_seqfish` a figure PDF). Stage positions were not found in the first
400 KB of the Micro-Manager OME header; the slide-layout file is under a key
with spaces and was not opened. Without positions, one section per FOV.
~40k cells total: platform coverage, not volume.
