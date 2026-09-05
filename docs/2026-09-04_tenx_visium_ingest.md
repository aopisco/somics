# Ingesting the 10x catalogue's Visium and Visium HD datasets

*2026-09-04 · the first block of new data after the rebuild · companion to
`docs/2026-08-30_full_atlas_build_plan.md` (Phase 1) and
`docs/2026-08-26_ingestion_pipeline.md` (the operating manual)*

## What this is

The registry holds 111 datasets harvested from 10x's own catalogue on the
Visium and Visium HD platforms (62 + 49). None was staged in `raw/`; the
registry recorded one verified bundle per row, and it was the wrong one for a
builder — the MEX tarball for Visium, `binned_outputs.tar.gz` for HD. This
pass resolves the files a builder actually needs, generates one spec per
dataset, extends the Visium builder to read every Space Ranger layout 10x has
published, and runs the lot unattended on EC2 into the atlas the 2026-09-02
rebuild verified.

**78 datasets are buildable (41 Visium, 37 HD), ~680 GB of source.** 33 are
not, each with a recorded reason in `data/tenx_visium_files.csv`:

| reason | n |
|---|---:|
| re-release of a sample already carried by a newer Space Ranger row (see below) | 17 |
| no verified bundle URL (Targeted-Compare per-artifact releases, pages rendering no CDN links) | 5 |
| several tissues on one capture area (tissue microarrays, cerebellum + brain rows) | 6 |
| Space Ranger `aggr` output, several sections in one matrix | 2 |
| plant tissue (Arabidopsis, soybean); donor/tissue schema is animal-oriented | 2 |
| two species on one capture area (xenograft) | 1 |
| no full-resolution image on the CDN (Alzheimer's AppNote) | 1 |

**The 17 re-releases are registry duplicates.** 10x reprocesses the same
sample under newer Space Ranger versions and lists each as a dataset; the
catalogue harvest took them at face value, so 16 samples have two or three
rows (`V1_Adult_Mouse_Brain` at 1.0.0 and 2.1.0, `V1_Mouse_Brain_Sagittal_
Anterior_Section_2` at 1.0.0, 1.1.0 and 2.0.0, six Visium HD samples at 3.x
and 4.0.1, ...). They are one section each, and the atlas's stable
`section_uid` refused the second copy in the first run — the guard doing
exactly its job. Policy: the newest release is ingested; the older rows are
skipped here and should be folded into the registry as re-releases of one
dataset rather than kept as datasets in their own right. That is a
`datasets.csv` edit for a later pass (the one-dataset-per-row rule cuts both
ways).

None of the remaining 16 is blocked on code that does not exist; they need a per-row
decision (split by tissue, pick an organism convention for xenografts, a
per-library split for `aggr`) and are left for a human.

## The pipeline

```
scripts/resolve_tenx_visium_files.py     registry rows -> data/tenx_visium_files.csv
scripts/make_tenx_visium_specs.py        files.csv + catalogue -> specs/tenx_visium/*.json
scripts/build_visium_package.py          one spec -> obs/var/image per sample   (extended)
scripts/assemble_visium_collection.py    registries + collection.json          (extended)
scripts/harmonize_visium_package.py      schema alignment                      (extended)
scripts/run_visium_pipeline.sh           the six steps, any Visium/HD spec
scripts/ingest_tenx_visium_ec2.sh        user-data: fetch, stage raw/, build, ingest, sync
```

`run_libd_dlpfc_pipeline.sh` now delegates to `run_visium_pipeline.sh`; the
LIBD spec is unchanged and the new builder reproduces its obs, var and image
byte-for-byte (checked on 151507 against the pre-change script).

### What the resolver decided, and why

10x names the files beside the recorded bundle under suffixes that changed
across releases. Each candidate is HEAD-probed, and only what exists is written.

- Counts: `_filtered_feature_bc_matrix.h5` (Visium). HD carries the counts
  per bin size inside `_binned_outputs.tar.gz`.
- Spatial: `_spatial.tar.gz` — positions and scale factors.
- **Image: `_tissue_image.btf|.tif|.tiff` if present, else `_image.tif`, else
  `_image.jpg`.** On CytAssist runs `_image.tif` is the instrument's own
  low-resolution capture and is *not* the frame the spot coordinates are
  written in; the microscope image is. Getting this wrong places every crop
  on the wrong pixels with no error, so the builder refuses a sample whose
  coordinates fall outside the image it was given. Three 1.3.0 FFPE releases
  publish the full-resolution image only as a JPEG; the builder converts it
  to a tiled TIFF because the atlas's image loader streams TIFF slabs.
- **The frame check is the hires image, not the spot extent.** Space Ranger's
  `tissue_hires_image.png` is the given image scaled by `tissue_hires_scalef`,
  so hires size / scale factor is the frame's size to a few pixels, and an
  image of another size is refused. Spots *can* lie outside the true frame:
  CytAssist detects tissue on its own full-capture-area image while the
  microscope scan may cover only part of it (two of the first 25 datasets, one
  with spots 23% past the scan's right edge). Every obs row must be placeable
  in the image, so the builder pads the image with background (white for
  brightfield, black for fluorescence) to the spot extent, records the original
  size as `padded_from_hw`, and says so on the section-image description. The
  padded copy is a derived file and is not staged to `raw/`.

### What the spec generator decided

- **HD is ingested at the 8 um bin.** The tarball carries 2, 8 and 16 um; 8 um
  is what 10x's own analyses and Loupe default to, is roughly one cell across,
  and keeps an 11 mm capture area under ~2M obs rows. `spatial_unit` is `bin`,
  `unit_size_um` 8, `technology` `visium_hd`; `assay` stays the EFO Visium label
  because EFO does not separate the instruments and our controlled column does.
- Donor sex, age and development stage are null: 10x does not publish them.
  The donor is a package-local key, as in the Xenium preview specs. Life stage
  is `embryonic` for the embryo sections and `unknown` otherwise.
- `disease_state` is `healthy` for 10x's own healthy / non-diseased / normal
  labels, `diseased` with the source's disease text otherwise, `unknown` where
  neither the registry nor the catalogue says. The text goes to the MONDO
  resolution pass verbatim; some of it ("inflamed") is a state rather than a
  diagnosis and may not resolve. That is recorded, not guessed around.
- Image modality is H&E unless 10x's methods section (`Image type:`), the
  slug, or a `Stains:` clause in the title says immunofluorescence: 13 of 95.
  The `Stains:` clause is the only signal on the 1.2.0 Targeted / Whole
  Transcriptome releases, and it also supplies `channel_names` verbatim
  (e.g. DAPI, Anti-SNAP25, Anti-GFAP, Anti-Myelin CNPase). The first run
  launched with seven of these labelled H&E; their images are channels-first
  stacks, so the builder's frame check rejected them rather than ingesting
  them under the wrong label, and they are in that run's `_failed.txt` for the
  follow-up pass below.
- No dataset in this block is perturbational (the CLAUDE.md rule was applied
  per dataset: these are 10x's untreated demonstration tissues), so no
  `perturbation` block is written. The one candidate, the Alzheimer's
  app-note mouse brain, is a genetic model rather than a treatment and is
  skipped for lack of an image anyway.

### What the builder absorbs

Positions in `tissue_positions_list.csv`/`.txt` (1.x, headerless),
`tissue_positions.csv` (2.x, header) or `tissue_positions.parquet` (HD).
Pixel size from `microns_per_pixel` where the scale factors carry it (HD,
3.x), derived as 55 um / `spot_diameter_fullres` where they do not — the same
relation the published LIBD sections use — and cross-checked where both exist.
HD extraction streams the ~14 GB tarball once and writes only the requested
bin's h5 and `spatial/`. A channels-first fluorescence TIFF (one page per
stain, which tifffile reads as `(I, Y, X)`) is rewritten once as a tiled
`(Y, X, C)` TIFF, because the atlas's image loader boxes the leading spatial
axes and reads channels in full; the original is what is staged to `raw/`.

## Running it

```bash
aws ec2 run-instances \
  --profile sci-data-dev-poweruser --region us-east-1 \
  --image-id ami-0332d564d76dbd8d6 --instance-type m5n.4xlarge --count 1 \
  --security-group-ids sg-0e81dbfc34d71253c --subnet-id subnet-0fce42712a109e498 \
  --iam-instance-profile Name=somics-raw-staging \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":1500,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --instance-initiated-shutdown-behavior terminate \
  --user-data file://scripts/ingest_tenx_visium_ec2.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=somics-tenx-visium-1}]'
```

The script clones the branch named in its `BRANCH` default; point it at
`main` once this work is merged. It starts from
`s3://somics-dev/rebuild/atlas/2026-09-02T00-43-52Z/` and writes to
`s3://somics-dev/ingest/tenx_visium/atlas/<stamp>/`, syncing after **every**
dataset, with `_done.txt` (per-dataset timings), `_failed.txt`, `_order.txt`,
`_ingest.log` and per-dataset `_logs/` beside it. `_DONE` or `_FAILED` marks
the end. Source files are staged to `s3://somics-dev/raw/<dataset_id>/` with a
`_manifest.json` as they are fetched, so the corpus grows whether or not the
ingest of a given dataset succeeds.

**Run history.** The first launch (`2026-09-04T23-44-58Z`) ingested ten
datasets in twelve minutes, skipped the two spinal cord stacks (fluorescence,
fixed above), then stopped on the eleventh: the re-release of a section it had
just ingested, which the ingest guard refused before writing. Its prefix
carries `_FAILED`. The second launch (`2026-09-05T00-18-10Z`) started again
from the verified rebuild with the re-releases skipped up front, ingested 36
of 78 and skipped 20 -- every skip one of two causes fixed on the branch
while it ran (fluorescence stacks failing on the harmonizer's library-db path,
and CytAssist scans that do not cover every spot). It was stopped by hand at
dataset 57 (marker `_STOPPED`), between syncs, because each HD skip was a
wasted 14 GB fetch. The third launch runs the fixed code from that atlas and
processes only what is not already in it.

Order is smallest-first with a healthy human Visium as the first dataset —
the normal prostate, the same one the builder was smoke-tested on locally — so
a pipeline regression fails in the first half hour, not after the fetch. A
failure before `== 6. ingest ==` skips that dataset and continues; a failure
inside ingest stops the run, because a half-ingested dataset is not resumable.

Judge the run by its artifacts: `_done.txt` growing, the atlas prefix
growing, the log's mtime advancing. Tail it over SSM if in doubt:

```bash
aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript \
  --parameters 'commands=["tail -30 /var/log/ingest.log; cat /mnt/work/done.txt"]' \
  --profile sci-data-dev-poweruser --region us-east-1
```

## Follow-up pass

Datasets a run skipped are listed in its `_failed.txt` with the step that
failed. After fixing the cause, run only those on top of that run's atlas,
with a wrapper as user-data:

```bash
#!/bin/bash
export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/tenx_visium/atlas/<first run stamp>
export SOMICS_ONLY="tenx_a tenx_b"
export SOMICS_BRANCH=main
curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/ingest_tenx_visium_ec2.sh | bash
```

`somics.ingest` refuses a section the atlas already holds, so listing an
already-ingested dataset fails loudly rather than doubling its rows.

## What to check when it lands

- Row counts per section against `n_spots` in each package's
  `sample_geometry.json` (the ingest log prints both).
- One H&E crop per platform, by eye, against the coordinates: the CytAssist
  frame decision above is the kind of thing only a picture confirms.
- The `disease` column on the diseased sections: how many of the source
  strings the MONDO pass resolved, and which it left null.
- `data/datasets.csv`: set `data_downloadable` and, for the 95, note the
  ingest; the 16 skips go to a review list.
