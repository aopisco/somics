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
- Blank beats guessed. A wrong accession or modality is worse than an empty
  cell, and several columns are deliberately sparse for that reason.

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
- **Ingestion into the atlas is blocked** on `polycomb`/`homeobox` — see below.
- 556 registry datasets have an access link but nothing fetchable; the clusters
  are CNGB, GSA-Human, HuBMAP portal links, and GitHub repos without releases.

## The blocker — libraries fixed, scripts reconstructed, unproven

@conradry released **homeobox 0.2.9** and **polycomb 0.0.3** (2026-08-25), which
fix the import wall. `homeobox.schema` is a package again with `.ir`/`.parser`,
it defines `emit`, and `polycomb.ingestion` imports. `pyproject.toml` pins both;
`tifffile` was also undeclared and is now. Verified: every symbol the repo
imports resolves, 170 tests pass, and the existing 59-section atlas still opens
under 0.2.9, so no migration is needed. **0.2.9 requires Python >= 3.12.**

**Seven driver scripts were never released** — they are Claude skills, not
package code. Five are reconstructed in `scripts/pipeline/`
(`stage_lance_tables`, `stage_library_table`, `stage_dataset_table`,
`apply_resolution_pass`, `finalize_collection`); the runner now calls those
instead of `/home/ubuntu/.claude/skills/`. Two more —
`join_feature_space_obs.py` and `stamp_uid_on_feature_space_obs.py`, named in
`materialize_bare_obs.py` — are **not** reconstructed. They are only needed by
datasets with two obs tables, so CosMx and Monkman, not the Xenium gate.

**Treat the reconstruction as unproven until it passes tier 2** of
`scripts/verify_rebuild_matches_atlas.py`. Full evidence trail in
`docs/2026-08-25_pipeline_reconstruction.md`; the plan in
`docs/2026-08-25_atlas_rebuild_plan.md`.

`SOMICS_DATA_HOME` overrides the `/home/ubuntu` prefix everywhere (19 files).

Tracked in `aopisco/somics#14`. @conradry is outside the CZI org, so that
conversation must stay on the public repo.

## Rebuilding the atlas is the correctness gate

Do not ingest 20 TB before reproducing the 59 sections we have — the published
atlas is the only ground truth, and a pipeline regression in new data is
indistinguishable from a quirk of the new data.

- **Stable uids reproduce; obs uids do not.** `make_stable_uid("hColon_Cancer_
  Add_on_FFPE")` is the published `section_uid`, so sections, donors, panels and
  features are comparable exactly. `uid` on obs and `dataset_uid` are `uuid4` —
  join on `source_obs_id` instead, and never compare them.
- **Only 46 of 59 sections have a builder.** The 12 spatialLIBD Visium sections
  and the 1 Xenium colon section never had one in this repo — checked the full
  history including deleted files.
- **10x's CDN gives us ~0.3 MB/s** regardless of user agent, against 16 MB/s
  from S3. Fetch vendor bundles on EC2 into `s3://somics-dev/rebuild/` and pull
  from there. Version paths differ per dataset: lung preview is `1.3.0`, colon
  is `1.6.0`, and guessing one for both returns 403.
- **Six members of an outs bundle are enough** — selective extraction turns
  18.42 GB into 1.3 GB. `transcripts.parquet` is the bulk and is unused.

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
