# The MIBI adapter: HuBMAP's maternal-fetal interface cohort

*2026-09-05 · a spec-driven builder for HuBMAP MIBI lab submissions · companion
to `docs/2026-08-26_ingestion_pipeline.md` (the operating manual) and the SPRM
adapter doc for CODEX/PhenoCycler*

## What this covers

HuBMAP holds 429 MIBI datasets and all 429 are staged under
`s3://somics-dev/hubmap/<HBM-ID>/`. They are **three layouts**, and only one is
a per-cell protein dataset a builder can read on its own:

| layout | n | tissue | GB | what it is | here |
|---|---:|---|---:|---|---|
| lab submission | 211 | uterus | 122 | 47-channel ion-count stack + cell mask + antibody table + cohort cell table | **built** |
| `MIBI [DeepCell + SPRM]` | 172 | uterus | 145 | HuBMAP's re-processing of a lab submission (`direct_ancestors` names it) in the SPRM layout | skipped: the SPRM adapter's domain, and the same section |
| lab-processed image | 46 | bone marrow | 42 | one 1.6 GB OME-TIFF per dataset, a channel list, no mask, no cell table | skipped: image only |

The 211 are one cohort (Stanford RTI, Greenbaum, Angelo *et al.*, "MIBI at the
Maternal-Fetal Interface"): decidua from **66 donors** aged 15.7–39.6, all
female, FFPE, one 800 x 800 um field of view per dataset at 391 nm/px, and one
37-antibody panel shared by all 211 (the spec generator hashes each dataset's
antibody set into the panel name and every dataset hashes the same).

`data/mibi_datasets.csv` is the accounting: every staged MIBI row with its
layout, bytes, its SPRM derivative where one exists (172 of 211), and a skip
reason where it is not built.

**The SPRM derivatives are the same sections.** Whichever adapter ingests a
uterus field, the other must leave it, or the atlas holds the same tissue twice
under two segmentations. The spec records `source.sprm_derivative` for that
decision. Recommendation: ingest the lab submissions from here (the
submission's own segmentation, all 211) and have the SPRM adapter skip MIBI.

## The pipeline

```
scripts/make_mibi_specs.py           registry + bucket listing + portal records -> specs/mibi/*.json, data/mibi_datasets.csv
scripts/build_mibi_package.py        one spec -> obs / var / counts matrix / (Y, X, C) stack, sample_geometry.json
scripts/assemble_mibi_collection.py  four registries + collection.json
scripts/harmonize_mibi_package.py    schema alignment, protein identities, join keys, channel names
scripts/run_mibi_pipeline.sh         the six steps for one spec (single-obs shape, like Monkman)
```

Single-obs shape: two feature spaces, `protein_abundance` and `discrete_image`,
one obs table, so `finalize_collection` is bracketed by `materialize_bare_obs`
and `reconcile_barcodes` is not run. Four library tables (donor, section,
panel, section image); the resolution pass runs on the first three.

## What the builder computes, and why it computes rather than reads

A lab-submission dataset ships `SingleCellData/cells.csv`, but it is the
**cohort's** table: 495,349 cells over 211 fields of view (`Point` column,
e.g. `10_31742_1_2`), with normalised marker values (0–3.1, per-marker
scaled), a `lineage` label, and a per-field `label`. Nothing in a dataset says
which `Point` it is: not the portal record (donor, sample, organ, protocol; no
lab sample id), not the assay metadata TSV (`roi_id: 1` for all), not the
mask. Matching the mask's label set against the table finds 136 candidate
fields for HBM526.FFGJ.297, so the join is not recoverable per dataset. The
per-cell values are therefore **computed from the dataset's own mask and
stack**, which is exact and self-contained; the cohort table is recorded in
the spec (`source.cohort_cells_table`) and not used. Consequence:
`cell_type_original` is null. If the cohort's field-to-dataset mapping is ever
obtained (the authors would know it), the lineage labels can be joined by
`Point` + `label` in a later pass.

- **An obs row is one label of `Mapping/cluster_labels_image.tif`.** Despite
  the name it is the cell segmentation mask: 2,900 labels for HBM526 with a
  median area of 403 px (≈62 um²), not a 20-class cluster image. Centroid and
  area come from the label's pixels; `x_um`/`y_um` from the pixel size in the
  assay metadata TSV (`pixel_size_x_value: 391 nm`), cross-checked against
  field width / image width (800 um / 2048 px = 390.6 nm) with a 2 % tolerance.
- **The protein readout is the summed ion count per channel per cell.** MIBI
  pixels are pulse counts (`signal_type: pulse count`, int16, 0–111 here), so
  the sum over a cell is an integer and enters the uint32 `counts` layer
  without rounding, unlike the QuPath means Monkman had to round. Mean per
  pixel is `counts / area_px` (area is in `additional_metadata`) and is left
  to the reader. `n_counts` stays null: it means transcripts.
- **All 47 stack channels are features.** 37 antibody channels carry the
  UniProt accession and RRID from `extras/antibodies.tsv` verbatim (37/37 have
  an accession), so `uniprot_id` needs no resolver and `protein_key` is
  `Homo sapiens:<accession>`. The ten others (Au, Ca, Co, Fe, Ir, Na, Sc, Si,
  Ta, background) are the elemental and background channels and are flagged
  `is_control`, as Monkman keeps its blank cycles; `protein_key` falls back to
  the channel name for them. `gene_name` is left null for a resolver (the
  source does not state it; the accession is enough identity).
- **The image is the whole stack**, rewritten from the OME-TIFF's `(C, Y, X)`
  to the `(Y, X, C)` the atlas's slab loader boxes, int16 → uint16 (no value
  is negative), zlib-tiled: 21–30 MB per dataset from a 394 MB source, ~6 GB
  for the block. `channel_names` are the OME channel names in stored order,
  written in harmonization as a list.
- **`image_modality` is `morphology`.** A multiplexed antibody stack acquired
  by secondary-ion mass spectrometry is not fluorescence, so
  `immunofluorescence` would misstate the physics, and `other` has no crop
  pointer to route to in `somics.ingest`. `morphology` is the enum member for
  a multichannel tissue stack (the Xenium DAPI/boundary stack uses it) and
  routes to `morphology_crop`.
- **`segmentation_method` is null**, not `other`: the submission ships the
  mask and does not say how it was drawn. HuBMAP's own DeepCell segmentation
  belongs to the derivative datasets, which are a different mask.

## What the spec generator decided

- Donor: HuBMAP donor id as `donor_id` (66 donors across 211 datasets share
  rows in the atlas because the uid is a content hash of that id); age, sex
  and race from the portal's `living_donor_data`. `human_development_stage`
  is `"<N>-year-old stage"` as the LIBD spec writes it; `life_stage` bins at
  18 / 40 / 65 (64 young adults, 2 juveniles). Race `Unknown` is null.
- Section: the dataset's HuBMAP id; `block_id` the HuBMAP sample id;
  `tissue` from the portal organ code (`UT` → uterus; the ROI description,
  "decidua", is kept in `additional_metadata`); `preservation: ffpe` because
  the dataset description says archival FFPE tissue.
- `disease_state: unknown`, `disease` null. Nothing in the record states a
  diagnosis; early pregnancy is not a disease and is not asserted as health
  either. The DCA perturbation judgment was applied: observational donor
  tissue, no treatment, so no `perturbation` block.
- `assay: "MIBI"`, the source's own `assay_type`, for the EFO resolver.

## Running it

Locally, the builder and assembler run end to end (polycomb's Collection API
is on PyPI); staging, harmonization, finalization and ingest need polycomb's
skills and the reference cache, i.e. an EC2 box set up as
`scripts/rebuild_atlas_ec2.sh` does. Per spec:

```bash
SPEC=specs/mibi/<dataset>.json SOMICS_ATLAS=<atlas> scripts/run_mibi_pipeline.sh
```

The builder fetches the six source files from `s3://somics-dev/hubmap/` when
absent (`--list-sources` prints URI and destination pairs for a pre-fetcher).
A block run is the Visium EC2 script's loop with this runner substituted;
sources are already staged, so no `raw/` copy is made. Order does not matter
since `ensure_registry_tables`, but the 211 share one donor table across 66
donors and one panel, so any first dataset exercises every join.

## Test evidence

Built locally on two datasets and checked against the source files by an
independent recomputation (`/tmp`-only script, not committed):

| dataset | cells | channels / antibodies | image | checks |
|---|---:|---|---|---|
| HBM526.FFGJ.297 | 2,900 | 47 / 37 | (2048, 2048, 47) uint16, 21 MB | 12 random cells: centroid, area and all 47 summed counts equal a naive per-label recomputation; channels 0, 13, 46 of the written image equal the source planes; 37/37 accessions |
| HBM539.PKZN.553 | 3,071 | 47 / 37 | (2048, 2048, 47) uint16, 30 MB | same, all pass |

The assembler ran on HBM526 and produced `collection.json` with seven dataset
files and five shared files; registries as expected (one donor, one section,
one panel, one image). Not run locally: staging into Lance, the resolution
passes, harmonization, finalization and ingest.

## What to check when it lands

- Section count equals the number of specs run, and 66 donor rows (not 211).
- `panel_uid` non-null on every obs row and one panel row.
- `channel_names` on the image row has 47 entries starting `Au, CD11c, CD14`.
- A `morphology_crop` at a high-count cell: the CD45 or H3 plane should be
  bright where the mask says a cell is; random windows should be dimmer.
- The SPRM adapter's list excludes the 172 derivatives of these sections.

## Suggested CLAUDE.md rows (not applied here)

```
| `scripts/make_mibi_specs.py` | HuBMAP MIBI rows → layout classification, `specs/mibi/`, `data/mibi_datasets.csv` |
| `scripts/build_mibi_package.py` | one MIBI lab submission → per-cell ion counts from mask + stack, (Y, X, C) image |
| `scripts/run_mibi_pipeline.sh` | build + ingest any MIBI spec (single-obs shape) |
```

And a gotcha: **HuBMAP MIBI `cells.csv` is the cohort's table, not the
dataset's.** 495k cells over 211 fields, and nothing in a dataset names its
field; per-cell values must be computed from the mask and the stack. The
`cluster_labels_image.tif` is the segmentation mask despite its name.
