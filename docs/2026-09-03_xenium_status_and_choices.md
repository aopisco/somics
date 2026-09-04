# somics × the DCA Xenium corpus: what we have, what overlaps, and the choices ahead

*2026-09-04 · for the imaging-team colleague who ingested the Xenium catalogue
into DCA staging · companions: `docs/2026-09-02_dca_spec_alignment.md` and the
bundle-level mapping in `data/xenium_dca_overlap.csv`*

## What somics is

somics is a spatial omics data effort with three layers:

- **A dataset registry** — 5,585 datasets, one row per dataset keyed to the
  publication that first released it, built from a literature sweep plus the
  HuBMAP portal.
- **A raw corpus** — ~20 TB staged in `s3://somics-dev` with per-dataset
  provenance manifests (literature bundles, the HuBMAP tier-2 datasets, and
  the 10x catalogue's verified bundle URLs).
- **A queryable atlas** — 59 tissue sections / ~2.47M spatial units in
  Lance + zarr v3, reproducible from source: 10x Xenium lung and colon
  previews, CosMx NSCLC (8 sections), LIBD Visium DLPFC (12 sections), and
  the Monkman NSCLC CODEX cohort, all browsable in a 3D viewer.

The three layers are one funnel, and the vision is to run everything through
it: **every dataset the registry lists ends up queryable in the atlas** — all
5,280 spatial datasets, not a curated subset. The 59 sections are the seed
that proved the pipeline reproduces published data exactly; the staged corpus
is the raw material; and ingestion scales by source *layout* rather than by
dataset, so each new builder or adapter unlocks a whole family at near-zero
marginal cost. The sequencing follows that economics: the 10x catalogue next
(175 datasets, builders already proven), then HuBMAP's pipeline-uniform
imagery and proteomics (~1,900 staged), then the literature long tail, where
every deposit is its own layout and per-dataset curation is the honest price.
The end state is one place to ask "what does this gene / protein / structure
look like across every spatial dataset ever published" — registry as the map,
corpus as the warehouse, atlas as the interface.

The atlas schema (`schema/spatial_omics_atlas_schema.yaml`) currently covers
three modalities: **spatial transcriptomics** (gene expression — Xenium,
CosMx, Visium/Visium HD), **spatial proteomics** (protein abundance — CosMx
protein, CODEX), and **imagery** (H&E and morphology as a `discrete_image`
feature space, addressed by per-cell crop pointers in the expression
coordinate frame). Spatial metabolomics (MALDI) has no feature space yet and
is deliberately deferred.

## What we found when we read the DCA spec

While assessing the imaging team's DCA spec for our image ingestion, we mapped
your Xenium staging area
(`s3://czi-dynamic-cell-atlas-staging/spatial_transcriptomics/xenium/`)
against our registry, bundle by bundle (`data/xenium_dca_overlap.csv`):

- **68 of our 69 verified Xenium bundles are datasets you have already
  ingested** — including the three Xenium sections in our atlas and
  effectively our whole queued 44-dataset Xenium block.
- **The 1 you don't have**:
  `Xenium_V1_Human_Clear_Cell_Renal_Cell_Carcinoma_FFPE_Protein` (29.9 GB).
- Your copies are richer than ours in places: you take the full `outs.zip`
  where several 10x pages only link the Explorer subset, keeping the 3D
  morphology stacks, per-transcript cell assignment, and secondary analysis
  that our selective extraction drops.
- **Visium/HD doesn't overlap**: your staging has none, and 130 of our 175
  queued datasets are Visium/HD — that block is unaffected.

We had independently fetched a subset of the same bundles (they built and
verified our atlas), so between us the 10x Xenium catalogue has been
downloaded roughly twice. The point of this doc is to stop that pattern before
it extends to the next several hundred datasets.

## The choices ahead

1. **Source of truth for Xenium raw.** Can somics get durable read access to
   `czi-dynamic-cell-atlas-staging` and treat your stores as canonical, with
   our `raw/` copies demoted to a cache? Until that's answered we delete
   nothing on our side.
2. **What our atlas ingests from.** Our builders currently read 10x bundle
   layouts. For the Xenium queue we could instead read your already-built
   stores (`sdata.zarr` + the image zarrs), skipping fetch and extraction
   entirely — small new reader code against a v0.2 spec that is still marked
   WIP, traded against ~1 TB of duplicate fetching.
3. **The one missing dataset.** Is the ccRCC protein bundle queued on your
   side? If yes, we do nothing; if no, we fetch it and can hand you the copy.
4. **Division of labor for imagery DCA doesn't cover.** We hold ~1,119 staged
   HuBMAP Histology/Auto-fluorescence datasets and ~590 proteomics-imaging
   datasets (CODEX/PhenoCycler/MIBI/Cell DIVE). We propose somics implements
   the DCA spec for those — reusing your `dca_helpers` writer, normalization
   stats, and validator rather than parallel-building — which would land a
   large tissue-imagery contribution in your format. Open question whether 2D
   tissue imagery (no time axis) fits the corpus you want.
5. **Newly published 10x datasets.** Who watches the catalogue and ingests new
   releases going forward — one of us, not both.

## One integration detail we'll need to handle together

Our atlas's image crops live in the **expression coordinate frame**; your
stores keep the post-Xenium H&E in its own pixel grid with the 10x affine in
`dca.he_alignment` (since OME-NGFF 0.5 can't express rotation). Any
consume-your-stores path on our side must apply that transform — we've noted
it so nobody overlays naively.

## Contacts and artifacts

- Gap analysis against the DCA spec: `docs/2026-09-02_dca_spec_alignment.md`
  (shared with Aofei, whose review is folded in)
- Bundle-level overlap mapping: `data/xenium_dca_overlap.csv`
- somics repo: `aopisco/somics` (registry, schema, builders, this doc)
