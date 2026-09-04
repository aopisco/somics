# somics — working notes

State, decisions and hard-won gotchas for this repo. Written to be picked up
cold. Numbers are as of 2026-08-25 and move as jobs finish — re-run
`scripts/bucket_inventory.py` rather than trusting them.

## What this project is

Three things, in increasing order of how finished they are:

1. **A dataset registry** — `data/datasets.csv`, 5,585 rows, one per dataset,
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
| `data/literature_datasets.csv` | claim-level: one row per (dataset × source paper) | 2,429 |
| `data/datasets.csv` | curated: one row per dataset, keyed to its original publication | 5,585 |
| `data/model_dataset_usage.csv` | many-to-many: which paper/model uses which dataset | 3,526 |
| `data/dissociated_reference_datasets.csv` | rows removed from the registry as non-spatial | 182 |
| `data/st_corpus.csv` | TERRA supplementary table, maintained by hand, **not** produced by this pipeline | 455 |

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
scripts/run_libd_dlpfc_pipeline.sh
```

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

**The work is on the `atlas-rebuild` branch, not `main`.** `main` stops at the
three-tier verifier; everything after it — the spec-driven builders, the
extended schema, the specs, the rebuild script and all the docs referenced here
— is on the branch. A fresh clone lands on `main` and misses it.

```bash
git checkout atlas-rebuild && git pull
```

`aopisco/somics#17` is the PR, marked **ready for review** on 2026-09-02 — the
unattended rebuild landing and verifying was the condition it was held on. AWS
access expires; re-run the `aws-oidc configure` line under Infrastructure if a
call returns a credentials error.

**The rebuild has landed.** Attempt 7 built, synced and verified unattended in
~3h20m on 2026-09-02: `s3://somics-dev/rebuild/atlas/2026-09-02T00-43-52Z/`,
with `_verify.txt` and `_rebuild.log` beside it. **236/237 checks passed**; the
one failure is `hColon_Cancer_Add_on_FFPE` gene_expression (6417/108200 values
differ), which is the published atlas's misaligned gene axis (`#19`) — the
rebuild is the correct side of that diff.

For any future rebuild: `scripts/rebuild_atlas_ec2.sh` as user-data (the exact
`run-instances` call and an SSM log-tail are in
`docs/2026-08-30_full_atlas_build_plan.md`). It fetches then builds one family
at a time, lung preview first, so a regression fails in minutes rather than
after the full fetch; it syncs the atlas to S3 **before** verifying, then
terminates — so the S3 prefix is the answer, not the instance. ~3-4 hours end
to end. Judge a run by its artifacts, and check the log's *mtime* over SSM if
nothing is landing: attempt 5 sat four hours in a silent SYN-SENT hang that no
FAILED log would ever report.

Seven attempts; six failures, all scaffolding, never pipeline: a shutdown timer
that took the atlas with it; `/tmp` a tmpfs too small for an 18 GB bundle; a
lung spec predating the parameterized assembler; `AddColumn` rejecting
`value=None` for a healthy section's null disease; the reference cache silently
never syncing (the `..` gotcha below), which sent gene resolution into gget's
Ensembl-MySQL hang; and registry tables typed by whichever package ingested
first (the other new gotcha below).

**Next:**

1. **Ingest the 175 datasets an existing builder can read.** Coverage is by
   *source layout*, not platform: 130 10x Visium/HD, 44 10x Xenium outs, 1
   Monkman. All have verified bundle URLs. The builders are spec-driven and
   proven byte-identical against the published atlas.
2. Held by decision, not blocked: HuBMAP Histology + Auto-fluorescence (1,119
   staged), and MIBI + PhenoCycler + Cell DIVE (~590). Both need adapters.
3. **The 144 unknown-layout Visium/Xenium rows are not spec-work.** 141 are
   staged, 120 from GEO, and GEO deposits are flat per-GSM files rather than a
   Space Ranger directory — `filtered_feature_bc_matrix.h5` next to `.cloupe`
   and loose TIFFs, named differently in every deposit. Per-deposit agent
   curation, not a spec.
4. GeoMx is **blocked on access**, not code: all 1,362 HuBMAP GeoMx datasets are
   `data_access_level: protected`. See `aopisco/somics#16`.

**Open issues:** `#16` HuBMAP (the single tracker), `#18` staging completeness
(one file per multi-file deposit, plus 16 prefixes holding source code rather
than data), `#19` the published colon section's misaligned gene axis, `#14`
portable ingestion, `#15` SAHA watch.

## Gotchas that cost real time

**Hosts disagree about user agents, in opposite directions.** Dropbox serves an
HTML preview to a browser UA and the real file to a bare one; Zenodo's API
returns 403 to a browser UA and 200 to a bare one; 10x's Cloudflare rejects bare
agents. Any fetcher needs per-host UA policy and a retry that flips it. A
200-with-HTML is the dangerous case — sniff the body, don't trust the status.

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
