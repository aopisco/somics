# somics

Tools for working with spatial omics datasets.

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
