# seqFISH adapter (HuBMAP, Cai lab) -- one section per field of view

*2026-09-10. Assessment in `docs/2026-09-09_merfish_adapter.md` ("seqFISH").*

**Scope.** Six HuBMAP seqFISH datasets are buildable, all from donor W105
(HBM876.BRLJ.659, 62-year-old white female, Caltech TMC): small intestine
duodenum (HBM782, 5 FOVs), ileum (HBM443, 5), jejunum (HBM543, 3); spleen
central (HBM354, 10), posterior (HBM359, 10), anterior (HBM954, 10). 43 FOV
sections, ~50 genes each, on the order of 40k cells in total. The other twelve
HuBMAP seqFISH rows are indexed-but-404 (9) or ship no count matrices (3 heart).

**Why one section per FOV.** Cell centroids are FOV-local pixels (0-2048 at
0.112 um/px, from the MMStack OME header). The `.pos` files are Micro-Manager
position lists covering several tissues (15-16 positions, labels `Pos0..Pos21`)
whose labels do not map onto the dataset's `pos0..posN` numbering, and the
MMStack headers carry no per-plane stage position. Placing FOVs on a slide
would be a guess; a FOV is an honest section with its own DAPI image, which is
also the tile-grid shape planned for HuBMAP imagery.

**Layout used.** `data/count_matrix_pos<N>.csv` (genes x cells, `cell_<id>`
columns, `blank` rows -> `blank_1..k`, `is_control`), `data/cell_centroids.csv`
(FieldID, CellID, regionID, X, Y, Z, Volume; Z and Volume kept in
`additional_metadata`), `segmentation_mask/dapi-hyb0-pos<N>.tif` (DAPI z-stack,
max-projected to the section image). Not used: `data/low_threshold/` (the
pipeline's second threshold), the segmentation masks and multicut `.h5`, the
raw `HybCycle_*` stacks (nearly all the bytes), the `Slide Explorer` overview.
Gene ids are symbols; `ensembl_gene_id` stays null (harmonizer rule, 2026-09-10).
`segmentation_method` is `other` (multicut on DAPI; no schema member).

**Scripts.** `scripts/make_seqfish_specs.py` (registry rows + S3 listing +
portal `metadata.json` -> `specs/seqfish/*.json`, samples = FOVs),
`scripts/build_seqfish_package.py` (per FOV: 10x-format h5 via the MERFISH
builder's writer, obs with `x_px`/`y_px` in the DAPI frame, `anatomical_region`
from the dataset description), then the MERFISH assembler, harmonizer and runner
(`SOMICS_BUILD_SCRIPT=scripts/build_seqfish_package.py`; the runner takes the
expression + image bracket because the assembler writes a section-image
registry). Verified locally on HBM782 fov0: 838 cells, 47 genes + 4 blanks,
matrix total equals the source total, registries carry the donor's age/sex/race,
block id and region.

**The DAPI stacks are malformed ImageJ files.** One valid IFD describes the
first plane; the other planes follow as contiguous raw pixels and the next-IFD
pointer is bogus, so tifffile yields (0, 0) pages after the first and its ImageJ
series parser raises "incompatible keyframe" (seqFISH runs 1 and 2 skipped all
six datasets on exactly these two errors). The builder reads the planes
directly: from the first plane's data offset, as many whole planes as the file
holds (11 on HBM782 fov0, two of them empty), **in the file's byte order** --
ImageJ writes big-endian, and the native-order read produced means of ~33,000
on a stack whose maximum is ~12,000. Validated on the real stack:
`docs/figures/seqfish_hbm782_fov0_dapi_centroids.png` is the max projection
with the 838 centroids of the field overlaid; every one sits on a nucleus.

**Duplicated gene rows.** The small-bowel count matrices repeat the `EEF2`
row verbatim (identical counts); polycomb's keyed merge on `feature_id` needs
unique keys, so the builder drops verbatim-duplicated gene rows (a repeated
name with different counts would be kept, suffixed `__2`) and keeps every
blank barcode row. Run 3 skipped all six datasets on exactly this.

**The runner must find the image registry in the package root.** The MERFISH
assembler's `coalesce(copy=False)` moves the registries out of staging, so a
check on `$STAGING/sectionimage_registry.csv` finds nothing, the runner skips
the `materialize_bare_obs` bracket, finalization leaves the obs table as
`SpatialObs_gene_expression`, and ingest dies with "no finalized obs table
'SpatialObs'" -- a fatal that aborts the run (run 5; the base prefix was
untouched). The runner now checks `$ROOT/` as well.

**Launch** (after the Liu 2022 block; from the newest `_DONE` prefix):

```bash
export SOMICS_FAMILY=seqfish SOMICS_BUILDER=scripts/build_seqfish_package.py
export SOMICS_RUNNER=scripts/run_merfish_pipeline.sh SOMICS_BUILD_SCRIPT=scripts/build_seqfish_package.py
export SOMICS_SPEC_DIRS="specs/seqfish" SOMICS_RAW_INCLUDE="*"
```

## Result (run 6, 2026-09-11 22:09Z -> 2026-09-12 ~03:30Z, `ingest/seqfish/atlas/2026-09-11T22-08-59Z`)

All six datasets in, 43 FOV sections, 31,531 cells; atlas at 73,263,158 obs
rows, repair check clean. Each build + ingest took 65-87 s.
  - `hubmap_hbm354_vtth_229_unspecified_spleen_na`: 10 FOVs, 7472 cells, 46 genes + 1 blank(s)
  - `hubmap_hbm359_csfk_287_unspecified_spleen_na`: 10 FOVs, 9554 cells, 46 genes + 1 blank(s)
  - `hubmap_hbm443_fhtz_898_unspecified_small_intesti`: 5 FOVs, 516 cells, 46 genes + 4 blank(s)
  - `hubmap_hbm543_kqkj_535_unspecified_small_intesti`: 3 FOVs, 2327 cells, 46 genes + 4 blank(s)
  - `hubmap_hbm782_dnvv_354_unspecified_small_intesti`: 5 FOVs, 3589 cells, 46 genes + 4 blank(s)
  - `hubmap_hbm954_nqwf_729_unspecified_spleen_na`: 10 FOVs, 8073 cells, 46 genes + 1 blank(s)
The spleen matrices carry one blank row where the small-bowel ones carry four;
both are as published.
