# Rebuilding the hackathon atlas from scratch

*2026-08-25 · feasibility study and plan, written before any rebuild work*

## Why do this first

We want to ingest 20 TB. Before that, we should be able to reproduce the 59 sections
we already have, because the published atlas is the only **ground truth** available:
if the rebuilt copy matches it, the toolchain is correct, and every later ingest
rests on something checked rather than assumed. If we skip this and go straight to
HuBMAP, a silent regression in the pipeline is indistinguishable from a quirk of the
new data.

There is a second reason. Five pipeline scripts and two package builders exist only
on the hackathon box. Some of them will have to be reconstructed. Reconstructing
them against a target we can diff is tractable; reconstructing them against new data
is guesswork.

## What is in the published atlas

`s3://epiblast-public/somics_spatial_atlas`, read with homeobox 0.2.9: **59 sections,
59 datasets, four families.**

| family | sections | section ids |
|---|---:|---|
| Monkman NSCLC CODEX | 36 | `Monkman_NSCLC_CODEX_1D` … |
| spatialLIBD DLPFC Visium | 12 | `LIBD_151507` … `LIBD_151676` |
| CosMx NSCLC | 8 | `Lung5_Rep1` … `Lung13` |
| Xenium lung preview | 2 | non-diseased, lung cancer |
| Xenium colon preview | 1 | `hColon_Cancer_Add_on_FFPE` |

## What we have, and what is missing

### Package builders — 3 of 5

`build_xenium_lung_package.py`, `build_cosmx_nsclc_package.py` and
`build_monkman_codex_package.py` are in the repo, with their `assemble_*_collection.py`
and `verify_*_ingest.py` partners. Together they cover **46 of 59 sections**.

**There has never been a builder in this repo for the 12 LIBD Visium sections or the
1 Xenium colon section.** I checked the full git history, including deleted files —
they were never committed. Those 13 sections were built on the hackathon box by
something we do not have.

### Pipeline orchestration — 0 of 5

`run_xenium_lung_pipeline.sh` shells out by absolute path to five scripts under
`/home/ubuntu/.claude/skills/`: `stage_lance_tables.py`, `stage_library_table.py`,
`stage_dataset_table.py`, `apply_resolution_pass.py`, `finalize_collection.py`.
These are Claude skills, so they were not part of the polycomb release.

The library functions they drive **are** all in polycomb 0.0.3 —
`polycomb.util` exposes `discover_tables`, `finalization_order`, `read_arrow`,
`overwrite_table`, `set_arrow_column`, `drop_arrow_columns`, `join_key`;
`polycomb.finalize_columns` exposes `ensure_schema_columns_for_table` and
`deferred_field_names`; `polycomb.resolvers` has the resolution pipeline. So this is
orchestration over an intact library, not missing capability.

`finalization_order(info)` matters in particular: the ordering constraint I previously
flagged as tribal knowledge is a library function, which makes reconstruction far less
risky than it looked.

### Source data — all four families are retrievable

| family | source | size | status |
|---|---|---:|---|
| Xenium lung, non-diseased | `cf.10xgenomics.com/samples/xenium/1.3.0/…` | 18.42 GB | **already in `s3://somics-dev/raw/human_lung2025_xenium`** |
| Xenium lung, cancer | same, v1.3.0 | 30.66 GB | reachable (HTTP 200) |
| Xenium colon | same, **v1.6.0** | 20.19 GB | reachable |
| CosMx NSCLC ×8 | `nanostring-public-share` S3, *SMI Flat data* | 11.3 GB | reachable, all 8 present |
| Monkman CODEX | Zenodo record 10258578, 45 files | 41.2 GB | reachable; 1 file already staged |
| spatialLIBD DLPFC ×12 | spatialLIBD / Bioconductor | TBD | not yet located |

**~121 GB total**, of which 18 GB is already in our bucket. The CosMx raw morphology
images (361 GB) are *not* needed — the builder reads only the flat files.

Note the version skew: lung preview is at `1.3.0` and colon at `1.6.0`. Guessing one
path for both returns 403, which is what my first probe did.

## The verification method — and why it can be exact

UIDs in this schema are **deterministic content hashes**, not random. Confirmed:

```python
>>> from homeobox.schema import make_stable_uid
>>> make_stable_uid("hColon_Cancer_Add_on_FFPE")
'183c734af72b51e0'          # == section_uid in the published atlas
```

So a rebuild is comparable to the published atlas **key by key**, not merely
statistically. `polycomb.collection.make_uid()` *is* random (`uuid4`), so anything
keyed off it — collection-internal ids — will differ; identity must be asserted on
the stable uids and on content.

Three tiers, in increasing strength:

1. **Structural** — same section_uids, dataset_uids, feature_spaces, `n_cells`,
   `extent_um`, per-section obs row counts.
2. **Content** — obs columns equal after sorting by uid; `x_um`/`y_um` equal within
   float tolerance; gene/protein matrices equal; image crops byte-identical.
3. **Provenance** — the metadata carried per section (study name, panel, donor,
   disease, accession link) equal.

Tier 2 is the real test. Tier 1 alone would pass even if every expression value were
wrong.

Expected *legitimate* differences, to be recorded rather than chased: collection-internal
random uids, file mtimes, and any ontology term that has since been re-resolved
against a newer OLS.

## Plan

**Phase 0 — inventory.** Done; this document.

**Phase 1 — stand up sources.** Fetch the five bundles into `s3://somics-dev/rebuild/`
(~103 GB new), reusing the lung bundle already staged. Locate the spatialLIBD source.

**Phase 2 — the five orchestration scripts.** Ask @conradry first; it is three
directories and costs him minutes. In parallel, reconstruct them from the polycomb
primitives — the atlas gives us a target to diff against, so a wrong reconstruction is
*detectable*, which is exactly the condition that was missing before.

**Phase 3 — rebuild Xenium lung (2 sections).** Smallest complete family with a
builder, and half its source is already staged. Run the full pipeline, then diff at
all three tiers. **This is the go/no-go gate.**

**Phase 4 — CosMx (8) and Monkman (36).** Brings the reproduced set to 46 of 59.
Monkman is the volume test: 36 sections, 60 channels, ~41 GB of OME-TIFF.

**Phase 5 — the 13 orphan sections.** Write builders for spatialLIBD DLPFC and Xenium
colon. Unlike phases 3-4 this is new code, but the atlas still gives us the target.

**Phase 6 — publish and diff whole-atlas.** Rebuild into `s3://somics-dev/atlas_rebuild/`,
diff against the R2 original, and write up every difference found.

## What could go wrong

- **The reconstructed scripts are subtly wrong.** Most likely failure. Mitigated
  entirely by phase 3 being a diff against ground truth — but only if we hold tier 2,
  not just tier 1.
- **Ontology drift.** Resolvers hit live OLS; terms may have changed since the
  hackathon. Would show as metadata diffs on otherwise-correct data. Record, do not
  silently accept.
- **Vendor bundles have been revised.** 10x re-issues datasets under new version
  paths. If content changed, the diff fails for a reason that is not our bug — check
  the bundle before blaming the pipeline.
- **spatialLIBD is not locatable in the same form.** Then 12 sections cannot be
  reproduced from source, only compared as ingested.

## The honest bottom line

We cannot rebuild the atlas today. We can rebuild **46 of 59 sections** once the five
orchestration scripts exist, and all the source data for them is reachable. The other
13 need builders that were never committed.

The single cheapest unblock is @conradry sending three directories.
