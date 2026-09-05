# The SPRM adapter: HuBMAP CODEX and PhenoCycler into the atlas

*2026-09-05 · the protein-imaging block after Visium · companion to
`docs/2026-08-26_ingestion_pipeline.md` (the operating manual) and
`docs/2026-08-30_full_atlas_build_plan.md` (Phase 2, item 1)*

## What this is

HuBMAP runs every CODEX and PhenoCycler submission through one pipeline --
Cytokit (or, for the PhenoCycler submissions, DeepCell) stitches and segments,
then SPRM measures each cell -- and publishes the result as a processed
dataset with a fixed layout. That makes the 131 staged datasets one *source
layout*, and one spec-driven builder serves all of them. This is the same
economics as the 10x Visium block: the adapter is the cost, each dataset is a
spec.

**128 of the 131 staged CODEX/PhenoCycler datasets qualify** (121 CODEX, 7
PhenoCycler; 125 single-region, two with four regions, one with two). Three do
not, each with its reason in `data/sprm_datasets.csv`: two older CODEX
deposits with no OME-TIFF pyramid (`data.json` + `output/` layout), and one
whose region has an image but no SPRM tables.

Nothing is ingested yet. The builder was run locally end to end on one CODEX
dataset (below); the polycomb steps and the ingest need the EC2 box with the
reference cache, and ingestion into the shared atlas must wait for the Visium
run to finish, since two boxes cannot write one Lance atlas.

## The layout

For `HBM626.KXRZ.238` (spleen, University of Florida TMC), 110 files, 12.9 GB:

```
anndata-zarr/reg1_stitched_expressions-anndata.zarr.zip      33 MB   SPRM per-cell AnnData (77,756 x 11)
sprm_outputs/*-cell_channel_total.csv                          5 MB   summed intensity per cell per channel
sprm_outputs/*-cell_channel_mean.csv                          14 MB   mean intensity (range ~0-13)
sprm_outputs/*-cell_centers.csv                                1 MB   cell centroids -- columns are (row, col)
sprm_outputs/*-cell_shape.csv, -cell_polygons_spatial.csv   150+ MB  shape descriptors, outlines
ometiff-pyramids/stitched/expressions/*.ome.tif             2.97 GB  (C, Y, X) uint16, 11 x 9493 x 12656, 377 nm/px
ometiff-pyramids/stitched/mask/*.ome.tif                      52 MB   cells / nuclei / boundaries masks
experiment.yaml                                                       Cytokit acquisition record
metadata.json                                                         the portal record: uuid, title, donor, samples
```

PhenoCycler datasets (`[DeepCell + SPRM]`) put the pyramid under
`ometiff-pyramids/pipeline_output/expr/`, carry no `experiment.yaml`, and are
uint8 with up to 54 channels (`HBM293.RMCL.695`: 19042 x 21450 x 54, 22 GB,
507 nm/px). SPRM's outputs are identical in form.

## Decisions

- **The protein matrix is `cell_x_antigen_total`, rounded to uint32.** The
  atlas's `protein_abundance` space admits one `counts` layer of uint32
  (`homeobox.builtins.PROTEIN_ABUNDANCE_SPEC`). SPRM's means have a dynamic
  range of about 0-13 -- the spleen dataset's mean is 0.49 -- so rounding them
  would erase the signal. The totals are sums of pixel values in the hundreds
  to tens of thousands; on uint16 images they are already integers (0 of
  855,316 values rounded on the spleen), on uint8 PhenoCycler images SPRM
  reports fractional totals and the rounding loses at most 0.5 on a value that
  large. The fraction rounded is recorded per region in `sample_geometry.json`.
  Means are recoverable as total / area.
- **Coordinates are the AnnData `xy` where it exists, else `cell_centers.csv`
  with its columns swapped.** `cell_centers.csv` is named `x, y` but holds
  `(row, col)`: against the AnnData centroid the two agree to under 4 px only
  after the swap, and only the swap keeps every cell inside the image. When
  both are present the builder checks them against each other and refuses the
  sample if they disagree by more than 8 px.
- **The full expression stack is kept, rewritten channels-last.** The image
  is the pipeline's *extract* (the antigen channels the submitter chose), not
  the raw per-cycle stack the Monkman package had to render down to three
  channels. The atlas loader boxes the leading spatial axes and reads channels
  in full, so `(C, Y, X)` is streamed slab by slab into a tiled `(Y, X, C)`
  TIFF and `channel_names` on the section image names each plane. A 3 GB
  CODEX stack takes about two minutes; a 22 GB PhenoCycler stack is bounded to
  one slab in memory.
- **Segmentation method follows HuBMAP's `dataset_type`.** `[Cytokit + SPRM]`
  is U-Net nuclei plus marker-controlled membrane watershed, recorded as
  `watershed`. `[DeepCell + SPRM]` is Mesmer, for which the enum has no member,
  recorded as `other` with the method in the audit trail. Both hand SPRM the
  same mask.
- **`n_counts`, `n_genes`, `cell_area_um2` stay null.** An imaging proteomics
  assay counts no transcripts, and SPRM's `cell_shape.csv` is a shape
  descriptor rather than an area (derivable later as total / mean).
- **Donor from the portal title, package-local id.** The processed datasets'
  metadata export carries no donor fields (0 of 274 CODEX/PhenoCycler rows in
  the TSV), and the staged `metadata.json` donor entity has no
  `mapped_metadata`. HuBMAP generates every title in one form -- "CODEX
  [Cytokit + SPRM] data from the spleen of a 14-year-old black or african
  american female" -- so age, race and sex are read with a strict pattern and a
  non-matching title leaves them null (127 of 128 parse; "unknown" race is
  null). `life_stage` follows the LIBD spec's bins; `human_development_stage`
  is the HsapDv "<n>-year-old stage" label. The HuBMAP donor id is in the
  description; donor rows are package-local (`<HBM-ID>_donor`) as every other
  package's are, so datasets from one donor do not yet share a donor row.
- **`disease_state` is `unknown`, `preservation` is `unknown`.** Neither is
  published with the processed dataset. HuBMAP donors are organ donors and
  surgical cases, not asserted healthy.
- **Protein identity resolves only through the table the Monkman package
  verified with `resolve_proteins`**, matched case-insensitively (`CD11C` finds
  `CD11c`). Every other target keeps its channel name as `protein_key` with
  `uniprot_id` null and the reason in the audit trail. HuBMAP panels reach 55
  targets and differ per dataset; typing accessions from memory would violate
  the blank-beats-guessed rule, so the long tail is a reference-cache job on
  the EC2 box.
- **No perturbation block.** Applied per dataset: these are untreated donor
  tissues from tissue mapping centers.

## The pipeline

```
scripts/make_sprm_specs.py            registry + S3 listing + metadata.json -> specs/sprm/*.json, data/sprm_datasets.csv
scripts/build_sprm_package.py         one spec -> obs / var / uint32 matrix / (Y, X, C) image per region
scripts/assemble_sprm_collection.py   donor, section, panel, image registries + collection.json
scripts/harmonize_sprm_package.py     schema alignment, protein identity, join keys, channel_names
scripts/run_sprm_pipeline.sh          the six steps, any SPRM spec (single-obs shape, four library tables)
```

Single-obs shape with the `materialize_bare_obs` bracket, like Monkman and
Visium; four library tables because HuBMAP publishes a donor and the extract is
a targeted panel.

## What was tested

Locally, on `HBM626.KXRZ.238`, with the sources copied from S3:

| check | result |
|---|---|
| obs rows | 77,756, the AnnData's cell set exactly, in its order |
| `x_px`, `y_px` | equal to the AnnData `xy` centroids |
| matrix | equal to `cell_x_antigen_total` for every cell and channel, reordered to image channel order; 0 values rounded |
| var | 11 channels in OME order; `DAPI-02` flagged control, 10 targets |
| pixel size | 0.37745 um from the OME-XML; matches `experiment.yaml` |
| package image | `(Y, X, C)` `(9493, 12656, 11)` uint16, 1024-px tiles, 2.45 GB |
| pixels | interior and border blocks equal the source with channels moved last |
| tissue where the cells are | DAPI at the ten brightest-DAPI centroids 7912 vs 3193 at random windows |

The polycomb steps (staging, resolution pass, harmonization, finalization) and
the ingest were **not** run: they need the skills and the 84 GB reference
cache, which live on the EC2 box.

## Running it

Per dataset, on a box set up as `scripts/ingest_tenx_visium_ec2.sh` does
(skills, reference cache, `SOMICS_SCHEMA`, `SOMICS_ATLAS`):

```bash
SPEC=specs/sprm/<dataset_id>.json bash scripts/run_sprm_pipeline.sh
```

The builder fetches from `s3://somics-dev/hubmap/<HBM-ID>/` itself, so the box
needs read access there. An unattended runner over all 128 specs should follow
the Visium script's shape: smallest first (`n_files` and image size are in the
report and specs), sync the atlas after every dataset, treat a refused
duplicate section as a skip, and stop on a failure inside ingest. Two of the
128 are 22 GB images; put them last.

## What to check after ingest

- Obs rows per section against `n_cells` in each package's
  `sample_geometry.json`.
- The image: one crop grid per platform, and the tissue-vs-random check the
  Visium verifier does, on a DAPI channel.
- How many targets resolved to UniProt (expect the Monkman set only), and run
  `resolve_proteins` over the rest.
- Donor rows: 128 package-local donors for what is fewer real people; decide
  whether the registry should merge on the HuBMAP donor id.

## Suggested CLAUDE.md rows (not applied here)

```
| `scripts/make_sprm_specs.py` | staged HuBMAP CODEX/PhenoCycler rows -> one spec per dataset in `specs/sprm/`, verdicts in `data/sprm_datasets.csv` |
| `scripts/build_sprm_package.py` | one SPRM spec -> obs, var, uint32 totals matrix, (Y, X, C) expression image |
| `scripts/run_sprm_pipeline.sh` | build + ingest any SPRM spec (assembler and harmonizer alongside) |
```
