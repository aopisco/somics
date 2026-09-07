# Ingesting the 10x Xenium outs bundles

*2026-09-06 · the block after Visium · companions:
`docs/2026-09-04_tenx_visium_ingest.md` (the pattern this follows) and the
preview specs `specs/xenium_lung_preview.json`, `specs/xenium_colon_preview.json`*

## What this is

43 registry rows point at a 10x-CDN Xenium `outs` bundle: 35 catalogue rows
and 8 literature rows citing the same bundles. `scripts/resolve_tenx_xenium_
files.py` verifies each, and `scripts/make_tenx_xenium_specs.py` writes one
spec per dataset into `specs/tenx_xenium/` in the shape the existing Xenium
builder, assembler and harmonizer read.

| | n |
|---|---:|
| buildable, first pass (RNA) | 39 |
| protein co-detection, second pass | 2 |
| re-release of a sample under an older Onboard Analysis (Breast IDC With Addon) | 1 |
| already in the atlas (the lung preview) | 1 |

514 GB of bundles, 5 to 52 GB each, median 12 GB. Four Onboard Analysis
generations (1.0 to 4.0), 31 human and 4 mouse, and one genetic disease model
(TgCRND8 Alzheimer's mouse) which is a model, not a treatment, so no
`perturbation` block.

## What changed in the builder

The preview sections were built from 1.3 and 1.6 bundles. Across versions:

- **The focus image.** Before 2.0, `morphology_focus.ome.tif` (DAPI). From
  2.0, a `morphology_focus/` directory: one channel for RNA-only runs, four
  with the multimodal segmentation kit (DAPI, ATP1A1/CD45/E-Cadherin boundary
  stain, 18S interior RNA, alphaSMA/Vimentin interior protein), and in 4.0
  protein bundles dozens of named channels. The builder stacks the directory
  once as a tiled `(Y, X, C)` BigTIFF with those names; `image_modality` is
  `dapi` for a single image and `morphology` for a stack, and `channel_names`
  goes on the section-image row.
- **The panel.** The catalogue text names it for 35 datasets, imperfectly in
  places; `gene_panel.json` in the bundle is authoritative and the builder
  uses it when the spec's name is null. `n_targets` comes from
  `experiment.xenium`'s predesigned + custom counts.
- **Cells.** 2.0+ adds a per-cell `segmentation_method` (nucleus expansion
  vs boundary or interior stain). The spec-level value (`cell_boundary_stain`
  when the catalogue lists "Cell Segmentation Staining", `nucleus_expansion`
  otherwise) is the obs column; the per-cell value is kept verbatim in
  `additional_metadata`. `nucleus_area` is null where a version lacks it.
- **Sources.** The EC2 runner fetches the zip, extracts only the six members
  the builder reads (plus the channel directory), stages the zip to
  `s3://somics-dev/raw/<dataset_id>/` with a manifest, and keeps each
  dataset's `sample_geometry.json` under the run prefix's `_geometry/` so the
  verifier can check row counts without the package.

Section ids are the 10x sample names, as in the Visium block. The three
preview sections were ingested under hand-written ids, so the lung preview is
excluded by name; the lung-cancer and colon previews have no CDN-URL registry
row and are not in the 43.

## Three more layouts, found by running it

- **Explorer bundles.** Some catalogue pages link `_xe_outs.zip` rather than
  `_outs.zip`; those carry cells and the count matrix only as
  `cells.zarr.zip` and `cell_feature_matrix.zarr.zip` (no parquet, no h5).
  Four of the 39 failed extraction on this in the first run. The builder now
  rewrites the zarr pair into the parquet + h5 it reads -- the shifted-hex cell
  id encoding (`(27196, 1)` -> `aaaagkdm-1`), `cell_summary`'s columns, control
  counts per feature type from the matrix, the h5 in 10x's CSC layout, and the
  zarr-only `aggregate_gene` feature dropped. On the prostate bundle, which
  ships both forms, every column and matrix value reproduces exactly.
- **HuBMAP's 20 small-intestine sections** (Stanford TMC, Onboard Analysis 3.3,
  5K panel, ~380-450k cells each, ~7.5M in all) keep the bundle's parquet, h5
  and experiment file under `lab_processed/xenium_bundle/`, `gene_panel.json`
  under `raw/`, and the 14-plane DAPI z-stack instead of a focus image. The
  builder max-projects the stack slab by slab; specs come from
  `scripts/make_hubmap_xenium_specs.py` (real HuBMAP donor ids with age, sex
  and race, block ids, FFPE, the multi-tissue segmentation stain) with S3 URIs
  the runner copies directly; `SOMICS_SPEC_DIRS=specs/hubmap_xenium` selects
  them. One dataset smoke-tested end to end: 445,593 cells, clean repair check.
- **Atera** (`specs/atera/`) is the Explorer layout plus a whole-transcriptome
  "panel" of 18,028 targets and a development software version; `technology`
  is `atera` (enum member added) so preproduction data can be filtered out.

## Running it

```bash
#!/bin/bash
export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/<newest prefix with _DONE>
export SOMICS_BRANCH=protein-adapters
curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/ingest_tenx_xenium_ec2.sh | bash
```

as user-data on an m5n.4xlarge with 1.5 TB; smallest bundle first (the 4.7 GB
pancreas preview) so a regression fails early. Serial with the other blocks:
it stacks on the newest atlas prefix and the repair step runs at the end.
Verify with `scripts/verify_visium_ingest.py --specs 'specs/tenx_xenium/*.json'`
against the output prefix; the row-count check uses the kept geometry.

## Run history

- **Run 1** (`2026-09-06T20-30-28Z`, on the protein-trial atlas): 23 of 39 in,
  16 skipped -- 10 Explorer bundles (zarr-only cells/matrix), 4 Onboard
  Analysis 1.0 cell tables without the unassigned-codeword column, 2 null panel
  names. All three causes were fixed while it ran; the repair check at its end
  found nothing to repair. The block's ingests took 60-190 s each after fetch.
- **Run 2** (`somics-tenx-xenium-2`, launched 2026-09-07 00:25Z from run 1's
  atlas) covers the 16 skipped plus the 20 HuBMAP sections and Atera in one
  pass: `SOMICS_SPEC_DIRS="specs/tenx_xenium specs/hubmap_xenium specs/atera"`,
  skipping sections already present.

## Not verified yet

The generalised builder was unit-tested on synthetic channel directories and
the panel lookup; it has not run on a real 2.0+ bundle. The first EC2 run is
the test, and a 3.0 bundle should be in its first three.
