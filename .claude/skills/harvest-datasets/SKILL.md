---
name: harvest-datasets
description: Run a Paperclip literature search, extract the spatial omics datasets each paper reports, and add them to data/literature_datasets.csv in the somics repo on a new git branch with a PR. Use this whenever the user wants to grow the dataset inventory from the literature — phrases like "search for X and add it to the inventory", "harvest datasets on spatial proteomics", "run another literature search", "add these papers' datasets to the CSV", or any request to expand, extend, or top up the literature dataset table. Also use when the user names a modality, platform, tissue, or disease and wants to know what datasets the literature reports for it, since the answer belongs in the inventory rather than in chat. Do not use for reading a single paper, or for st_corpus.csv, which is the TERRA sample corpus and is maintained separately.
---

# Harvest datasets from the literature

`data/literature_datasets.csv` is an inventory of spatial omics datasets as reported by the papers that describe or reuse them. It is a **claim-level** table: one row per (dataset × source paper). If three papers all reuse the Visium DLPFC data, that is three rows, not one. Each row records what a specific paper said about a specific dataset.

That design matters for how you add to it. The same dataset appearing again under a new source paper is a *wanted* row. The failure mode is re-mining a paper already in the table, which silently duplicates every row that paper contributed. So the dedup key is `source_paper_id`, not `dataset_name`.

Checking that key before extraction rather than after is also the cheapest thing you can do: the table already covers 204 papers, so any new search on adjacent vocabulary will overlap heavily, and every skipped paper is a `map` call you don't pay for.

## Schema

Fourteen columns, this exact order. `scripts/append_datasets.py` enforces it.

| Column | Notes |
|---|---|
| `dataset_name` | as the paper names it — don't invent a canonical name |
| `platform` | normalized against the controlled vocabulary (see below) |
| `modality` | `spatial transcriptomics` \| `spatial proteomics` \| blank if genuinely unclear |
| `species` | `human`, `mouse`, … |
| `tissue` | free text |
| `disease` | free text; blank for healthy or unstated |
| `n_samples` | integer or blank |
| `data_access_link` | accession URL, GEO/Zenodo/DOI |
| `origin` | `generated` (this paper produced it) or `reused` |
| `source_paper_title` | |
| `source_paper_doi` | |
| `source_paper_year` | |
| `source_paper_id` | Paperclip ID, e.g. `bio_ecfd39cd6dd6` — **the dedup key** |
| `found_via` | provenance tag, e.g. `literature_search`, `TERRA_paper` |

Blank is a legitimate value and is much better than a guess. A blank `disease` means the paper didn't say; a fabricated one is a claim the inventory can't support. Roughly a quarter of existing rows have blank `modality` for exactly this reason.

## Workflow

### 0. Preflight

Confirm a clean tree before touching anything — this workflow ends in a commit, and mixing it with unrelated work in progress makes the PR unreviewable.

```bash
cd ~/src/somics
git status --short          # expect empty
git checkout main && git pull
```

### 1. Search

```bash
paperclip searches --quiet --tag harvest-$(date +%m%d) -n 200 \
  "spatial proteomics imaging mass cytometry atlas" \
  "CODEX multiplexed imaging tissue dataset" \
  "MIBI-TOF spatial single cell"
```

`-n` on `searches` is per query with a default of 50 — three queries at 200 requests up to 600 rows before dedup. Note the `s_` result ID.

Query design matters more than `-n` here. The table is already saturated for common spatial transcriptomics phrasing, so a bigger pull on familiar vocabulary mostly returns papers you've mined. New coverage comes from new vocabulary — an unrepresented platform, a tissue nobody's searched, a proteomics term rather than a transcriptomics one.

### 2. Find which papers are actually new

```bash
paperclip results s_abc123 --save /tmp/hits.csv
python scripts/append_datasets.py --known --sheet data/literature_datasets.csv > /tmp/known_ids.txt
```

Compare the `id` column of `/tmp/hits.csv` against `/tmp/known_ids.txt`. Extract from the difference only, and tell the user the split — "62 hits, 18 new" is what tells them whether the query was worth running. A search returning zero new papers means the vocabulary is exhausted, not that the search failed.

### 3. Extract

Run `map` over the new papers, asking for the schema directly:

```bash
paperclip map --from s_abc123 \
  "List every spatial omics dataset this paper describes or reuses. For each one give:
   dataset_name (as the paper names it), platform, modality (spatial transcriptomics or
   spatial proteomics), species, tissue, disease, n_samples, data_access_link (accession
   or URL), and origin (generated if this paper produced the data, reused otherwise).
   Return nothing for a field the paper does not state -- do not infer. If the paper
   reports no datasets, say so explicitly."
```

Turn the output into a JSON array of row objects, one per dataset, and fill `source_paper_*` from the search results and `found_via` from the run.

`map` is an AI reader, so treat its output as a draft. Papers that report no datasets are common and should produce zero rows rather than a speculative one — a methods paper that mentions Visium in passing did not contribute a dataset.

### 4. Append on a branch

```bash
git checkout -b lit/spatial-proteomics-$(date +%Y-%m-%d)

python scripts/append_datasets.py \
  --rows /tmp/extracted.json \
  --sheet data/literature_datasets.csv \
  --found-via literature_search
```

The script refuses rows whose `source_paper_id` is already present, normalizes `platform` against the controlled vocabulary, validates column order, and prints a summary. If it rejects rows, read the reason before overriding — `--force` exists for the case where you deliberately want to re-mine a paper with a better prompt, and you should delete that paper's old rows first if so.

### 5. Commit, push, PR

```bash
git add data/literature_datasets.csv
git commit -m "lit: +37 datasets from 18 papers (spatial proteomics)"
git push -u origin HEAD
```

Then open the PR. If the `gh` CLI is available:

```bash
gh pr create --title "lit: spatial proteomics harvest" --body "$(cat <<'EOF'
## Queries
- "spatial proteomics imaging mass cytometry atlas"
- "CODEX multiplexed imaging tissue dataset"
- "MIBI-TOF spatial single cell"

Result ID: s_abc123

## Result
62 hits · 18 new papers · 37 dataset rows added (1028 -> 1065)

## Notes
- 4 papers reported no datasets
- 2 rows have blank n_samples (not stated in source)
EOF
)"
```

`gh` is often not installed — check with `command -v gh` rather than assuming, and don't treat its absence as a failure. Pushing a new branch makes GitHub print a `pull/new/<branch>` URL in the push output; surface that link to the user along with the PR body as text they can paste. The work is already safely on the remote either way, so the PR step should never block the run.

Whichever route, put the queries and the result ID in the description. Six weeks from now the only way to know why a row exists is that text, and a reviewer's first question is always "where did these come from."

## Platform normalization

`10x Visium`, `Visium`, and `Visium Spatial Gene Expression` are the same platform written three ways, and the existing table contains all three. That fragmentation makes the column useless for grouping, which is most of what anyone wants it for.

The script maps known aliases to canonical forms and leaves anything unrecognized untouched rather than guessing. When it reports an unmapped platform, decide whether it's genuinely new or another spelling of something present — and if it's a new spelling, add it to `PLATFORM_ALIASES` so the next run handles it. The vocabulary is meant to grow.

Don't retroactively rewrite existing rows as part of a harvest run. That's a separate, reviewable change; bundling it into an append makes the diff impossible to read.

## What tends to go wrong

**Re-mining a paper with a different prompt and getting different datasets.** The extraction is not deterministic. If you re-run a paper, delete its old rows first — otherwise the table holds two inconsistent accounts of the same paper's claims with no way to tell which is current.

**Trusting `map` on dataset counts.** `n_samples` is the field it most often gets wrong, usually by conflating a cohort size with a section count. Blank is better than wrong, and the prompt above asks for exactly that.

**Treating a paper's mention as a dataset.** Reviews and benchmark papers name dozens of datasets they never touched. `origin` is meant to capture generated vs reused, but neither applies to a passing citation. When the paper didn't actually use the data, it shouldn't produce a row.

**Assuming Paperclip has everything.** Its bioRxiv ingestion lags publication by roughly three months as of August 2026 — TERRA (posted 2026-08-04) is absent entirely. Recent work has to be added by hand, and a search returning nothing recent reflects the index, not the field.
