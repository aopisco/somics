# Literature Xenium from GEO and Zenodo (2026-10-01)

Ingests the Xenium datasets the literature harvest found that were not yet in
the atlas. 31 candidates (13 staged, 18 with a fetchable URL) were surveyed
file by file; 18 specs / 115 sections came out of it.

## What is specced (`specs/geo_xenium/`, generator `scripts/make_geo_xenium_specs.py`)

| spec | source | sections | layout | notes |
|---|---|---|---|---|
| bilous2026_xenium | GSE311609 | 6 | 4 focus channels | lung, Prime 5K |
| bilous2026_xenium_io_custom | GSE311609 | 5 | focus | lung, custom 340-gene IO panel |
| bilous2026_xenium_lung_panel | GSE311609 | 11 | focus | lung, human lung panel |
| bilous2026_xenium_breast | GSE311609 | 19 | focus | breast panel; new registry row |
| human_kidney_iga2024_xenium | GSE264334 | 1 | mtx + z-stack | IgA nephropathy biopsy |
| in_house_xenium_2026_xenium | GSE342202 | 4 | z-stack | mouse brain, fresh frozen |
| normal_bone_marr2026_xenium | GSE322974 | 1 | z-stack | normal bone marrow clot, 5K + add-on |
| xenium2026_xenium | GSE342289 | 4 | z-stack (24-33 GB each) | neurotypical amygdala |
| xenium_breast_ca_xenium | GSE286964 | 2 | mtx + z-stack | **skin**, not breast (registry fixed) |
| xenium_spatial_t2026_xenium_2 (+ _319g) | GSE335552 | 3 + 1 | z-stack | CRLM + adjacent normal; two panels |
| xenium_tcr_seq_d_xenium | GSE300147 | 18 | z-stack | HNSCC + 1 ameloblastoma; per-sample tissue (site) |
| yip2025_xenium (+ _multitissue) | GSE299193 | 21 + 1 | cells.csv + z-stack | myeloma spectrum; 5K and multi-tissue |
| yu2026_xenium | GSE326226 | 5 | cells.csv + z-stack | MIBC; donor per region (GEO does not map regions to patients) |
| zhang2026_xenium | GSE313006 | 4 | mtx + z-stack | COPD TMAs; cores not split per participant, donor sex `mixed` |
| spatial_transcri2025_xenium | GSE297945 | 8 | outs tarball | infant lung; the 8 samples with per-sample outs |
| human_gastric_ca2025_xenium | Zenodo 15164980 (staged) | 1 | outs zip | gastric cancer, XOA 1.7.1 |

A series that mixes gene panels is split into one spec per panel (a package
carries one PanelSchema row).

## Builder changes (`build_xenium_package.py`)

GEO deposits are per-sample files, so specs list them under `files` like the
HuBMAP specs, as https URLs. The builder now:

- keeps `.gz` on fetched files and decompresses in place (`materialize_deposit`);
- extracts only the needed members from an `outs.tar.gz` (streamed);
- converts `cells.csv.gz` to `cells.parquet`, and a Matrix Market directory to a
  10x-layout `cell_feature_matrix.h5` (same barcode order, so the existing
  barcode-vs-cells check still guards alignment);
- treats `experiment.xenium` as optional: pixel size from the spec or the
  morphology image's OME `PhysicalSizeX`; run metadata recorded as not deposited;
- names a panel `Xenium panel of N genes (<GSE>)` when neither spec nor
  `gene_panel.json` names one.

The assembler and harmonizer accept a per-sample `tissue` (GSE300147 spans four
resection sites).

Verified locally before EC2: IgAN (mtx + z-stack, no experiment.xenium; h5 equals
the mtx exactly, gene-column sums equal n_counts on 500 cells, centroids inside
the image), bilous L1 5K (four gzipped focus channels), infant lung PDL006
(outs tarball). GEO's FTP resets long transfers; the runner's `curl -C -`
retry handles it, a plain download does not.

## Not specced, and why

- **Perturbed designs, need a decision and a `perturbation` block:** GSE269719
  (mouse kidney ischemia-reperfusion injury vs sham, 75.5 GB staged), GSE277936
  (Egln1 endothelial knockout vs littermates; only an rds staged, outs zips on
  GEO), GSE341169 (little skate embryos incl. fin ablation; also an unsupported
  species), Zenodo 19639395 (Del(1.5 Mb) mouse; processed h5ad only), GSE284271
  (Pax9 knockout; Visium HD only, no Xenium).
- **Need new work:** vannan2023 (GSE250346, 176 GB staged): several samples
  per slide bundle, so sections must be split by cell lists; GSE308148 (tumor
  TMA): transcripts only, counts would have to be re-aggregated; GSE297945's two
  slide-level bundles cover the other 26 cores and need core splitting.
- **Private on GEO:** GSE341852 (public after 2026-10-31), GSE319943, GSE328654.
- **Organoid:** Zenodo 20889605 (kidney organoid, normoxia arm) -- whether
  organoids belong in a tissue atlas is undecided.
- **Duplicates / not Xenium:** hbc_xenium (staged file is 10x pancreas, already
  in), janesick2023_xenium(_3), fu2024 (code), dumoulin2026 (snRNA-seq), the
  10x Atera cervical preview (no outs bundle). Notes added to the registry rows.

## Running it

Same EC2 script as the other Xenium runs:

```bash
export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/stereoseq/atlas/2026-09-22T19-03-01Z
export SOMICS_SPEC_DIRS="specs/geo_xenium" SOMICS_RAW_INCLUDE="*"
```

Raw staging keeps the fetched GEO files under `raw/<dataset_key>/` with a
manifest. The atlas is 1.1 TiB, so launch with a 3 TB volume.

## The production run, and the repair fix (2026-10-02)

The first production run (`ingest/geo_xenium/atlas/2026-10-01T20-35-26Z`)
ingested 16 of 18 specs, then failed its end-of-run repair: the whole-table
rewrite in `repair_atlas.py` left obs failing filtered reads at 112.8M rows, as
it had for the Stereo-seq follow-up (the smoke run's 3M-row atlas repaired
fine). Reproduced on a local copy of that obs table: 6 of 108 fragments fail
(five `he_crop`, one `morphology_crop`; Lance 10.0.0, file format 2.1). The
same rows written fragment by fragment read fine, so the defect comes from the
one-shot write of the whole table, not the data.

Fix: `rewrite_streaming` rewrites obs from an unfiltered scan, one batch at a
time, each rebuilt into contiguous buffers (Arrow IPC round trip). On the copy:
112.8M rows in 30 s, 0 failing fragments, 40 random sections clean, and every
column equal row by row to the source version (enum columns compared decoded;
their dictionaries are re-encoded). The check now reads every fragment plus 60
sampled sections (7 min) instead of every section twice (~10 h of the 10.7 h
the failed repair took); `--full-section-check` keeps the old pass.

The two failures in that run are fixed too: a 30 GB Zenodo zip unzip could not
read (zipfile fallback) and mouse donors missing `human_development_stage`.
The follow-up builds on the failed prefix with `SOMICS_REPAIR_BASE=1` (repair
the base first, then ingest what is missing, then repair).
