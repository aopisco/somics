# somics × DCA spec — gap analysis and a proposed division of labor

*2026-09-02 · for Aofei (@aliu) · written against DCA specs v0.2 (repo at 0.2.8)
and the worked example at
`s3://czi-dynamic-cell-atlas-staging/spatial_transcriptomics/xenium/xenium-breast-biomarkers-s1-top-ffpe/`*

## Context

somics maintains a registry of 5,585 spatial omics datasets, a ~20 TB raw
corpus in `s3://somics-dev`, and a queryable cross-dataset atlas (Lance +
zarr v3) that we have just finished rebuilding byte-identically from source.
The question this doc answers: **what somics would need to change for its
image data ingestion to follow the DCA spec**, based on reading the spec and
inspecting both your example dataset and our own freshly rebuilt store.

## Where somics stands against the array standard

Measured from a real H&E array in our atlas (13332 × 13332 × 3), not from the
code:

| DCA v0.2 requirement | somics today | verdict |
|---|---|---|
| Zarr v3 | Zarr v3 | ✓ |
| uint8/uint16 raw, uint32 labels | uint8 | ✓ |
| zstd/LZ4/Blosc, level ≤ 3 | zstd, sharded | ✓ |
| 5D `t,c,z,y,x`, channel-first | 3D `y,x,c` | reshape needed |
| channel chunk size = 1 | RGB interleaved (512×512×**3**) | rechunk needed |
| multiscale pyramid, one LOD ≤ 2048, mean filter | single level, base 13332 | pyramid needed |
| OME-NGFF 0.5 `multiscales` + axes + µm units | `attributes: {}` — none | write it (we hold µm/px in our section-image table) |
| `dca.channels` per-channel semantics | channel names live in our Lance registry, not the zarr | mirror in; our data maps cleanly (H&E → `chromogenic`, morphology/AF → `fluorescence`/`labelfree`) |
| `dca.normalization_statistics` | none | compute at ingest (`dca_helpers` covers it) |
| experimental-metadata floor + sibling Parquet w/ per-field provenance | equivalent facts in Lance tables; NCBITaxon/UBERON/MONDO already resolved | mapping exercise + new provenance capture |
| `perturbation` | none in the 59-section atlas | **per-dataset judgment — never a default.** See below. |

The plumbing is closer than expected; the metadata layers are where the work
is.

**On `perturbation` specifically** (per Aofei's review): the spec makes the
block conditionally required and says outright that whether a dataset "has
perturbations" is a judgment about the experiment, not a machine-checkable
property of the store. The 59 sections in our atlas happen to have none, but
spatial datasets with CRISPRi guides, drug treatments, or other perturbational
designs exist (spatial CRISPR screens, Perturb-map-style experiments), and for
those the `PerturbationAssignment` block must be filled — definition, dosing,
control class, timing. So the ingest rule is: **check every dataset for
perturbational treatment before omitting the block.** "The datasets so far had
none" is an observation, not a policy.

## The finding that reframes the question

Your staging bucket already holds **~80 Xenium datasets ingested to DCA**,
including the exact sections in the somics atlas
(`xenium-preview-human-non-diseased-lung-with-add-on-ffpe`,
`…-lung-cancer-with-add-on-2-ffpe`, `xenium-v1-hcolon-cancer-add-on-ffpe`)
and, at a glance, most of the 10x catalogue that makes up somics' queued
44-dataset Xenium block. Your ingest is also richer than ours in places: it
takes the full `outs.zip` and keeps the 14-plane 3D morphology stack and
per-transcript cell assignments that selective extraction drops.

So for Xenium the answer to "what do we change" may be **nothing — we should
consume rather than rebuild**. Double-ingesting the same 10x bundles into two
CZI buckets in two formats is the outcome to avoid.

## Proposed division of labor

1. **Xenium (and any future overlap): somics consumes DCA staging.** Either as
   the fetch source (in-region S3 vs 10x's measured 0.3 MB/s CDN) or, better,
   treating the DCA store as the canonical image home, with our atlas's
   section-image registry pointing into it rather than re-encoding TIFFs.
2. **HuBMAP Histology + Auto-fluorescence (~1,119 staged datasets, ~8 TB) and
   the proteomics imagery (CODEX/PhenoCycler/MIBI/Cell DIVE, ~590): somics
   implements the spec.** Nothing in DCA staging covers tissue-scale
   consortium imagery. The adapter was on our roadmap anyway; we would emit
   DCA-conformant stores, reuse `dca_helpers` (writer, normalization stats,
   validator in the pipeline), and map our already-resolved ontology terms
   onto the metadata floor. This could become the largest single contribution
   of tissue imagery to the DCA corpus.
3. **The somics atlas stays as-is.** It is the cross-dataset query layer
   (Lance obs + crop pointers); the DCA layout is the per-dataset
   archival/training format. Complementary, not competing — the atlas can
   reference DCA stores.

## Details we'd want to align on

- **The H&E alignment convention.** Your `dca.he_alignment` +
  `shares_coordinate_space_with_xenium: false` handling (OME-NGFF having no
  rotation transform) is exactly the trap our crop pointers care about — we
  place image crops in the expression frame. If we consume your stores we
  adopt your convention; worth a short conversation so neither side overlays
  wrongly-by-90° imagery.
- **Version pinning.** v0.2 is marked WIP and the example is built against it.
  Before somics writes 1,100+ datasets to the spec we'd want to pin a version
  and know the migration story.
- **Ownership going forward.** Who ingests newly published 10x datasets, and
  where does the somics registry point its `download_url` for datasets that
  exist in DCA staging?

## Questions for you

1. Is DCA the *required* landing format for somics' HuBMAP imagery ingest, or
   a strong recommendation?
2. Can somics read (and build on) `czi-dynamic-cell-atlas-staging`
   long-term, or should overlapping datasets be mirrored?
3. Is there interest in the HuBMAP tissue imagery for DCA training, and if
   so, does 2D+channels tissue data (no time axis) fit the corpus you want?
4. Which repo owns the ingestion pipeline behind the breast-biomarkers
   example (`scripts/ingest.py`, `dca_writer`)? We'd rather extend it than
   parallel-build.
