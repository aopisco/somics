# analysis

Plots and summary statistics over the dataset inventory tables in `data/`.
Regenerate everything with:

```bash
uv run python analysis/table_stats.py
uv run python analysis/plot_datasets_by_organism_modality.py
uv run python analysis/plot_technologies_by_model_reuse.py
```

Outputs land in `analysis/plots/`. Requires matplotlib; it is not a project dependency, so either
`uv add --dev matplotlib` or prefix each command with `uv run --with matplotlib`.

- `plot_datasets_by_organism_modality.py` — stacked bars of `datasets.csv` by
  organism, colored by modality. Modality is inferred from platform keywords
  where the `modality` column is blank; residual unknowns stay gray.
- `plot_technologies_by_model_reuse.py` — datasets per canonicalized technology,
  shaded by how many **named** model papers use each dataset (none / 1 / 2–4 /
  5+, from `model_dataset_usage.csv`).

  The no-model group is the point of the chart: 1,567 of 1,822 datasets
  (86%) have no named model using them. It needs the "named" qualifier because
  every dataset has at least one usage row by construction — the paper that
  reported it becomes one — so a usage row only counts when its `model` field
  holds a real model name (TERRA, VirTues, Thor…) rather than the fallback
  paper id the merge writes for a plain analysing paper. That heuristic keys
  off a `Name:` prefix in the paper title, so a model paper titled without one
  is undercounted.

  Notable: Slide-seq (11%) and smFISH/ISH (9%) lead on reuse, while GeoMx DSP
  and mass spectrometry sit at 0%.
- `table_stats.py` — headline row/distinct counts for the three tables.

Both charts use a colorblind-validated palette; canonicalization of organisms
and platforms is keyword-based, so counts can differ marginally from other
reports rolling up the same free-text fields.
