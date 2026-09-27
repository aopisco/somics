# analysis

Plots and summary statistics over the dataset inventory tables in `data/`.
Regenerate everything with:

```bash
uv run python analysis/table_stats.py
uv run python analysis/plot_datasets_by_organism_modality.py
uv run python analysis/plot_technologies_by_model_reuse.py
uv run python analysis/plot_models_by_dataset_technology.py
uv run python analysis/plot_datasets_by_year.py
uv run python analysis/plot_top_tissues.py
```

Outputs land in `analysis/plots/`. Requires matplotlib; it is not a project dependency, so either
`uv add --dev matplotlib` or prefix each command with `uv run --with matplotlib`.

- `plot_datasets_by_organism_modality.py` — stacked bars of `datasets.csv` by
  organism, colored by modality. Modality is inferred from platform keywords
  where the `modality` column is blank; residual unknowns stay gray. A dataset
  covering more than one organism is counted under each, so bars sum to more
  than the dataset count — a combined "Human & Mouse" bar was less useful.
- `plot_technologies_by_model_reuse.py` — datasets per canonicalized technology,
  shaded by how many **named** model papers use each dataset (none / 1 / 2–4 /
  5+, from `model_dataset_usage.csv`).

  Literature rows only: HuBMAP's 3,948 rows are cited by one named usage row
  in total, so they are excluded rather than drawn as a wall of grey.
  The no-model group is the point of the chart: 1,690 of 2,016 literature
  datasets (84%, 2026-09-27) have no named model using them. (The hackathon
  version said 1,567 of 1,822; its legend double-counted the technologies
  folded into Other, fixed since.) It needs the "named" qualifier because
  every dataset has at least one usage row by construction — the paper that
  reported it becomes one — so a usage row only counts when its `model` field
  holds a real model name (TERRA, VirTues, Thor…) rather than the fallback
  paper id the merge writes for a plain analysing paper. That heuristic keys
  off a `Name:` prefix in the paper title, so a model paper titled without one
  is undercounted.

  Notable: Slide-seq (13%) and ST (8%) lead on reuse (2+ named models), while
  GeoMx DSP and Visium HD sit at 0%.
- `plot_models_by_dataset_technology.py` — which models use which datasets:
  the 30 most-used named models, each bar the datasets its paper uses,
  segmented by technology. 107 named models, 547 model×dataset links. The
  title-prefix heuristic admits named resources too (SPASCER, SpatialDB and
  CellMap are databases, not models).
- `plot_datasets_by_year.py` — datasets by the year of their *original*
  publication. Recent years are undercounted: paperclip's bioRxiv ingestion lags
  publication by roughly three months.
- `plot_top_tissues.py` — most represented tissues, split by modality. Tissue is
  free text, so unmatched labels are left out of the chart rather than bucketed.
- `table_stats.py` — headline row/distinct counts for the three tables.

Both charts use a colorblind-validated palette; canonicalization of organisms
and platforms is keyword-based, so counts can differ marginally from other
reports rolling up the same free-text fields.
