# The ingestion pipeline, end to end

*2026-08-26 · what every step does, what it needs, and everything that broke*

This is the operating manual for turning a raw dataset into atlas rows. It
supersedes the scattered knowledge in the runner scripts. Read this before
running an ingest or writing a builder for a new dataset family.

## Prerequisites

Four, and three of them are not obvious.

**1. Python >= 3.12.** homeobox 0.2.9 requires it; 0.2.8 did not.

**2. polycomb's skills, installed separately from the package.**

```bash
curl -sSL https://raw.githubusercontent.com/epiblastai/homeobox/refs/heads/main/packages/polycomb/install.sh | bash
```

This is the single most important line in this document. The pipeline scripts
are Claude *skills*, not package code, so `pip install polycomb` does not
provide them. They land in `~/.agents/skills/`; point `POLYCOMB_SKILLS` at that
directory. Seven skills install, of which four matter here:
`prepare-package-for-resolution`, `schema-harmonization`, `finalize-tables`,
`multimodal-alignment`.

**3. The reference cache.** Not optional in a locked-down network:

```bash
hf download epiblastai/polycomb-lancedb --repo-type dataset --local-dir <path>
polycomb setup --db-path <path>
```

84 GB. Mirrored at `s3://somics-dev/polycomb/reference_db`, which is far faster
than Hugging Face from EC2. Without it, `resolve_genes` falls through to gget,
which opens a **MySQL connection to Ensembl on port 5306** — a port our
security group does not allow egress on, so it hangs in SYN-SENT forever with
no timeout. With it, 960 CosMx symbols resolve instantly.

**4. `imagecodecs`.** Declared in `pyproject.toml`. Xenium morphology TIFFs are
sometimes JPEG2000-compressed and `tifffile` refuses them without it. It varies
*within* a dataset family: the Xenium lung cancer section needs it, the
non-diseased one does not.

`SOMICS_DATA_HOME` overrides the `/home/ubuntu` prefix every path in the repo
was written against.

## The two shapes

A dataset's shape is decided by how many obs tables staging produces, which is
decided by how many feature spaces carry an OBS file.

### Single obs table — `scripts/run_xenium_lung_pipeline.sh`

```
1  build_xenium_package.py          outs bundle  -> obs/var CSVs
2  assemble_*_collection.py         registries + collection.json
3  stage_lance_tables.py            OBS/VAR CSVs -> Lance
   stage_library_table.py  (x4)     registry CSVs -> collection lance_db
   stage_dataset_table.py           one row per feature space
4  apply_resolution_pass.py (x3)    ontology columns, from the schema's markers
   harmonize_*_registries.py        what the resolver pass cannot do
   harmonize_*_datasets.py          per-dataset columns and join keys
5  materialize_bare_obs.py --phase bare
   finalize_collection.py
   materialize_bare_obs.py --phase artifact
6  python -m somics.ingest
7  verify_*_ingest.py
```

Xenium lung has two feature spaces (`gene_expression`, `discrete_image`) but
only one obs table, because the section image is a DATA file with no obs of its
own. Staging still suffixes the obs table, the join declines to merge a single
table, and the stamp declines to name an artifact for a two-space dataset — so
the table would be silently omitted from finalization. `materialize_bare_obs`
bridges that gap, and **must** bracket finalization: `bare` before, because
cleanup drops the target-side `*_join` columns once every referrer is resolved;
`artifact` after, because it needs the uids finalization assigns.

### Two obs tables — `scripts/run_cosmx_nsclc_pipeline.sh`

Identical except step 5:

```
5  reconcile_barcodes.py (per dataset lance_db) --obs-class SpatialObs
   finalize_collection.py
```

**That is the whole difference.** `finalize_collection` is an orchestrator and
already does everything else multimodal:

```
   join feature-space obs
1  assign_uids
1b stamp uid onto each per-space obs table     <- multimodal only
2  set_dataset_uid                              (obs only)
3  populate_registry_keys
4  compute_auto_fields                          (obs only)
5  drop_leftover_columns, ensure_schema_columns
6  validate_tables
```

**Do not call `join_feature_space_obs`, `assign_uids` or
`stamp_uid_on_feature_space_obs` around it.** They exist for table-by-table
debugging. Running them alongside the orchestrator double-runs steps and breaks
it, because the stamp *replaces* each per-space table with a uid-only artifact
and the orchestrator's own join then finds no barcodes.

## What each step actually requires

| step | needs | fails as |
|---|---|---|
| build | the vendor bundle's six files (Xenium: `cells.parquet`, `cell_feature_matrix.h5`, `morphology_focus.ome.tif`, `experiment.xenium`, `metrics_summary.csv`, `gene_panel.json`) | missing file, or an assertion on barcode order |
| assemble | built CSVs in staging; `coalesce(copy=False)` **moves** them into the package | a rebuild needs the source bundles again |
| stage | the collection manifest; leading column becomes `obs_key`/`var_key` | wrong column name downstream |
| resolution pass | network or the reference cache | silent hang without the cache |
| harmonize | staged tables with the columns the script names | `KeyError`, or a curation transaction error |
| reconcile | `obs_key` holding a **barcode string** shared by both modalities | `Could not convert '0' with type str` |
| finalize | everything above | pydantic validation errors naming the missing field |
| ingest | finalized tables; a clean atlas | see the resume trap below |

## Everything that broke, and why

Nine failures on the way to a green Xenium gate and a CosMx run. Five were real;
four I caused.

**Real:**

1. **The skill scripts were not on PyPI.** They install separately (above).
2. **`var_index` -> `var_key`.** Staging renamed the leading OBS/VAR column and
   added `row_position`. Our harmonizers, written at hackathon time, read the
   old name. Both now accept either.
3. **The same rename again, inside a SQL string** —
   `value_sql="coalesce(ensembl_gene_id, var_index)"`. Grep for a column name
   finds identifiers, not text embedded in SQL.
4. **`imagecodecs` missing** (above).
5. **Gene resolution hanging on a blocked port** (above).

**Self-inflicted, all in the CosMx runner:**

6. `reconcile_barcodes` needs `--obs-class`; I read its arguments off a grep of
   `add_argument` calls, which missed a required one.
7-9. I wrapped `finalize_collection` in join / assign_uids / stamp calls it
   already makes, having taken the Xenium `materialize_bare_obs` bracket as the
   general pattern. It is not: that bracket exists because
   `materialize_bare_obs` is a somics-specific bridge with no polycomb
   equivalent. Reading the orchestrator's import block would have settled it.

**Re-ingesting a package silently duplicates it.** This is the worst failure
mode found so far, because the run succeeds and the obvious health check passes.

`ingest_collection(skip_existing=True)` decides it has seen a dataset before by
`dataset_uid` — which `make_uid()` generates fresh on every build, so a rebuilt
package never looks familiar. Its `section_uid` values *are* stable content
hashes, so both copies land on the same section and merge:

```
LIBD_151507 obs rows:  8,452   (published: 4,226)
LIBD dataset rows:        48   (expected: 24)
sections in registry:     59   <- unchanged
```

Section count is the number everyone checks, and it does not move. Only a row
count against a known value exposes it.

`python -m somics.ingest` now refuses when any incoming `section_uid` is already
in the atlas, with `--allow-existing-sections` as the escape hatch for when the
previous datasets have genuinely been removed. **Build an atlas in one pass from
a fixed set of packages**; if a package is rebuilt, rebuild the atlas.

**The resume trap.** `ingest_collection(skip_existing=True)` checks the dataset
uid, not whether that dataset is *complete*. A crashed ingest leaves rows
written and a dataset record missing; the next run skips the dataset as present
and then fails in the post-ingest CSC step with `No dataset record found for
zarr_group=...`. Wipe the atlas and re-ingest rather than resuming onto a
partial write. This will matter much more at 20 TB, where a mid-run failure is
likely rather than hypothetical.

## Verifying a rebuild

`scripts/verify_rebuild_matches_atlas.py --rebuilt <path>`, three tiers:

- **tier 1, structural** — sections, row counts, uids
- **tier 2, content** — obs joined on `source_obs_id`, every column compared
- **tier 2b, measurements** — expression and image crops over aligned rows

Three things must be aligned before any value is compared, and getting each
wrong produces a confident, entirely false failure — all three happened:

1. **Rows.** `limit(n)` from two atlases returns the same cells in different
   physical order. Select an explicit `source_obs_id` set and sort both sides.
2. **Columns.** Feature order follows the atlas's own registration, so the
   published corpus (33,772 features across 59 sections) orders the same 541
   Xenium features differently from a two-section rebuild. Sort by feature uid.
3. **Scope.** Comparing a global feature registry against a two-section one is
   meaningless. Compare the section's own `var` axis.

And know which ids are reproducible: **stable uids are**, because they hash a
natural key — `make_stable_uid("Xenium_Lung_Preview_non_diseased")` is
`b078a2710ca256ef` in both. **obs `uid` and `dataset_uid` are not**; they come
from `make_uid()`, which is `uuid4`. Never compare them.

## Running it on EC2

The pattern, which also documents itself in `docs/ec2_raw_staging_runbook.md`:
launch with `--instance-initiated-shutdown-behavior terminate`, no public IP, no
key pair, SSM only, and a `systemd-run --on-active=8h /sbin/shutdown` backstop so
a lost session cannot leave the box running. Drive it with base64'd scripts over
`AWS-RunShellScript`; a multi-line command sent as a plain SSM parameter gets its
newlines collapsed.

Two things worth knowing about throughput. **10x's CDN serves ~0.3 MB/s** to us
regardless of user agent, against 16 MB/s from S3 — fetch vendor bundles on an
EC2 box into `s3://somics-dev/rebuild/` and pull from there. And **selective
extraction matters**: an 18.42 GB Xenium bundle yields 1.3 GB of files the
builder actually reads, because `transcripts.parquet` is the bulk and is unused.
