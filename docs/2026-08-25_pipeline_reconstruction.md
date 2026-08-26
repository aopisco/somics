# Reconstructing the polycomb pipeline scripts

*2026-08-25 · how the five missing scripts were recovered, what evidence each
convention rests on, and what is still unverified*

## The situation

`scripts/run_xenium_lung_pipeline.sh` is the whole ingest chain, and three of
its seven steps shelled out to scripts that existed only on the hackathon box:

```
/home/ubuntu/.claude/skills/prepare-package-for-resolution/scripts/
    stage_lance_tables.py
    stage_library_table.py
    stage_dataset_table.py
/home/ubuntu/.claude/skills/schema-harmonization/scripts/
    apply_resolution_pass.py
/home/ubuntu/.claude/skills/finalize-tables/scripts/
    finalize_collection.py
```

They are Claude *skills*, so @conradry's release of homeobox 0.2.9 and polycomb
0.0.3 on 2026-08-25 — which fixed the import wall — did not include them.

I had previously argued against reconstructing these, on the grounds that the
step order is load-bearing and a wrong reimplementation would fail as bad data
rather than as an error. Two things changed that judgement:

1. **`polycomb.util.finalization_order()` is a library function.** The ordering
   constraint is derived from the schema's own registry-key declarations, not
   remembered. The part I thought was tribal knowledge is not.
2. **The published atlas is ground truth.** A wrong reconstruction is now
   *detectable*. That is the condition that was missing.

## Every convention, and the evidence for it

Nothing below was invented. Each row is something observable in code we already
have, which matters because a plausible guess here produces a corpus that looks
fine and is wrong.

| convention | evidence |
|---|---|
| `<root>/lance_db/<Class>` holds collection-level registries; `<root>/<dataset>/lance_db/<Class>` holds per-dataset tables | `polycomb.util._lance_db_dirs` walks exactly these two shapes and reports the first with `dataset=None` |
| A Lance table is only seen if its name **exactly** equals a schema class name | `polycomb.util._class_for_table` does `table_name in class_names` and returns `None` otherwise — a typo is silently ignored, not an error |
| An obs table is named `<ObsClass>_<feature_space>` when its dataset has more than one feature space | stated in `scripts/materialize_bare_obs.py`'s docstring, which exists to bridge that exact naming |
| The target of a FK carries `<Target>_join`; each referrer carries `<field>_<Target>_join`; both hold the same natural key | `scripts/harmonize_*_registries.py` writes both sides and documents it: *"Every `RegistryKeyField` needs the natural key that links it to its target recorded under the `*_join` convention, on both sides, so that finalization can resolve it to a uid"* |
| Join keys are compared as stripped strings | `polycomb.util.join_key` exists for precisely this and says why: Arrow type variance (int vs str ids) must not cause a spurious miss |
| The dataset table has one row per feature space and only structural columns at staging time | `harmonize_*_datasets.py` `AddColumn`s the provenance onto it, and `AddColumn` onto an existing column is an error |
| `dataset_registry.csv` is *not* staged into `lance_db` | the assembler tags it `OTHER`, with a comment saying it is provenance for the harmonizer rather than a schema table |
| `obs_index` and `source_obs_id` come from the package builder, not staging | `build_xenium_lung_package.py` writes both into the obs CSV |
| feature space → feature registry class | `PointerField.declare(feature_space=..., feature_registry_schema=...)` on the obs class; `discrete_image` declares a space and no registry, so it contributes no var table |

## What each script does

### `stage_lance_tables.py`

Reads each dataset's OBS/VAR files out of `collection.json` and writes them to
`<dataset>/lance_db/` under the schema class they belong to. Stamps
`dataset_uid` on obs, because it comes from the manifest and nothing downstream
can recover it once the file has been read.

One non-obvious behaviour: a column that is entirely null is forced to string.
pandas types an empty column as `float64`, which then collides with a schema
field declared as a string. Staging has no business inferring a type from no
values, so it takes the widest one.

### `stage_library_table.py`

One registry CSV → one collection-level Lance table. The table name is passed
explicitly rather than derived, because `donor_registry.csv` does not name
`DonorSchema`.

### `stage_dataset_table.py`

Creates `<dataset>/lance_db/<DatasetClass>` with one row per feature space and
**only two columns**: `dataset_uid` and `feature_space`. Everything else is
deliberately absent — provenance belongs to the harmonizer, the summary fields
to finalization, and `zarr_group`/`layout_uid`/`created_at` to ingestion once
arrays actually exist. `discrete_image` gets a row like any other space: it has
no registry and no obs, but ingestion looks it up here.

### `apply_resolution_pass.py`

`--from-schema` reads which columns to resolve off the schema's own
`OntologyAlignedField` markers. It resolves EFO, NCBITaxon, UBERON, MONDO, CL,
HsapDv and PATO through `polycomb.ontologies.resolve_ontology_terms`.

It **skips** HANCESTRO (needs a custom resolver — the harmonizers do ethnicity)
and MmusDv (no `OntologyEntity` member), and reports every skip. A value that
does not resolve keeps its original text and is listed; writing a wrong CURIE
would be worse than leaving free text, because a CURIE reads as authoritative.

### `finalize_collection.py`

Walks `finalization_order(info)` and per class:

1. **Assigns `uid`** — `make_stable_uid` over the `StableUIDField` columns, or a
   random uid when the class declares none.
2. **Resolves incoming FKs** through the join-column pair.
3. **Materializes missing nullable schema columns** via
   `ensure_schema_columns_for_table`.

Then drops the `*_join` columns, and only then — the ordering the runner warns
about. Finally computes the dataset table's summary fields (`n_rows`,
`n_sections`, and the list-valued `organism`/`tissue`/`disease`/`assay`) from
the finalized obs.

**An unresolved FK raises rather than writing null.** A dangling foreign key
would surface much later as missing data with no obvious cause.

## Which uids are reproducible — measured, not assumed

This determines what "identical" can even mean, so it was checked against the
live atlas rather than reasoned about.

**Stable uids reproduce.** `make_stable_uid("hColon_Cancer_Add_on_FFPE")`
returns `183c734af72b51e0`, which is that section's `section_uid` in the
published atlas. The same holds for donors, panels and features — anything with
a `StableUIDField`:

| class | stable-uid field |
|---|---|
| `DonorSchema` | `donor_id` |
| `TissueSectionSchema` | `section_id` |
| `PanelSchema` | `panel_name` |
| `GenomicFeatureSchema` | `feature_key` |
| `ProteinSchema` | `protein_key` |
| `ImageFeatureSchema` | `feature_name` |
| `PublicationSchema` | `pmid` |

**Obs `uid` and `dataset_uid` do not.** They come from `make_uid()`, which is
`uuid4().hex[:16]`. I tested three plausible derivations against a real
published row (`uid = 4e4888461a62474d`, `source_obs_id = abdlfdhm-1`,
`dataset_uid = b28d6c6098ae42c4`, `section_uid = 183c734af72b51e0`):

| candidate | result |
|---|---|
| `make_stable_uid(source_obs_id)` | `5c26a6b4bfb2556c` — no |
| `make_stable_uid(dataset_uid, source_obs_id)` | `27c0c0fc8236595b` — no |
| `make_stable_uid(section_uid, source_obs_id)` | `6a0b6f6404e45a7f` — no |

So the verifier joins obs on `source_obs_id` and sections on `section_uid`, and
excludes `uid`, `dataset_uid`, `layout_uid` and `created_at` from comparison.
Comparing them would report a failure that is not one.

## Verification

`scripts/verify_rebuild_matches_atlas.py`, three tiers reported separately:

- **Tier 1, structural** — which sections exist, row counts, feature spaces,
  extents. Passing this alone is nearly worthless: it would pass with every
  expression value wrong.
- **Tier 2, content** — obs joined on `source_obs_id`, compared column by
  column; numerics within `atol=1e-6`, everything else exactly. **This is the
  tier that means something**, and the one we are holding.
- **Tier 3, provenance** — study, panel, donor, disease, accession link.

## Running it anywhere

Every package path in the repo was hardcoded to `/home/ubuntu`. All 19 affected
files now read `SOMICS_DATA_HOME`, defaulting to the old value so committed
paths still mean what they did.

## Sources, and a throughput trap

The two Xenium lung bundles are 18.42 GB and 30.66 GB at
`cf.10xgenomics.com/samples/xenium/**1.3.0**/…`. The colon bundle is under
**1.6.0** — guessing one version for both returns 403.

**10x's CDN serves us at ~0.3 MB/s** — measured at 294 kB/s with a browser UA,
272 kB/s with `curl/8.0`, 135 kB/s with none, so it is not user-agent gating.
S3 to the same machine runs at **16 MB/s**. The 30 GB bundle is therefore
fetched by a short-lived EC2 instance into `s3://somics-dev/rebuild/` and pulled
from there: ~35 minutes instead of ~28 hours.

Only six members of each bundle are needed — `cells.parquet`,
`cell_feature_matrix.h5`, `morphology_focus.ome.tif`, `experiment.xenium`,
`metrics_summary.csv`, `gene_panel.json`. Selective extraction turns 18.42 GB
into **1.3 GB** on disk; the bulk of the bundle is `transcripts.parquet`, which
the package does not use.

## What is still unverified

- **The scripts have not yet completed an end-to-end run.** Everything above is
  reconstruction and static evidence. Until the Xenium lung pair rebuilds and
  passes tier 2, treat the reconstruction as unproven.
- **Ontology drift.** Resolution is exact matching against a live OLS, so a term
  can resolve differently than it did at the hackathon. That would show as a
  tier 3 metadata diff on otherwise-correct data — record it, do not accept it
  silently.
- **Vendor revision.** If 10x has re-issued a bundle, the diff fails for a
  reason that is not our bug. Check the source before blaming the pipeline.
- **Two feature spaces with two obs tables** is untested here. Xenium lung has
  one obs and one image, which is the `materialize_bare_obs` path. CosMx has
  gene expression *and* protein, which needs `join_feature_space_obs.py` and
  `stamp_uid_on_feature_space_obs.py` — **two further skill scripts that are
  also missing**, named in `materialize_bare_obs.py` but not yet reconstructed.
  So the missing-script count is seven, not five; the extra two are not needed
  for the Xenium gate but are needed for CosMx and Monkman.
