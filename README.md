# somics

Tools for working with spatial omics datasets.

`data/st_corpus.csv` holds the sample corpus of spatial transcriptomics datasets and their metadata
(this matches the TERRA supplementary table, doi:10.64898/2026.07.29.741565).

`data/literature_datasets.csv` is a literature-derived inventory of spatial omics datasets (1,028 rows),
compiled from a paperclip full-text search of 983 papers on spatial transcriptomics/proteomics plus the
dataset inventories of the TERRA, VirTues (spora corpus), and KRONOS foundation-model papers. Each row is
one dataset as reported by one source paper: platform, modality, species, tissue, disease, sample count,
data access link, whether the paper generated or reused it, and the source paper's title/DOI/year.

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
