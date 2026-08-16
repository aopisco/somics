# somics

Tools for working with spatial omics datasets.

📊 [Team slide deck](https://docs.google.com/presentation/d/1JAx5EtSJxe9Zc3rZCMGvH3-RoAFuo6U96toq0wJD7nE/) — project overview and paperclip demo (see also [docs/demo](docs/demo)).

`data/st_corpus.csv` holds the sample corpus of spatial transcriptomics datasets and their metadata
(this matches the TERRA supplementary table, doi:10.64898/2026.07.29.741565).

`data/literature_datasets.csv` is a literature-derived inventory of spatial omics datasets (1,028 rows),
compiled from a paperclip full-text search of 983 papers on spatial transcriptomics/proteomics plus the
dataset inventories of the TERRA, VirTues (spora corpus), and KRONOS foundation-model papers. Each row is
one dataset as reported by one source paper (claim-level): platform, modality, species, tissue, disease,
sample count, data access link, whether the paper generated or reused it, and the source paper's
title/DOI/year. This is the raw extraction that feeds the curated tables below.

`data/datasets.csv` is the curated dataset registry: **one row per dataset**, referenced by its
**original publication** — the paper that first released the data. When data debuted in a model paper
(e.g. TERRA's in-house Xenium pancreas), that model paper is the original reference
(`first_published_by_model_paper = yes`). Vendor datasets (10x, Bruker) carry the vendor page as their
reference. Rows are resolved by tracing each analyzing paper's dataset table to the cited original
publication (see `docs/`).

`data/model_dataset_usage.csv` tracks which model papers use which datasets (many-to-many):
model, dataset_id, usage type (pretraining / benchmark / analysis), and the dataset's alias in the
model paper (e.g. DRIFT's "10xHPC" = the spatialLIBD DLPFC dataset).

## Data access

The atlas is hosted publicly on Cloudflare R2. The credentials below are
read-only and intentionally published — no setup or account is needed.

```python
from homeobox import RaggedAtlas

ATLAS_DIR = "s3://epiblast-public/somics_spatial_atlas"

STORE_KWARGS = {
    "config": {
        "endpoint": "https://61be05560bebc4714cdd9913fb075bc9.r2.cloudflarestorage.com",
        # Read only public credentials for R2
        "aws_access_key_id": "087ee61ad71e3fc431f7c8031545c4e4",
        "aws_secret_access_key": "3c94e43945c4e49a466930527f368756810315f68ad26a2c10c8adac2ed08b8d",
        "aws_region": "auto",
    }
}

atlas = RaggedAtlas.checkout_latest(ATLAS_DIR, store_kwargs=STORE_KWARGS)
```

Rows live in the `SpatialObs` table, one per spatial unit (a cell, nucleus, or
spot depending on the platform), with physical coordinates in `x_um`/`y_um` and
pixel coordinates in `x_px`/`y_px`. Filter with `where()`, then materialize the
modality you want:

```python
query = atlas.query().where("tissue == 'colon'").limit(8)

# Obs metadata as a polars DataFrame.
obs = query.to_polars()

# Counts as AnnData. select_fields is required whenever the atlas carries more
# than one AnnData-capable feature space.
adata = query.select_fields("gene_expression").to_anndata()

# Image crops centred on each unit, one ndarray per row under the "raw" layer.
crops = query.to_spatial_batch("morphology_crop").layers["raw"]
```

Imagery is stored once per section at full resolution; each obs row addresses a
128x128 px box into it, slid inward at section edges so every crop is the same
shape and `np.stack(crops)` works directly. Crops are stored as `uint16` but read
back as `float32`. Use `he_crop` in place of `morphology_crop` for H&E, and check
the `has_he_crop` / `has_morphology_crop` flags before requesting either — not
every section carries both.

## Corpus builder

`web/` is the UI for assembling training corpora from the atlas: a chat bar that turns fuzzy intent
("subcellular colon cancer, transcripts/cell > 90") into a visible filter statement, a faceted
sidebar, and a card grid with QC surfaced on every dataset. It renders a precomputed index rather
than querying the atlas live.

```bash
uv run python scripts/build_corpus_index.py   # atlas on R2 -> data/corpus_index.json
uv run python -m somics.viewer                # API on http://127.0.0.1:8787
cd web && npm install && npm run dev          # UI on http://127.0.0.1:5274
```

Rerun the index script after every ingest — it is a snapshot, and nothing warns you when it goes
stale. See [web/README.md](web/README.md) and
[docs/2026-08-15_corpus_builder_ui_mapping.md](docs/2026-08-15_corpus_builder_ui_mapping.md) for
what the atlas can and cannot tell the UI, particularly on QC.

## Viewer

`viewer/` is a 3D browser view of the atlas: a low-resolution voxel rat, human or zebrafish standing
in a photographic alpine valley, with a pin on every organ the atlas holds data for. Clicking a pin
selects that organ's samples. The measured data — every cell or spot as a 2D plot, plus the section's
H&E or morphology imagery — is drawn in a floating panel over the scene rather than in it, on the
grounds that a tissue section is a flat thing. Every view is a URL, down to the panel's position and
size, so a copied link reopens exactly what you were looking at, and an agent can drive the UI over
HTTP while you watch.

```bash
uv run python -m somics.viewer            # API on http://127.0.0.1:8787
cd viewer && npm install && npm run dev   # UI on http://127.0.0.1:5273
```

See [viewer/README.md](viewer/README.md) for the URL format, the agent control surface, and the
performance notes.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/aopisco/somics.git
cd somics
uv sync
```

This creates a `.venv` and installs the project along with the dev tooling.

## Development

Install the pre-commit hooks once after cloning:

```bash
uv run pre-commit install
```

Lint and format manually:

```bash
uv run ruff check .
uv run ruff format .
```
