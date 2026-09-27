# Stereo-seq adapter -- sources, layouts, decisions

*Started 2026-09-17, smoke 2026-09-22. Companion to the MERFISH and seqFISH notes.*

## What is fetchable

Of 69 Stereo-seq registry rows, most point at CNGB/STOmicsDB project pages
that need an account (the "link but nothing fetchable" cluster), the GITomicsDB
fish (also non-Ensembl species), or GSA-Human. Fetchable today:

| source | sections | form | notes |
|---|---|---|---|
| **MOSTA** (`ftp.cngb.org/pub/SciRAID/stomics/STDS0000058`, public HTTPS) | 53 embryo (E9.5-E16.5) + adult brain hemisphere + 2 olfactory bulb | bin1 GEM, 0.5-2.8 GB each | files are named `.tsv.gz` but the embryo ones are **plain text** (magic `ge`); the adult brain one is gzip. Sniff, never trust the name. |
| GSE298650 (staged RAW tar, 33.7 GB) | 5 mouse heart (P0, P7 x2, adult x2) | `TissueCut.gem.gz` + registered ssDNA `tif.gz` | the only source with imagery |
| GSE256319 (staged, 12.9 GB) | 1 mouse brain | SAW 3.0.2 `counts.gef.h5` | GEF bin1 |
| GSE274447 (staged, 2.1 GB) | 3 mouse hippocampus | `adjusted.cellbin.gef` | cells |
| SpatialGlue bundle (staged zip) | 4 mouse thymus | RNA + ADT h5ads, bin100, DNB coordinates | **Stereo-CITE-seq**; the bundle's spleen is SPOTS (Visium + ADT, 10x barcodes) and its brain is spatial ATAC/CUT&Tag-RNA |

Not spatial despite the registry: GSE267315 (snRNA-seq matrices; the Stereo-seq
is on GSA-Human HRA010703), GSE176078 (Wu 2021 breast scRNA-seq). The Dropbox
bin60 h5ads (qiu2022/qiu2024) are the MOSTA adult brain re-binned. The ~10
olfactory-bulb rows are SEDR-tutorial copies of the two MOSTA sections.

## Decisions

- **Unit = a square bin of DNBs, the Visium HD shape**: `spatial_unit bin`,
  `segmentation_method grid`, `unit_size_um = bin_dnb * 0.5` (500 nm DNB
  pitch). **Default 20 DNB = 10 um** (HD is at 8 um; MOSTA's own analysis used
  bin50 = 25 um). Cellbin GEFs are ingested as cells with the lab's centroids
  and areas. Bin centres are `(bin_index + 0.5) * bin_dnb` in DNB units.
- **Feature axis = every gene with a MID in the section** (untargeted; symbols,
  so `ensembl_gene_id` stays null and the panel is "whole transcriptome").
- **Images only where published** (GSE298650); the registered TIFF is taken as
  1 px = 1 DNB and the builder refuses if fewer than 95% of bins fall inside.
- **Stereo-CITE ADT** is a `protein_abundance` space on the same bins; the
  shared MERFISH runner gained the co-detection branch (reconcile + finalize).
- `STEREO_SEQ` added to the schema's technology enum; corpus index labels it
  a capture assay (no negative probes).

## Verified locally

The GEM path on 831k real MOSTA E9.5 rows: 34,719 bins of 10 um, 2,085 genes;
total counts equal the source MIDCounts, a hand-aggregated bin matches
n_counts/n_genes exactly, gene column sums match the source per gene, and the
categorical gene reader (a 100M-row embryo as Python strings is >6 GB) changes
nothing. The GEF, cellbin, h5ad and image paths are exercised first on EC2.

## Runs

- **Smoke** 2026-09-22 17:38Z (`somics-stereoseq-smoke`, r5.4xlarge, throwaway
  atlas from the rebuild base): the two MOSTA olfactory-bulb sections.
- **Production**: `ingest_tenx_xenium_ec2.sh` with `SOMICS_FAMILY=stereoseq`,
  `SOMICS_BUILDER=scripts/build_stereoseq_package.py`,
  `SOMICS_RUNNER=scripts/run_merfish_pipeline.sh`,
  `SOMICS_BUILD_SCRIPT=scripts/build_stereoseq_package.py`,
  `SOMICS_SPEC_DIRS=specs/stereoseq`, `SOMICS_RAW_INCLUDE="*"`, from the
  newest `_DONE` prefix (seqFISH's). Expect ~70 sections; the embryo block is
  the bulk (E16.5 sections are ~2.8 GB of GEM each).

## Production run 1 (2026-09-22 19:03Z, `ingest/stereoseq/atlas/2026-09-22T19-03-01Z`)

17 of 20 packages in on the first pass; three skips, each a real property of
the source, each fixed on the branch for the follow-up:

- `chen2022_mosta_stereoseq__E15.5_embryo2`: a different MOSTA export (gzip,
  internal name `E15.5_HC21_filter.txt`) with a header line repeated inside
  the data -> the GEM reader now finds the header wherever it sits and drops
  rows whose coordinates are not integers, reporting how many.
- `stereo_seq_datas2024_stereo` (hippocampus cellbin GEFs): built 229,775
  cells, then the harmonizer's keyed merge hit a repeated gene symbol in the
  GEF gene table -> duplicated gene columns are summed into one.
- `stereo_seq_datas_stereo_3` (GSE298650 heart): four of five chips place on
  their registered ssDNA TIFF at 1 px = 1 DNB (>=95% of bins inside); on
  `FP200000337BR_B3` only 71% do -- the image is 12,451 px wide while bins run
  to x = 17,028 DNB, and neither the GEM (no `#Offset` lines) nor the ImageJ
  metadata carries an origin. That chip is ingested expression-only with the
  reason in `additional_metadata`; the other four keep their images.

GEO brain GEF (`stereo_seq_mouse_stereo_2`) and the Stereo-CITE thymus
(RNA bins + 51 ADT as `protein_abundance`) went through first time, so the
GEF-bin, h5ad-bins and co-detection paths are validated on real data.
