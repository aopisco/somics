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

**Launch** (after the Liu 2022 block; from the newest `_DONE` prefix):

```bash
export SOMICS_FAMILY=seqfish SOMICS_BUILDER=scripts/build_seqfish_package.py
export SOMICS_RUNNER=scripts/run_merfish_pipeline.sh SOMICS_BUILD_SCRIPT=scripts/build_seqfish_package.py
export SOMICS_SPEC_DIRS="specs/seqfish" SOMICS_RAW_INCLUDE="*"
```
