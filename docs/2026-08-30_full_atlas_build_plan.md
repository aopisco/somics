# Building the atlas over everything we hold

*2026-08-30 · scope, sequencing, and what it costs in time and money*

Every number here is measured from this project unless marked as an estimate.
The rates that matter came from the 59-section rebuild, not from guessing.

## What "everything" actually is

`data/datasets.csv` holds **5,737 rows, 5,280 of them spatial**. That is the
catalogue, not the corpus — most rows have never been fetched.

| block | spatial rows | staged | staged volume |
|---|---:|---:|---:|
| HuBMAP | 3,850 | 1,891 | **17.65 TB** |
| literature | 1,284 | 467 | **2.42 TB** |
| 10x catalogue | 146 | 0 | 0 (141 have a verified URL) |
| **total** | **5,280** | **2,358** | **20.07 TB** |

By modality: 2,604 spatial transcriptomics, 2,393 spatial proteomics, 16 spatial
epigenomics, 267 unclassified.

**The corpus is not a flat list of 5,280 problems.** It is three blocks with
completely different unit economics, and the plan follows that split.

## The economics: heterogeneity, not volume

Ryan's figure is **$10–20 and 30–60 minutes of agent time per dataset** for
curation. Taken naively over 5,280 datasets that is **$53k–106k and 2,600–5,300
agent-hours**. That number is wrong, in both directions, and the reason is the
finding that shaped this whole project:

> A spec-driven builder makes the first dataset of a family expensive and every
> subsequent one nearly free.

Measured today: `build_xenium_package.py` + a spec reproduces the hardcoded lung
builder **byte-identically**, and adding the colon section afterwards cost one
JSON file. Adding LIBD's 12 sections cost one builder plus one spec covering all
twelve.

So the cost driver is **the number of distinct source layouts**, not datasets.

| block | distinct layouts | curation model |
|---|---:|---|
| HuBMAP | ~6 pipeline output shapes | one adapter per shape, then free |
| 10x catalogue | 3 (Xenium, Visium, Visium HD) | **already built** for 2 of 3 |
| literature | ~200+, effectively one per deposit | genuinely per-dataset agent work |

## Phase 0 — finish what is in flight (0.5 day, ~$5)

The 59-section rebuild is running. It must land and verify before anything is
built on top of it, because it is the only end-to-end proof the toolchain works.

Also outstanding and cheap:

- **Issue #18: literature staging fetched one file per dataset.** 465 of 467
  prefixes hold exactly one object; 96 are Zenodo records where a record is many
  files. Sampling 14, nine were multi-file and 49 files were unfetched. Must be
  fixed before the literature block is worth ingesting at all — we would be
  curating fragments.
- Teach the verifier to classify schema-introduced columns (
  `has_chromatin_accessibility`) as expected rather than as 59 failures.

## Phase 1 — 10x catalogue: 146 datasets (2 days, ~$60 compute, ~$40 agent)

**Do this first.** Highest ratio of datasets to new code anywhere in the corpus,
and the builders already exist.

- 141 of 148 have a **verified** bundle URL; 1.41 TB across 219 bundles.
- Xenium (35) and Visium (62) use builders proven against the published atlas.
- **Visium HD (49) reuses the Visium builder.** Same atlas schema, same feature
  spaces, and a bin is a `spatial_unit` exactly as a spot is; what differs is the
  source layout (`binned_outputs.tar.gz` carrying several bin sizes) and
  `unit_size_um`. So this is a variant in `build_visium_package.py` and a choice
  of which bin size to ingest, not a new builder. Revised estimate: half a day,
  down from one.
- Atera (2) is a new platform; treat as a spike, not a commitment.

Per-dataset cost after the builder exists is a spec: minutes of agent time,
cents. The real cost is the one Visium HD builder.

**Storage: +1.41 TB raw.**

## Phase 2 — HuBMAP: 1,891 staged datasets (1–2 weeks, ~$400 compute, ~$200 agent)

The largest block by far and, counter-intuitively, **cheap per dataset** because
it is pipeline output rather than lab output.

Staged today, by platform:

```
Histology          763      Auto-fluorescence  356      CODEX   124
MIBI               429      MALDI              151      Xenium   20, seqFISH 18
```

Six or seven adapters cover essentially all of it, because HuBMAP's processed
datasets share a layout: `anndata-zarr/`, `ometiff-pyramids/`, `pipeline_output/`.
**The AnnData is already there** — `homeobox.AnnDataReader` consumes it directly.

Sequenced by ratio of datasets to effort:

1. **MIBI + CODEX + PhenoCycler + Cell DIVE** (SPRM output, ~700 datasets) — one
   adapter. Protein is proven: CosMx and Monkman both ingest it today.
2. **Histology + Auto-fluorescence** (~1,120) — imagery only, `discrete_image`.
   Cheapest of all; no feature registry.
3. **MALDI (151) — blocked.** No `metabolite_abundance` feature space exists
   upstream. Deferred by decision; needs Ryan plus your in-house expert.
4. **seqFISH / Xenium (38)** — existing builders may cover with a spec.

Known gaps to carry: 117 datasets are short 6,232 files that HuBMAP indexes but
does not serve (permanent 404s), and 1,493 protected datasets need controlled
access that no transport change reaches.

**Storage: already staged. +~8 TB for the ingested atlas (estimate).**

## Phase 3 — literature: 1,284 spatial rows (6–10 weeks, $13k–26k agent)

This is where Ryan's per-dataset figure genuinely applies, because **every
deposit is its own layout**. No amortization is available.

Sequencing that keeps the cost honest:

1. **Fix issue #18 first.** Otherwise agents curate one file out of a 24-file
   record and produce a confident, incomplete package.
2. **Cluster before curating.** 48 Visium + 32 "10x Visium" + 14 "Visium Spatial
   Gene Expression" are almost certainly the same layout under three platform
   strings; the same is true across IMC (22) and MERFISH (11). Consolidating
   those strings is a day of work that could convert several hundred one-off
   curations into a handful of specs. **Do this before spending agent budget.**
3. Then agent-curate the genuine long tail, cheapest-first, in batches of ~50 so
   the per-batch failure rate is visible before the next batch is launched.

Estimate range is wide on purpose: **$13k–26k** assumes 1,284 datasets at
$10–20, and step 2 could plausibly halve it. I would not commit to the full
number without running one 50-dataset batch and measuring.

## Compute and storage, priced

**Compute.** m5n.2xlarge is **$0.53/hr** on demand. Measured from the rebuild:

| stage | measured |
|---|---|
| fetch 41 GB from Zenodo, 4 workers | 19 MB/s → ~35 min |
| fetch from our S3 | 16 MB/s |
| fetch from 10x CDN | **0.3 MB/s** — always route via EC2 |
| full 59-section build + ingest | ~3–4 h wall clock |
| 59-section verification diff | ~40 min |

The 59-section atlas (2.47M obs rows) built in one afternoon on one box. Scaling
linearly by obs rows, HuBMAP's ~1,891 datasets is on the order of **200–400
box-hours = $110–210**, plus re-runs. Call it **$400** for Phase 2 with failures
and iteration, **$60** for Phase 1.

**Storage.** S3 Standard at ~$0.023/GB/month:

| | volume | $/month |
|---|---:|---:|
| raw staged today | 20.07 TB | **$462** |
| +10x catalogue | 1.41 TB | $32 |
| +literature backfill (issue #18, est.) | ~5 TB | $115 |
| +atlas output (est. ~40% of raw) | ~11 TB | $253 |
| **projected total** | **~37 TB** | **~$860/month** |

Two levers, neither urgent: Intelligent-Tiering on `raw/` would cut the raw line
by roughly half once objects age out, and the 87 TB of HuBMAP raw CODEX we
deliberately skipped stays skipped.

## Totals

| phase | wall clock | compute | agent | new storage |
|---|---|---:|---:|---:|
| 0 finish rebuild + #18 | 3–4 days | $20 | — | ~5 TB |
| 1 10x catalogue | 2 days | $60 | ~$40 | 1.41 TB |
| 2 HuBMAP | 1–2 weeks | $400 | ~$200 | ~8 TB |
| 3 literature | 6–10 weeks | $300 | **$13k–26k** | ~2 TB |
| **total** | **~2–3 months** | **~$780** | **~$13k–26k** | **~17 TB** |

**Compute and storage are noise. Agentic curation of the literature tail is
95% of the cost**, and it is the only part where the estimate is soft.

## What would change these numbers most

1. **Consolidating platform strings before Phase 3.** Plausibly the single
   highest-leverage day of work in the plan. Started: 34 multi-platform rows
   split into 76, with 71 left needing a human. The remaining spread is real —
   **102 distinct strings contain "visium"** — and most of it is casing and
   vendor-suffix noise over a handful of genuine platforms.
2. **A metabolomics feature space upstream** unlocks 151 staged MALDI datasets
   for the price of one adapter.
3. **Whether HuBMAP's AnnData is as uniform as it looks.** I have confirmed the
   directory shape across a sample, not that every `anndata-zarr` carries usable
   spatial coordinates and channel names. One dataset opened end to end would
   de-risk the largest block in the plan; it is a couple of hours and I would do
   it before committing to the Phase 2 estimate.

## Relaunching the rebuild

The exact call, recovered from CloudTrail after the terminated boxes took it
with them. The box clones `atlas-rebuild` from GitHub at boot, so push first —
a local fix that is not on the remote does not exist as far as the run is
concerned. Bump the `Name` tag's attempt number.

```bash
aws ec2 run-instances \
  --profile sci-data-dev-poweruser --region us-east-1 \
  --image-id ami-0332d564d76dbd8d6 \
  --instance-type m5n.2xlarge \
  --count 1 \
  --security-group-ids sg-0e81dbfc34d71253c \
  --subnet-id subnet-0fce42712a109e498 \
  --iam-instance-profile Name=somics-raw-staging \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":400,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --instance-initiated-shutdown-behavior terminate \
  --user-data file://scripts/rebuild_atlas_ec2.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=somics-atlas-rebuild-N}]'
```

To watch a run in flight, tail its log over SSM — the box has no SSH:

```bash
aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript \
  --parameters 'commands=["tail -25 /var/log/rebuild.log"]' \
  --profile sci-data-dev-poweruser --region us-east-1
# then: aws ssm get-command-invocation --command-id <cid> --instance-id <id> ...
```

## Non-negotiables carried from the rebuild

- **Build an atlas in one pass.** Re-ingesting a package duplicates it silently;
  the section count does not change. Guarded now in `somics.ingest`.
- **A crashed ingest is not resumable.** Wipe and rebuild.
- **Preserve the artifact before doing anything optional.** The first rebuilt
  atlas was lost to a shutdown timer because the sync came after verification.
- **Verify against the source, not against another atlas.** That is what found
  the defect in the published colon section — 1041/1833 correct against the h5,
  where the rebuild is 1833/1833.
