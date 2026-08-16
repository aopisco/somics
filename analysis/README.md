# analysis

Plots and summary statistics over the dataset inventory tables in `data/`.
Regenerate everything with:

```bash
uv run python analysis/table_stats.py
uv run python analysis/plot_datasets_by_organism_modality.py
uv run python analysis/plot_technologies_by_model_reuse.py
```

Outputs land in `analysis/plots/`. Requires matplotlib (`uv add --dev matplotlib`
if it isn't in the environment yet).

- `plot_datasets_by_organism_modality.py` — stacked bars of `datasets.csv` by
  organism, colored by modality. Modality is inferred from platform keywords
  where the `modality` column is blank; residual unknowns stay gray.
- `plot_technologies_by_model_reuse.py` — datasets per canonicalized technology,
  shaded by how many model papers use each dataset (1 / 2–4 / 5+, from
  `model_dataset_usage.csv`). Notable: ST (original) and Slide-seq are ~40%
  reused across models while GeoMx DSP sits at 0%.
- `table_stats.py` — headline row/distinct counts for the three tables.

Both charts use a colorblind-validated palette; canonicalization of organisms
and platforms is keyword-based, so counts can differ marginally from other
reports rolling up the same free-text fields.
