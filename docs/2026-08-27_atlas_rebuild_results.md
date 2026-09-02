# Rebuilding the hackathon atlas: results

*2026-08-27 · the outcome of the correctness gate, and a defect it found in the original*

## The question

Can the current toolchain reproduce the 59-section atlas built at the hackathon?
Asked because the published atlas is the only ground truth available, and a
pipeline regression in new data is indistinguishable from a quirk of the new
data. Everything here was run on one EC2 box; nothing depends on a laptop.

## Answer

**Yes. 58 of 59 sections reproduce exactly. The 59th differs because the
published atlas is wrong and the rebuild is right.**

| family | sections | result |
|---|---:|---|
| Monkman NSCLC CODEX | 36 | exact |
| CosMx NSCLC | 8 | exact |
| LIBD DLPFC Visium | 12 | exact |
| Xenium lung preview | 2 | exact |
| Xenium colon preview | 1 | **rebuild correct, published defective** |

Comparison is `scripts/verify_rebuild_matches_atlas.py`: structure, then every
obs column joined on `source_obs_id`, then expression, protein and image crops
over aligned rows. Not a spot check.

## The defect in the published colon section

The published `hColon_Cancer_Add_on_FFPE` has its **gene axis misaligned**: the
right counts attached to the wrong genes.

Measured against the source `cell_feature_matrix.h5` — the actual truth, not the
other atlas — over 40 cells:

```
hColon_Cancer_Add_on_FFPE   published 1041/1833   rebuilt 1833/1833
LIBD_151507                 published 35783/35783 rebuilt 35783/35783
Xenium_Lung_Preview_non_d.  published  2063/2063  rebuilt  2063/2063
```

**57% of the published colon's nonzero values sit on the wrong gene.** Every
other family agrees with its source perfectly, on both sides.

It is invisible to every cheap check. Per-cell totals are identical (53 = 53),
sorted per-cell vectors are identical, row counts and uids match. Only comparing
gene-by-gene against the source exposes it, which is why the diff had to be
strict to be worth running at all.

Two facts about which sections are affected are worth putting together. The
colon section is one of the two that **never had a committed builder** — built
conversationally at the hackathon, method never written down. The other such
family is LIBD, which is clean. So this is not "uncommitted builders are
unreliable"; it is one section, one mistake, undetectable without this exercise.

**The published atlas should not be treated as authoritative for that section.**

## What the gate caught along the way

Nine pipeline failures and four verifier bugs. The verifier bugs matter as much
as the pipeline ones, because each produced a *confident false result*:

- comparing a global feature registry against a two-section rebuild
- comparing rows without aligning them — `limit(n)` returns the same cells in
  different physical order
- comparing columns without aligning them — feature order follows registration,
  which depends on what else is in the atlas
- hardcoding `morphology_crop`, which crashed the whole run on the first Visium
  section and reported `0 failures` having made 0 checks

And two data-integrity traps now guarded in code:

- **`he_crop` vs `morphology_crop`.** `ingest` picks the pointer by joining
  `SectionImageSchema.dataset_uid` to the manifest; the assemblers never wrote
  `dataset_uid`, so every image took the `morphology_crop` fallback. Xenium,
  CosMx and Monkman passed anyway because that fallback is correct for DAPI and
  morphology stacks. Only a study with H&E exposed it.
- **Re-ingesting a package duplicates it.** See `CLAUDE.md`; `somics.ingest` now
  refuses on an overlapping section.

## What is now reproducible

Spec-driven builders, so a new dataset of a known platform costs a spec rather
than a script:

```
build_xenium_package.py    assemble_xenium_collection.py
harmonize_xenium_package.py  run_xenium_pipeline.sh
build_visium_package.py    assemble_visium_collection.py
harmonize_visium_package.py  run_libd_dlpfc_pipeline.sh
run_monkman_codex_pipeline.sh   run_cosmx_nsclc_pipeline.sh
specs/xenium_lung_preview.json  specs/xenium_colon_preview.json
specs/libd_dlpfc.json
```

The Xenium builder is proven equivalent to the hardcoded original: byte-identical
obs, var and geometry outputs for the lung pair.

Xenium takes four library tables to Visium's three — a targeted assay's panel is
part of its identity, a whole-transcriptome one has none.

## The atlas that was built

59 sections, ~2.47M obs rows, on `schema/spatial_omics_atlas_schema.yaml` — the
extended schema, so the corpus is already on the version that can hold spatial
ATAC. The only obs difference from the published atlas is the presence flag
`has_chromatin_accessibility`, which exists because that schema declares the
pointer. That is the extension working, not a defect, and the verifier should
learn to report it as such.

## What is not done

- The verifier still counts `has_chromatin_accessibility` as 59 failures. It
  should classify known schema-introduced columns separately.
- The published colon defect should be reported to @conradry, and the R2 atlas
  either corrected or annotated.
- Metabolomics has no feature space upstream; deferred deliberately.
- `join_feature_space_obs` / `stamp_uid_on_feature_space_obs` are exercised only
  through `finalize_collection`, which is the supported path.
