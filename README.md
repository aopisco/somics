# somics

Tools for working with spatial omics datasets.

`data/st_corpus.csv` holds the sample corpus of spatial transcriptomics datasets and their metadata.

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
