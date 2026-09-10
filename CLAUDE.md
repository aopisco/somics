# somics — working notes

State, decisions and hard-won gotchas for this repo. Written to be picked up
cold. Numbers are as of 2026-08-25 (state as of 2026-09-04) and move as jobs finish — re-run
`scripts/bucket_inventory.py` rather than trusting them.

## What this project is

Three things, in increasing order of how finished they are:

1. **A dataset registry** — `data/datasets.csv`, 5959 rows, one per dataset,
   keyed to the publication that **first released** the data. Built from a
   paperclip literature sweep plus the HuBMAP portal export.
2. **A raw corpus in S3** — `s3://somics-dev`, ~4 TB and growing, the actual
   source bundles.
3. **An ingested atlas + two UIs** — 59 datasets in Lance/zarr, browsable in a
   3D viewer and a corpus builder. This is the hackathon output and the only
   part that is queryable today.

Origin: a weekend hackathon (2026-08-15) by @aopisco and @conradry. Public
repo `aopisco/somics`; a private `chanzuckerberg/somics` exists but **nothing
has been ported to it yet** — see its issue #1 for the plan.

## The tables

| file | grain | rows |
|---|---|---|
| `data/literature_datasets.csv` | claim-level: one row per (dataset × source paper) | 2,708 |
| `data/datasets.csv` | curated: one row per dataset, keyed to its original publication | 5959 |
| `data/model_dataset_usage.csv` | many-to-many: which paper/model uses which dataset | 3,526 |
| `data/dissociated_reference_datasets.csv` | rows removed from the registry as non-spatial | 182 |
| `data/st_corpus.csv` | TERRA supplementary table, maintained by hand, **not** produced by this pipeline | 455 |
| `data/tenx_visium_files.csv` | per 10x Visium/HD row: the CDN files a builder needs, HEAD-verified, or a `skip_reason` | 111 |
| `data/tenx_rereleased_rows.csv` | registry rows folded away as Space Ranger re-releases of a sample another row carries (`folded_into`) | 18 |
| `data/tenx_visium_rows_needing_review.csv` | 10x Visium/HD rows the spec-driven builder cannot take, with the reason | 16 |
| `data/sprm_datasets.csv` | per staged HuBMAP CODEX/PhenoCycler row: SPRM layout verdict, regions, or why not buildable | 131 |
| `data/mibi_datasets.csv` | per staged HuBMAP MIBI row: which of three layouts, buildable or skip reason | 429 |
| `data/hubmap_truncated_files_2026-09-07.tsv` | the 108 staged HuBMAP files found short against the files index (44 datasets), re-staged 2026-09-07 | 108 |

Key columns on `datasets.csv`: `is_spatial` (yes/no/unknown), `modality`
(spatial transcriptomics / proteomics / epigenomics), `data_access_link`
(landing page), `download_url` (fetchable file), `data_downloadable`
(verification verdict), `candidate_accessions` (where a paper cited 2-3 and the
mapping is unresolved), `first_published_by_model_paper`.

**Rules that shape the registry**
- A dataset's reference is the paper that first released it. If data debuted in
  a model paper (TERRA's in-house Xenium pancreas), that model paper *is* the
  original reference.
- HuBMAP `dataset_id` is `hubmap_<HBM-ID>_<technology>_<tissue>_<analyte>`. The
  HuBMAP ID alone is unique; the rest is for legibility.
- **Visium and Visium HD are separate platforms here, deliberately.** 10x's own
  dataset facet labels both "Visium", but they are different instruments at
  different resolution — 55 um spots against 2 um bins — and pooling them
  overstates what the corpus can support. `scripts/harvest_10x_catalog.py` sets
  the platform from the dataset title rather than the facet for exactly this
  reason. Do not "fix" it back.

  They do **share an ingestion schema**, though: same feature spaces
  (`gene_expression` + `discrete_image`), same obs shape, and a bin is a
  `spatial_unit` exactly as a spot is. So one builder serves both — what differs
  is the source layout (`binned_outputs.tar.gz` at several bin sizes, against a
  spatial directory) and `unit_size_um`. Separate platforms in the registry, one
  builder in the pipeline; those are different questions and the answer is
  different for each.
- Blank beats guessed. A wrong accession or modality is worse than an empty
  cell, and several columns are deliberately sparse for that reason.
- **One sample per row, too.** 10x's catalogue lists every Space Ranger
  reprocessing of a sample as a new dataset; 16 Visium/HD samples had two or
  three rows until `scripts/fold_tenx_rereleases.py` kept the newest release
  and moved the rest to `data/tenx_rereleased_rows.csv` (2026-09-05). The
  atlas's stable `section_uid` is what caught it. Check the CDN sample name,
  not the landing page, when a new 10x harvest lands.
- **One technology per row.** A row is one measurement of one tissue on one
  platform. A row naming several is several datasets — split it, duplicating
  every other field and the `model_dataset_usage.csv` entries.
  `scripts/split_multiplatform_rows.py` does this and only splits when *every*
  part is a platform the registry already uses alone; 34 rows became 76 and
  **64 are left for a human in `data/platform_rows_needing_review.csv`**.
  Punctuation is not a reliable signal: `LC-MS/MS` is one technique,
  `Xenium 5K + custom panel` is a platform and a qualifier,
  `VisiumHD / 10X Genomics` is a platform and its vendor.
- **Never overwrite `platform` without recording the original** in `notes`:
  `platform recorded by the source as '<original>'; normalised for grouping`.
  `classify_spatial_modality.py` used to overwrite and only *print* the change,
  which lost nine rows' original wording to disk (seven backfilled from
  `b5da607^`). It matters most where a spelling names a different instrument —
  `VisiumHD` and `Visium` differ by one letter and are 2 um bins against 55 um
  spots. `scripts/normalize_platform_strings.py` folds vendor and generic noise
  (324 rows, "visium" strings 102 -> 36) and keeps HD, CytAssist, v1/v2 and
  "(no probes)" distinct.

## The bucket

```
s3://somics-dev/            (us-east-1, account 440744247602)
  somics_spatial_atlas/     24 GB   — the ingested atlas, mirrored from the hackathon R2 bucket
  raw/                      2.52 TB — literature-derived bundles, one prefix per dataset_id
  raw/_candidates/<acc>/    100 GB  — all accessions for datasets citing 2-3, since the mapping is unknown
  hubmap/<HBM-ID>/          17.5 TB — whole HuBMAP datasets, source layout preserved
  hubmap/_metadata/         the two portal exports this was built from:
                            the datasets metadata TSV (3,945 rows x 173 cols)
                            and the Globus download manifest
  hubmap/_staging_run.log   first pass; _staging_retry.log the second
```

Only prefixes with a `_manifest.json` are actually staged; the manifest records
source URL, bytes, md5 and fetch time.

**Still to do:** organise the bucket — it currently preserves HuBMAP's own
layout, which mixes raw and processed within each dataset.

**What is staged** (`scripts/staged_summary.py`, 2026-08-25): 20.19 TB over
2,408 prefixes. 2,310 join to a registry row (19.54 TB); the 97 that do not are
61 dissociated-reference prefixes whose rows moved to
`dissociated_reference_datasets.csv` (0.61 TB), 15 HuBMAP IDs staged from the
download manifest but absent from the portal TSV, and two log files. Largest
technologies: Histology/H&E 6.65 TB, CODEX/PhenoCycler 4.59 TB, Cell DIVE
2.92 TB, Autofluorescence 1.58 TB, Xenium 1.19 TB.

## Where things stand

- **465 literature datasets staged**, 2.52 TB, zero unexplained failures.
- **HuBMAP Tier 2 complete**: 1,891 datasets, **17.67 TB**, 92,179 files.
  Two passes; the retry recovered 40 datasets. **1,774 complete, 117 short by
  6,232 files (0.96 TB)** and those are *permanent* 404s, not transient — see
  below. 175 of the 2,066 tier-2 datasets have no files indexed at all.
- **The unattended atlas rebuild landed and verified 2026-09-02** — see "Where
  to pick up" below. Ingestion of new data is unblocked.
- **Since 2026-09-05 the atlas has grown from 59 to ~500 sections** (10x
  Visium/HD, 10x + HuBMAP Xenium, Atera, HuBMAP MIBI and SPRM); the lineage
  of prefixes, what is running, and exactly what to launch next are under
  "Where to pick up". The newest `ingest/*/atlas/<stamp>/` prefix with a
  `_DONE` marker is always the current atlas.
- 556 registry datasets have an access link but nothing fetchable; the clusters
  are CNGB, GSA-Human, HuBMAP portal links, and GitHub repos without releases.

## Running an ingest

**`docs/2026-08-26_ingestion_pipeline.md` is the operating manual.** Read it
before running an ingest or writing a builder. The four prerequisites, in the
order they bite:

1. **Python >= 3.12** (homeobox 0.2.9 requires it; 0.2.8 did not).
2. **Install polycomb's skills** — they are not on PyPI:
   `curl -sSL https://raw.githubusercontent.com/epiblastai/homeobox/refs/heads/main/packages/polycomb/install.sh | bash`
3. **Install the reference cache** from `s3://somics-dev/polycomb/reference_db`
   (84 GB) and `polycomb setup --db-path <path>`. Without it `resolve_genes`
   falls through to gget, which opens MySQL to Ensembl on **port 5306** — an
   egress our security group does not allow — and hangs in SYN-SENT with no
   timeout. That cost 3.5 hours before it was diagnosed.
4. **`imagecodecs`** is a real dependency: some Xenium morphology TIFFs are
   JPEG2000, and it varies *within* a dataset family.

Two pipeline shapes, decided by how many obs tables staging produces. A
single-obs dataset brackets `finalize_collection` with
`materialize_bare_obs` (a somics bridge with no polycomb equivalent). A
multimodal one adds `reconcile_barcodes` and then **just calls
`finalize_collection`** — it already joins the per-space obs tables and stamps
uids back onto them. Do not wrap it in `join_feature_space_obs` / `assign_uids`
/ `stamp_uid_on_feature_space_obs`; those are for debugging, and running them
alongside the orchestrator breaks it.

**Never ingest a package twice.** A rebuilt package carries fresh
`dataset_uid`s, so `skip_existing` never fires, while `section_uid` is a stable
hash — so the two copies merge and every obs row doubles. The section count does
not change, so nothing looks wrong. `somics.ingest` now refuses on an overlapping
section and needs `--allow-existing-sections` to proceed. Build the atlas in one
pass; if a package changes, rebuild the atlas.

**A crashed ingest is not resumable.** `skip_existing` checks the dataset uid,
not whether the dataset is complete, so the next run skips it and then fails
looking for its zarr group. Wipe the atlas and re-ingest.

**Image ingestion follows the DCA spec**
(`chanzuckerberg/dynamic-cell-atlas-specs-private`, v0.2; gap analysis and
division of labor in `docs/2026-09-02_dca_spec_alignment.md`). One rule from
that spec that is easy to get wrong by pattern-matching: the `perturbation`
block is conditionally required, and whether a dataset has perturbations is a
**judgment about the experiment, not a machine-checkable property** — the spec
says so explicitly. Every dataset in the atlas so far happens to be
unperturbed, but spatial datasets with CRISPRi guides, drug treatments, or
other perturbational designs exist and must carry a filled
`PerturbationAssignment`. **Check each dataset for perturbational treatment
before omitting the block; never omit it because previous datasets did.**

## The rebuild: done, and it found a defect in the original

**58 of 59 sections reproduce exactly; the 59th differs because the published
atlas is wrong.** Full write-up in `docs/2026-08-27_atlas_rebuild_results.md`.

The published `hColon_Cancer_Add_on_FFPE` has a **misaligned gene axis** — right
counts, wrong genes. Against the source h5 over 40 cells: published 1041/1833
nonzero values correct, rebuilt 1833/1833. Every other family agrees with its
source perfectly on both sides. Per-cell totals, sorted vectors, row counts and
uids all match, so nothing but a gene-by-gene comparison against the source
finds it. **Do not treat the published colon section as authoritative.**

The rebuilt atlas is 59 sections / ~2.47M obs rows on
`schema/spatial_omics_atlas_schema.yaml`, built in one pass on EC2. Its only obs
difference from the published atlas is `has_chromatin_accessibility`, a presence
flag the extended schema introduces.

**Rebuild it with:** the five runners below in any order into a fresh atlas, then
`scripts/verify_rebuild_matches_atlas.py --rebuilt <path>`. "Any order" is only
true since `ensure_registry_tables` in `somics.ingest` — before it, whichever
package ingested first set the registry tables' column types (see gotcha below),
and this exact claim cost attempt 6 its final step.

```
scripts/run_xenium_pipeline.sh          SPEC=specs/xenium_lung_preview.json
scripts/run_xenium_pipeline.sh          SPEC=specs/xenium_colon_preview.json
scripts/run_cosmx_nsclc_pipeline.sh
scripts/run_monkman_codex_pipeline.sh
scripts/run_libd_dlpfc_pipeline.sh      (= run_visium_pipeline.sh with specs/libd_dlpfc.json)
```

Visium HD uses the same runner and builder as Visium: `spatial_unit` is `bin`,
`unit_size_um` is the bin edge, and the builder pulls one bin size out of
`binned_outputs.tar.gz`. **HD is ingested at 8 um** by decision (10x's own
default; ~1 cell; keeps an 11 mm area under ~2M rows); `hd_bin_um` in the spec
is where that lives.

## Rebuilding the atlas is the correctness gate

Do not ingest 20 TB before reproducing the 59 sections we have — the published
atlas is the only ground truth, and a pipeline regression in new data is
indistinguishable from a quirk of the new data.

- **Stable uids reproduce; obs uids do not.** `make_stable_uid("hColon_Cancer_
  Add_on_FFPE")` is the published `section_uid`, so sections, donors, panels and
  features are comparable exactly. `uid` on obs and `dataset_uid` are `uuid4` —
  join on `source_obs_id` instead, and never compare them.
- **All 59 sections now have a spec-driven builder.** The 12 LIBD Visium and 1
  Xenium colon sections never had one — confirmed across all 478 blobs in the
  object store — because `create-data-package` is a *skill*: an agent drives the
  Collection API and the artifact is the package, not a script. Ryan's estimate
  is $10-20 and 30-60 min of agent time per dataset, which is why per-family
  builders only pay for homogeneous vendor bundles.
- **10x's CDN gives us ~0.3 MB/s** regardless of user agent, against 16 MB/s
  from S3. Fetch vendor bundles on EC2 into `s3://somics-dev/rebuild/` and pull
  from there. Version paths differ per dataset: lung preview is `1.3.0`, colon
  is `1.6.0`, and guessing one for both returns 403.
- **Six members of an outs bundle are enough** — selective extraction turns
  18.42 GB into 1.3 GB. `transcripts.parquet` is the bulk and is unused.

## Where to pick up

**Everything is on the `protein-adapters` branch** (PR #22, stacked on
`tenx-visium-ingest`, PR #21). `main` stops at the DCA brief. Every EC2
script clones the branch by name; point them at `main` once both PRs merge.

```bash
git checkout protein-adapters && git pull
```

### The atlas lineage (each run stacks on the previous prefix)

| step | prefix under `s3://somics-dev/` | adds |
|---|---|---|
| verified rebuild (base) | `rebuild/atlas/2026-09-02T00-43-52Z` | 59 sections, 2.4M rows |
| Visium block, 5 runs | `ingest/tenx_visium/atlas/2026-09-05T19-14-47Z` | +78 (20.3M HD bins, 219k spots) |
| obs-table repair | `ingest/repair/atlas/2026-09-06T17-46-36Z` | same rows, readable under a filter, snapshot v83 |
| protein trial | `ingest/protein/atlas/2026-09-06T19-06-13Z` | +1 MIBI |
| Xenium run 1 | `ingest/tenx_xenium/atlas/2026-09-06T20-30-28Z` | +23 |
| Xenium run 2 | `ingest/tenx_xenium/atlas/2026-09-07T00-25-11Z` | +30 (18 HuBMAP Xenium, Atera, Explorer bundles) |
| Xenium run 3 | `ingest/tenx_xenium/atlas/2026-09-07T08-13-13Z` | +0 (7 skips, all fixed since) |
| protein block (finished 2026-09-08 05:30Z) | `ingest/protein/atlas/2026-09-07T12-58-44Z` | +320 (MIBI 205, SPRM 115); 18 skipped, all causes fixed |
| protein follow-up (finished 2026-09-08 12:30Z) | `ingest/protein/atlas/2026-09-08T05-32-55Z` | +17 of the 18 skips (the 44-plane one needs a final pass) |
| Xenium run 4 (finished 2026-09-08 13:41Z; 56.4M obs rows) | `ingest/tenx_xenium/atlas/2026-09-08T12-32-24Z` | the 9: 3 OA-1.0 bundles, 2 mip-image bundles, 2 re-staged HuBMAP; **the 2 protein co-detection skipped** (`ProteinSchema.is_control` missing; builder fixed 2026-09-08) -> run 5 from run 4's prefix |
| Xenium run 5 (finished 2026-09-09 05:20Z; 57.59M obs rows) | `ingest/tenx_xenium/atlas/2026-09-08T21-28-41Z` | +2 protein co-detection (first Xenium sections with a protein feature space) |
| final protein pass (finished 2026-09-09 ~10:00Z) | `ingest/protein/atlas/2026-09-09T05-31-42Z` | +1 (hbm393, the 44-plane SPRM dataset); every protein skip resolved |
| MERFISH production (finished 2026-09-10 03:00Z; 67.50M obs rows) | `ingest/merfish/atlas/2026-09-09T13-32-48Z` | +5 releases (638850 + Zhuang ABCA-1..4, ~9.8M cells, ~210 expression-only sections); HMBA human skipped on null gene symbols (builder fixed) |
| MERFISH follow-up (finished 2026-09-10 ~11:30Z; **72.94M obs rows**) | `ingest/merfish/atlas/2026-09-10T03-01-26Z` | +1 (HMBA human basal ganglia, 95 sections / 5.43M cells, 299 genes); 638850 re-fetched and refused as duplicate (skip check fixed since) |
| verification runs 1-5 (2026-09-10) | `ingest/merfish/atlas/2026-09-10T03-01-26Z/_verify/<stamp>/`; **`docs/2026-09-10_verification_results.md`** | Visium 1403/1416 (12 sections have ~7k rows at negative px: crops slid to the edge; builder fixed, sections wait for the next rebuild); all 62 cell-unit sections pass row sums once gene columns only are summed; Atera 18/18; run 5 = 200-cell registration check on Xenium |
| **Liu 2022 MERFISH block (relaunched 2026-09-10 13:05Z, `somics-merfish-liu-2`, `i-015628b70f93f6504`; the first box was terminated before it wrote anything)** | `ingest/merfish/atlas/2026-09-10T13-*` | the 2 segmented runs (kidney 111921, liver JH 09-18-2021) as cells; the 12 transcript-only runs are excluded |

**The newest `ingest/*/atlas/<stamp>/` prefix with a `_DONE` marker is the
current atlas.** Every prefix carries `_done.txt`, `_failed.txt` (dataset,
step), `_logs/<dataset>.log` for failures, `_geometry/<dataset>.json` (Xenium
runs; the builder's counts, for the verifier), `_repair.txt` (the end-of-run
pointer-read check) and `_order.txt`/`_provenance.txt`. Judge a run by these,
never by the instance.

### Exactly what to launch next, in order (each waits for the previous `_DONE`)

Wrapper user-data pattern (all three ingest scripts take it):

```bash
#!/bin/bash
export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/<family>/atlas/<newest stamp with _DONE>
export SOMICS_BRANCH=protein-adapters
# optional: SOMICS_ONLY="key1 key2"; SOMICS_SPEC_DIRS="specs/tenx_xenium specs/hubmap_xenium specs/atera"
curl -sL https://raw.githubusercontent.com/aopisco/somics/protein-adapters/scripts/<script>.sh | bash
```

`run-instances`: `ami-0332d564d76dbd8d6`, `m5n.4xlarge`, 1500 GB gp3,
`sg-0e81dbfc34d71253c`, `subnet-0fce42712a109e498`, profile
`somics-raw-staging`, `--instance-initiated-shutdown-behavior terminate`
(the exact call is in `docs/2026-08-30_full_atlas_build_plan.md`). GitHub raw
caches ~5 min; after a push, either wait or embed the script in the user-data
(`sed '1d' scripts/x.sh` appended after the exports).

1. **Protein follow-up** -- LAUNCHED 2026-09-08 05:32Z (`i-0750ad358511d3558`)
   from the protein block's prefix, no `SOMICS_ONLY`: skip-if-present makes it
   process only the block's 18 skips. One of them
   (`hubmap_hbm393_tmdx_795`, the 44-plane image) failed again: SPRM names the
   unnamed plane "Channel:0:43"; the builder now adopts that name (fixed
   2026-09-08). **It needs one more protein pass** -- run `ingest_protein_ec2.sh`
   again from the newest prefix; skip-if-present leaves only it. Their causes are all fixed on the branch: truncated staged
   files (108 re-staged byte-exact 2026-09-07, list in
   `data/hubmap_truncated_files_2026-09-07.tsv`), the 8 px centroid tolerance
   (now records up to 40 px), an OME header naming 43 of 44 planes, an
   all-digit dataset uid (assemblers redraw).
2. **Xenium run 4** -- LAUNCHED 2026-09-08 12:32Z from the follow-up's prefix;
   when it lands, the **final protein pass** (step 1's leftover) goes from run
   4's prefix, then verification. Original notes:
   `ingest_tenx_xenium_ec2.sh` from the follow-up's prefix,
   `SOMICS_SPEC_DIRS="specs/tenx_xenium specs/hubmap_xenium specs/atera"`,
   `SOMICS_ONLY` = the 9 in `/tmp/somics_smoke/xenium_run4_userdata.sh` on the
   laptop, or simply no ONLY (skip-if-present drops everything already in):
   3 Onboard-Analysis-1.0 bundles (integer cell ids, now namespaced), 2
   Explorer bundles with `morphology_mip.ome.tif`, the 2 protein co-detection
   datasets (second feature space, first use on Xenium), the 2 re-staged
   HuBMAP Xenium sections.
3. **Verification** -- `verify_atlas_ec2.sh` on the final prefix with
   `SOMICS_SPECS="specs/tenx_visium/*.json specs/tenx_xenium/*.json specs/hubmap_xenium/*.json specs/atera/*.json"`
   (r5 not needed; the verifier reads per section). Report lands under
   `<prefix>/_verify/<stamp>/`. The laptop cannot run it: the exported SSO
   token expires after an hour and lance's S3 store ignores profiles.
4. Then `docs/2026-09-07_next_plan.md`: merge the PRs and collapse the three
   EC2 scripts into one; `in_atlas` column in the registry; publish to R2 and
   rebuild the UI index (`sync_atlas_to_r2.sh`, `build_corpus_index.py`);
   HuBMAP imagery (1,165 image-only datasets) as tile-grid sections; the
   literature tail behind #18; seqFISH/Cell DIVE/MALDI decisions; file the
   Lance compaction bug upstream.

### MERFISH block (2026-09-09) -- smoke running, then production

Vizgen's showcase buckets are gated (403 anonymous; needs their data-release
form + a Google account), so the MERFISH family is the **Allen Brain Cell
Atlas** public releases: 638850 (MERSCOPE, 59 sections, ~4M cells, 500 genes),
Zhuang ABCA-1..4 (~9M cells, 1,122 genes), HMBA human basal ganglia MERSCOPE.
No per-section imagery exists, so these are the atlas's first expression-only
sections. Everything is in `docs/2026-09-09_merfish_adapter.md`. The EC2
script is the Xenium one with a family switch:

```bash
export SOMICS_FAMILY=merfish SOMICS_BUILDER=scripts/build_merfish_package.py
export SOMICS_RUNNER=scripts/run_merfish_pipeline.sh SOMICS_SPEC_DIRS="specs/merfish" SOMICS_RAW_INCLUDE="*"
```

Smoke (638850 alone, throwaway atlas from the rebuild base) **passed
2026-09-09 04:30Z**: 59 sections / 3.94M cells in 835 s of build+ingest, repair
check clean on the expression-only sections (prefix deleted). **Run production
from the newest `_DONE` prefix after Xenium run 5 and the final protein pass**
(serial rule): the six specs, ~13M cells, no `SOMICS_ONLY`. Macaque (QM23.50.001) is deliberately unspecced -- macaque gene
resolution is unverified against the reference cache and a miss hangs on
gget. **Liu et al. 2022 (LSA; @aopisco's own MERFISH kidney/liver/pancreas)**
is a third MERFISH layout, `liu2022_figshare`: figshare zips per Vizgen run.
**Only the two runs with Vizgen cell outputs go in, as cells**; the twelve
transcript-only runs (decoded barcodes, no cell assignment) are excluded by
the author's decision (2026-09-10; a grid-binned version was built and tested,
then dropped -- the builder now refuses such runs).
figshare's `ndownloader` needs curl's own UA (gotcha below). **seqFISH is
prepped** (`docs/2026-09-10_seqfish_adapter.md`): 6 HuBMAP datasets, 43 FOV
sections, one section per field of view because stage positions are not
recoverable; specs in `specs/seqfish/`, builder verified locally. Launch it
after the Liu block lands, with the MERFISH runner and
`SOMICS_BUILD_SCRIPT=scripts/build_seqfish_package.py`.

### Literature harvest in progress (2026-09-07, `harvest-datasets` skill)

Done and pushed on `protein-adapters`: miR-Space (bioRxiv
10.64898/2026.08.12.744364, not in paperclip; 2 datasets by hand, controlled
access) and a sweep -- searches `s_17a07eb8` (200 papers; 75 new by DOI/id),
extraction map `m_93d14edc` -> 277 claim rows appended to
`data/literature_datasets.csv` (2431 -> 2708). Steps 6-8 are done too: trace
map `m_fb3317d6` -> `trace_originals.py` added 194 registry rows and 636 usage
rows (the trace covered all 197 mapped papers, not only the 70 new; existing
rows untouched), `classify_spatial_modality.py --apply` set `is_spatial` on
them ({'yes': 104, 'unknown': 67, 'no': 23}), `resolve_download_urls.py` ran after. Registry is
5959 rows. **Review items**: the added rows flagged `is_spatial: no`
(scRNA-seq / small-RNA references) belong in
`data/dissociated_reference_datasets.csv`; 45 added rows carry an
unresolved-original note; platform strings left as written. The recipe, for
the next sweep:

```bash
paperclip results m_fb3317d6 --save /tmp/trace_0.txt          # per-paper answers
paperclip results s_17a07eb8 --export-bundle /tmp/bundle  # cohort.csv: document_id, title, doi
# new papers = cohort rows whose doi/document_id are not in literature_datasets.csv (75)
# meta.jsonl: one line per new paper {"id": document_id, "title", "doi", "year"} from cohort.csv
python3 scripts/trace_originals.py --trace /tmp/trace_0.txt --meta /tmp/meta.jsonl --cache /tmp/crossref_cache.json
python3 scripts/resolve_download_urls.py
# commit data/datasets.csv + data/model_dataset_usage.csv with the s_/m_ ids in the body
```

paperclip changed under us (0.7.38 -> 0.7.48): `search` needs `-s papers`;
result ids are now document ids (`PMC12611760`, bioRxiv DOIs), not
`bio_`/`pmc_` hashes, so dedup against the sheet by **DOI and document_id**;
`results <s_id> --save x.csv` writes only a summary for accumulated
`searches` sets -- use `--export-bundle DIR` and read `cohort.csv`; the skill's
`append_datasets.py` lives at `.claude/skills/harvest-datasets/scripts/`. The
map output is `--- [N] [success] Title ---` blocks followed by JSON; match to
the cohort by exact title. 13 of the 70 new papers reported no datasets;
non-spatial reused datasets (scRNA-seq references) were kept at the claim
level for the curated step's `is_spatial` decision.

### How the runs were watched

A persistent Monitor per box polling S3 every 4-5 min: the newest prefix,
`_done.txt`/`_failed.txt` line counts, `_DONE`/`_FAILED`, instance state.
Distinguish concurrent runs writing under one family by a dataset-name prefix
in `_geometry/` or `_failed.txt`, not by "newest". On a skip, read
`_logs/<dataset>.log`; the last `Error` line is the cause. Throwaway smoke runs
(one dataset into an atlas built from the rebuild base) proved every new
layout before it touched the production line; delete their prefixes after.

### Registry state

`data/datasets.csv` is 5,763 rows. 18 10x re-releases folded
(`data/tenx_rereleased_rows.csv`); 10x Visium/Xenium rows carry
`data_downloadable` verdicts; 16 Visium rows in
`data/tenx_visium_rows_needing_review.csv`. Atera: breast staged (bundle +
H&E + artifacts), cervical has no bundle and a mislinked H&E. Not yet done:
an `in_atlas` column; the 172 MIBI DeepCell+SPRM re-processings marked as
duplicates of ingested sections.

### Numbers to expect when everything lands

~540 sections, ~50M obs rows: 20.3M Visium HD bins, ~19M Xenium cells
(incl. ~7.5M from 18-20 HuBMAP small-intestine sections at 5K genes), ~8M
SPRM cells, 2.4M base, 0.6M MIBI, 219k Visium spots. Five organisms.

## Gotchas that cost real time

**Hosts disagree about user agents, in opposite directions.** Dropbox serves an
HTML preview to a browser UA and the real file to a bare one; Zenodo's API
returns 403 to a browser UA and 200 to a bare one; 10x's Cloudflare rejects bare
agents; **figshare's `ndownloader` answers a browser UA with `202` and an
empty body** and redirects only curl's own UA, to a signed S3 URL that expires
in 10 s (so no HEAD-then-GET). Any fetcher needs per-host UA policy and a
retry that flips it. A 200-with-HTML is the dangerous case — sniff the body,
don't trust the status — and so is a 202 with nothing in it.

**GEO's bulk endpoint lies.** `download/?acc=X&format=file` 404s for any series
without a RAW bundle. The FTP supplementary directory
(`ftp.ncbi.nlm.nih.gov/geo/series/GSExxxnnn/<acc>/suppl/`) is fine. Fixing this
recovered 71 datasets that had silently failed.

**HuBMAP file manifests are in a second, undocumented index.** The portal index
has no file fields at all. Use `POST search.api.hubmapconsortium.org/v3/files/search`
with `{"term": {"dataset_uuid.keyword": uuid}}`, then download over plain HTTP
from `assets.hubmapconsortium.org/<uuid>/<rel_path>` — no auth, no Globus.
Datasets with `contains_human_genetic_sequences` are absent from that index
entirely and need controlled access; **no transport change reaches them**.

**ETags are not comparable across multipart boundaries.** A 65-part upload and
a single-part server-side copy of identical bytes have different ETags. Compare
sizes and content, not ETags.

**S3 takes `..` in a key literally.** `aws s3 sync s3://bucket/a/../b/ dest`
matches zero keys, copies nothing, and exits 0. That left the 84 GB reference
cache silently absent on attempt 5's box — `polycomb setup` then created 11
*empty* tables over the void and reported "Reference DB ready", and gene
resolution fell through to gget's Ensembl MySQL and hung. Guard a sync by what
landed on disk (`du -sm`), never by its exit status. (`polycomb setup` saying
CREATED rather than "already existed" is itself the tell that the sync
delivered nothing.)

**`optimize()` can leave the obs table unreadable under a filter.** Every
ingest compacts obs into ~1M-row fragments, and a compacted fragment has twice
come out with a struct null buffer Lance rejects on a *filtered* read of a
pointer column (`Incorrect number of nulls for StructArray`): after the CosMx
ingest, and again at the end of the Visium block (22.9M rows), where the
verifier hit it on its first section. Unfiltered scans are fine, so nothing is
lost, but every `where()` on obs fails. `scripts/repair_atlas.py` checks each
struct column under a filter, rewrites the table from a whole read if one
fails, and snapshots; both EC2 ingest scripts run it before their final sync,
and `scripts/repair_atlas_ec2.sh` runs it standalone on a 128 GB box. A run
whose `_repair.txt` is missing has not been checked.

**An enum column that is null on every row cannot be compacted.** Lance writes
it and `optimize()` then fails with `Value at position 0 out of bounds ... [0,
-1]` (an empty dictionary) — *after* the rows are in the atlas. The first MIBI
package did this through a null `segmentation_method`, following the schema's
own "null when unreported" advice. The enum now carries `UNKNOWN`, the schema
doc says to use it, and `somics.ingest` refuses a package with an all-null enum
obs column before writing anything.

**The first package to ingest types the atlas's registry tables.** polycomb's
`_copy_registry_key_tables` creates each registry-key table verbatim from the
first collection carrying it, so a family whose donors have no ages hands over
an all-null `age_unit` typed float64 (pandas NaN inference) and the first real
`'year'` string cannot cast into it. Ingestion order silently decided column
types; the run died only when LIBD — the one family with donor ages — ingested
last. `ensure_registry_tables` in `somics.ingest` now pre-creates the tables
empty from the schema's own types (enum dictionaries flattened to their value
type, matching the published atlas and dodging the all-null-enum Lance encoder
bug), so every package is a merge into known-good types, in any order.

**paperclip quirks**: `sql`-saved result sets cannot be used with `map --from`;
`results --save` truncates titles (read `/papers/<id>/meta.json` instead); maps
over ~1,000 papers hit a ~25 min server cap, so chunk with `-n`/`--offset` and
recover with `map --resume <id> --retry-failed`; `.xlsx` supplements are indexed
as *summaries only* (row/column counts, no cell values), so spreadsheet SI is
invisible to grep.

**Some staged HuBMAP files are truncated, not just missing -- at scale.** A
size check of every file the MIBI, SPRM and HuBMAP-Xenium specs use against
the HuBMAP files index found **106 files across 44 datasets short by multiples
of 256 KB** (36 SPRM datasets, mostly expression images and covariance CSVs;
3 MIBI stacks; 5 Xenium datasets), with no error recorded at staging time. The
builders found them as JPEG 2000 decode errors and one-page stacks. (Metadata
TSVs differ from the index by 35-38 bytes systematically; that is not
truncation.) Check a staged file's size against the files index before
trusting it, and re-fetch with `scripts/restage_hubmap_files_ec2.sh`, which
accepts only a byte-exact result -- the assets server answers HEAD with 500,
403s curl-like user agents, and served both files as error pages for hours on
2026-09-07. All four truncated files (two Xenium z-stacks, two MIBI stacks) were
re-staged byte-exact on 2026-09-07 once the server recovered; they go through
the next follow-up passes (Xenium run 4, a protein follow-up).

**Some HuBMAP files are indexed but not served.** The files index lists them,
`assets.hubmapconsortium.org` returns 404 for every one, on any UA, at any
concurrency. 117 datasets are affected and 9 of them fail *wholesale* (743/743
files), concentrated in **seqFISH and MALDI**. A retry at 6 workers moved this
only from 157 datasets to 117, so treat it as missing upstream data and stop
retrying. The remaining gap is 6,232 files / 0.96 TB.

*This was initially misdiagnosed as concurrency-induced rate limiting.* Two
things caused that: a 6-file spot check that happened to land on
partially-short datasets rather than the wholesale-failing ones, and the fact
that `stage_hubmap_to_s3.py` collects failures in memory and prints them only
in its closing summary — so grepping a running log for errors returns zero no
matter how many have occurred. **Judge a run's health from manifests vs actual
objects, not from its log.**

**A successful operation is not a correct outcome.** Four instances this week,
all the same shape: "465 datasets staged, zero failures" where each fetch pulled
one file from a multi-file record; a HuBMAP run judged healthy from a log that
cannot show errors until it ends; a verification reporting "0 failures" having
performed 0 checks; and 16 staged prefixes holding a GitHub source release
instead of data, each with a manifest and `data_downloadable = yes`. Check the
artifact, not the exit status.

**On CytAssist Visium, `_image.tif` is not the coordinate frame — and the
frame is not the spot extent either.** 10x's pages offer `_image.tif` (the
CytAssist instrument's own capture, ~25 MB) and `_tissue_image.btf|tif` (the
microscope scan, GBs); Space Ranger writes `pxl_*_in_fullres` in the frame of
the microscope image whenever one was supplied, so the small file places every
crop on the wrong pixels with no error. The reliable frame check is
`tissue_hires_image.png` size / `tissue_hires_scalef`, which is the size of the
image Space Ranger was given, to a few pixels. A bounds check is *not* a frame
check: CytAssist detects tissue on its own full-capture-area image, so in-tissue
spots can lie past the edge of a microscope scan that covers only part of the
area (two of the first 25 datasets; up to 23% of the width). Those are real
measurements with no pixels under them; `build_visium_package.py` pads the
image with background to the spot extent so every row is placeable and the
crops there are honestly blank, and records `padded_from_hw` in the geometry
and on the section-image description.

**Verify by content, not by size.** The Dropbox incident stored a 192 KB HTML
page as an `.h5ad` and recorded it as success. Magic bytes are cheap:
`aws s3 cp s3://... - | head -c 8 | xxd`.

## Infrastructure

AWS profile `sci-data-dev-poweruser` (account 440744247602, us-east-1). Profiles
are generated by:

```bash
aws-oidc configure --issuer-url https://czi.okta.com \
  --client-id 0oa1be0s0d6KhEAed1t8 \
  --config-url https://aws-oidc.prod-central.prod.czi.team \
  --default-region us-west-2 --default-role-name poweruser
```

Reusable for staging runs, both already created:
- IAM role + instance profile `somics-raw-staging` (SSM core + S3 write to `somics-dev`)
- Security group `sg-0e81dbfc34d71253c` — **no inbound rules**, egress 80/443 only
- Private subnet `subnet-0fce42712a109e498` (us-east-1a), NAT egress, S3 gateway endpoint

Pattern for long jobs: launch with
`--instance-initiated-shutdown-behavior terminate`, run under `tmux`, and arm a
`systemd-run` watcher that archives the log to S3 and calls `shutdown -h now`
when the summary line appears. Access via SSM only — no SSH, no public IP, no
key pair. CZI treats an exposed port 22 as a security risk.

## Scripts

| script | does |
|---|---|
| `.claude/skills/harvest-datasets/` | the whole literature harvest workflow — **read this first** |
| `scripts/trace_originals.py` | map exports → canonical datasets, Crossref-resolved |
| `scripts/scan_publications_for_data.py` | mine papers + supplements for accessions |
| `scripts/resolve_download_urls.py` / `_v2.py` | accessions/landing pages → fetchable URLs |
| `scripts/verify_downloads.py` | probe every link, write `data_downloadable` |
| `scripts/recover_dead_links.py` | rehome, re-resolve or Wayback dead links |
| `scripts/stage_raw_to_s3.py` | stream bundles to S3, resumable, per-host UA fallback |
| `scripts/stage_hubmap_to_s3.py` | whole HuBMAP datasets, resumable per file |
| `scripts/add_hubmap_to_registry.py` | portal TSV → registry rows |
| `scripts/classify_spatial_modality.py` | `is_spatial` flag + spatial epigenomics |
| `scripts/bucket_inventory.py` | what is actually staged, by tissue/species/technology |
| `scripts/staged_summary.py` | one nested table: technology > tissue > species |
| `scripts/pipeline/` | the reconstructed staging/resolution/finalization scripts |
| `scripts/verify_rebuild_matches_atlas.py` | three-tier diff of a rebuild against the published atlas |
| `scripts/resolve_tenx_visium_files.py` | 10x Visium/HD rows → the counts/spatial/image files a builder needs, HEAD-verified |
| `scripts/make_tenx_visium_specs.py` | those files + the catalogue → one spec per dataset in `specs/tenx_visium/` |
| `scripts/run_visium_pipeline.sh` | build + ingest any Visium or Visium HD spec (LIBD runner delegates to it) |
| `scripts/ingest_tenx_visium_ec2.sh` | user-data: fetch, stage to `raw/`, build, ingest, sync — the whole 10x Visium block |
| `scripts/verify_visium_ingest.py` | read ingested Visium/HD sections back (local or S3 atlas) and check them against spec, source and image frame |
| `scripts/fold_tenx_rereleases.py` | fold 10x re-release rows into one per sample; write the block's `data_downloadable` verdicts and review list |
| `scripts/make_sprm_specs.py` | staged HuBMAP CODEX/PhenoCycler rows → one spec per dataset in `specs/sprm/`, verdicts in `data/sprm_datasets.csv` |
| `scripts/build_sprm_package.py` | one SPRM spec → obs, var, uint32 totals matrix, (Y, X, C) expression image (assembler and harmonizer alongside) |
| `scripts/run_sprm_pipeline.sh` | build + ingest any SPRM spec (single-obs shape, four library tables) |
| `scripts/make_mibi_specs.py` | HuBMAP MIBI rows → layout classification, `specs/mibi/`, `data/mibi_datasets.csv` |
| `scripts/build_mibi_package.py` | one MIBI lab submission → per-cell ion counts from mask + stack, (Y, X, C) image |
| `scripts/run_mibi_pipeline.sh` | build + ingest any MIBI spec (single-obs shape) |
| `scripts/resolve_tenx_xenium_files.py` / `make_tenx_xenium_specs.py` | 10x Xenium outs rows → verified bundles + one spec per dataset in `specs/tenx_xenium/` |
| `scripts/ingest_tenx_xenium_ec2.sh` | user-data: fetch the outs zip (or copy S3 files), extract, stage, build, ingest, repair, sync; `SOMICS_SPEC_DIRS` picks specs/tenx_xenium, specs/hubmap_xenium or specs/atera |
| `scripts/make_hubmap_xenium_specs.py` | HuBMAP's 20 Xenium submissions → specs with real donor/block ids and S3 sources |
| `scripts/verify_atlas_ec2.sh` | run the verifier on EC2 under the instance role (a laptop's SSO token expires mid-run) |
| `scripts/stage_urls_ec2.sh` | stage any list of URLs into `raw/` with manifests, from EC2 (used for Atera) |
| `scripts/repair_atlas.py` / `repair_atlas_ec2.sh` | per-section pointer-read check; rewrite + snapshot the obs table if a compacted fragment fails |
| `scripts/ingest_protein_ec2.sh` | user-data: SPRM + MIBI blocks into the newest atlas prefix, serial |
| `scripts/make_seqfish_specs.py` / `build_seqfish_package.py` | HuBMAP seqFISH (Cai lab): one section per field of view with its DAPI; reuses the MERFISH assembler/harmonizer/runner via `SOMICS_BUILD_SCRIPT`; `specs/seqfish/` (6 datasets, 43 FOVs); `docs/2026-09-10_seqfish_adapter.md` |
| `scripts/build_merfish_package.py` / `assemble_merfish_collection.py` / `harmonize_merfish_package.py` / `run_merfish_pipeline.sh` | MERFISH: an Allen Brain Cell Atlas release (or a staged MERSCOPE bundle) -> per-section 10x-format h5 + obs, no image; `specs/merfish/`; notes in `docs/2026-09-09_merfish_adapter.md` |
| `scripts/backfill_hubmap_dataset_type.py` | recover technology the portal TSV writes as N/A |
| `scripts/copy_atlas_to_s3.py` | mirror the atlas R2 → S3 |
| `scripts/render_report_pdf.py` | markdown + figures → PDF via Playwright |
| `analysis/*.py` | the four report figures |

## Open issues

- `aopisco/somics#14` — portable ingestion (the blocker)
- `aopisco/somics#16` — HuBMAP: access route, 115.8 TB scope, what to ingest
- `aopisco/somics#15` — SAHA watch: the Zenodo community is empty, data not deposited
- `chanzuckerberg/somics#1` — port plan, awaiting @ebezzi review

## Decisions already made — don't relitigate

- One table for everything, with `found_via` / id prefixes marking provenance,
  rather than separate literature and consortium registries.
- Every HuBMAP dataset gets a row, protected ones included; they simply carry no
  `download_url`.
- Tier 2 only for HuBMAP (18.5 TB). Raw CODEX and PhenoCycler are 87 TB and
  **9.77M of the 9.87M objects** — 75% of volume for 12% of datasets — and the
  SPRM-processed variants of much of the same tissue are already included.
- Our HTTP pipeline over Globus for this size: measured throughput made the cost
  difference ~$6, and consistent `_manifest.json` provenance was worth more.
  Globus is the right call if the 87 TB raw tiers are ever wanted.
- Where a paper cites 2-3 accessions, fetch them **all** rather than guess the
  mapping. Storage is cheap next to a wrong link.
