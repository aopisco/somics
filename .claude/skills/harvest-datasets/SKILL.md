---
name: harvest-datasets
description: Run a Paperclip literature search, extract the spatial omics datasets each paper reports, add them to data/literature_datasets.csv, then curate them into data/datasets.csv (one row per dataset, referenced by its ORIGINAL publication) and data/model_dataset_usage.csv (which papers/models use which datasets) in the somics repo. Use this whenever the user wants to grow the dataset inventory from the literature or "check for new datasets" — phrases like "search for X and add it to the inventory", "harvest datasets on spatial proteomics", "run another literature search", "add these papers' datasets to the CSV", or any request to expand, extend, or top up the dataset tables. Also use when the user names a modality, platform, tissue, or disease and wants to know what datasets the literature reports for it, since the answer belongs in the inventory rather than in chat. Do not use for reading a single paper, or for st_corpus.csv, which is the TERRA sample corpus and is maintained separately.
---

# Harvest datasets from the literature

The inventory is three tables, fed in order:

1. **`data/literature_datasets.csv`** — raw **claim-level** extraction: one row per
   (dataset × source paper). Steps 0–5 below maintain it.
2. **`data/datasets.csv`** — the curated registry: **one row per dataset**, referenced by
   its **original publication** (the paper that FIRST released the data). In-house data
   first released by a model paper carries that model paper as its original reference
   (`first_published_by_model_paper = yes`); vendor datasets (10x, Bruker) carry the vendor
   page. `data_access_link` is the landing page/accession; `download_url` is a direct
   curl-able file URL (st_corpus.csv semantics). Steps 6–8 maintain it.
3. **`data/model_dataset_usage.csv`** — many-to-many (paper/model × dataset): usage type
   and the dataset's alias inside that paper (e.g. DRIFT's "10xHPC" = spatialLIBD DLPFC).

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

Confirm a clean tree before touching anything — this workflow ends in a commit straight to main, and mixing it with unrelated work in progress makes the diff unreviewable.

```bash
cd ~/src/somics
git status --short                    # expect empty
git checkout main
git pull --ff-only origin main        # ALWAYS start from current origin/main
```

Parallel sessions land commits on main frequently. Starting stale is how pushes get rejected later, so the pull is not optional.

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

### 4. Append

Work directly on main — no feature branch.

```bash
python scripts/append_datasets.py \
  --rows /tmp/extracted.json \
  --sheet data/literature_datasets.csv \
  --found-via literature_search
```

The script refuses rows whose `source_paper_id` is already present, normalizes `platform` against the controlled vocabulary, validates column order, and prints a summary. If it rejects rows, read the reason before overriding — `--force` exists for the case where you deliberately want to re-mine a paper with a better prompt, and you should delete that paper's old rows first if so.

### 5. Commit and push

Team practice for this repo (decided 2026-08-15): harvest commits go to main
directly rather than through a PR. The provenance that used to live in the PR
description — queries, result ID, hit/new split, caveats — goes in the commit
message body instead:

```bash
git add data/literature_datasets.csv
git commit -m "$(cat <<'EOF'
lit: +37 datasets from 18 papers (spatial proteomics)

Queries:
- "spatial proteomics imaging mass cytometry atlas"
- "CODEX multiplexed imaging tissue dataset"
- "MIBI-TOF spatial single cell"

Result ID: s_abc123
62 hits · 18 new papers · 37 rows added (1028 -> 1065)
4 papers reported no datasets; 2 rows blank n_samples (not stated)
EOF
)"

git fetch origin && git rebase origin/main
git push origin HEAD:main
```

Main moves often — parallel sessions land commits throughout the day — so
rebase onto origin/main immediately before the push. If the push is rejected
anyway, fetch and rebase again; do not force-push and do not merge.

Six weeks from now the only way to know why a row exists is that commit
message, and a reviewer's first question is always "where did these come
from" — keep the queries and the result ID in it.

### 6. Trace new papers' datasets to their original publications

The claim-level rows reference the *analyzing* paper; the curated registry wants the paper
that *first released* the data. Papers cite datasets by reference number, so ask the map
reader to expand each citation from the paper's own reference list. Run over the NEW papers
only (same result set as step 3):

```bash
paperclip map --from s_abc123 \
  --output-schema '{"type":"object","required":["datasets"],"additionalProperties":false,"properties":{"datasets":{"type":"array","items":{"type":"object","required":["name","platform","original_title","original_first_author","original_year","original_is_this_paper","accession_or_link"],"additionalProperties":false,"properties":{"name":{"type":"string"},"platform":{"type":["string","null"]},"species":{"type":["string","null"]},"tissue":{"type":["string","null"]},"original_first_author":{"type":["string","null"]},"original_title":{"type":["string","null"]},"original_journal":{"type":["string","null"]},"original_year":{"type":["integer","null"]},"original_is_this_paper":{"type":"boolean"},"accession_or_link":{"type":["string","null"]}}}}}}' \
  "For every spatial omics DATASET that this paper analyzed or generated, trace it to its ORIGINAL publication - the paper that FIRST released the data. Papers usually cite datasets with reference numbers (in a datasets table or Methods); look up each cited reference in the REFERENCE LIST and extract: original_first_author (surname), original_title (full title), original_journal, original_year. If this paper itself generated the dataset (in-house/new data), set original_is_this_paper=true and leave the original_* fields null. Also give the dataset short name/alias as used in the paper, its platform, species, tissue, and any data accession/URL stated (null if none). Skip simulated/synthetic data. Methods/review papers with no specific datasets: return empty array."
```

**Operational limits learned the hard way (2026-08):**
- A single map over ~1,000 papers hits the server's ~25 min cap with this heavier prompt.
  Chunk with `-n 250 --offset 0/250/500/…` — each chunk runs in 5–10 min.
- Chunks sometimes come back with mass transient `[error]` blocks. Do NOT rerun from
  scratch: `paperclip map --resume m_XXXX --retry-failed` recovers them cheaply
  (983 papers went from 6% to 98% success this way).
- `paperclip sql`-saved result sets are NOT usable with `map --from`; only search/searches
  result sets are. To subset, chunk with `-n/--offset` instead.
- Export each chunk with `paperclip results m_XXXX --save trace_N.txt`.

Also build the analyzing-paper metadata file (paperclip's SQL/`results --save` truncate
titles, so read each paper's `meta.json`):

```bash
while read id; do paperclip cat /papers/$id/meta.json ...; done < new_ids.txt > meta.jsonl
# one JSON object per line: {"id":..., "title":..., "doi":..., "year":...}
```

### 7. Merge into the curated tables

```bash
python3 scripts/trace_originals.py --trace trace_0.txt --trace trace_1.txt \
    --meta meta.jsonl --cache /tmp/crossref_cache.json
```

The script dedupes claims by (original publication × platform family), resolves cited
originals to DOIs via Crossref (title-match verified; misses are flagged in `notes`,
never guessed), matches new datasets into existing rows by DOI, and appends the rest to
`data/datasets.csv` + `data/model_dataset_usage.csv`. Existing rows are never modified.

### 8. Resolve direct download URLs and verify links

```bash
python3 scripts/resolve_download_urls.py            # fills download_url (probes every URL)
python3 scripts/verify_downloads.py data/datasets.csv   # optional: refresh data_downloadable
```

`resolve_download_urls.py` knows Zenodo (single file → content URL; ≤300 MB record →
files-archive zip; larger → largest file), GEO bulk download, Dryad, and direct-file
passthrough. It never overwrites a non-empty `download_url`. Sites behind bot protection
(10x/Cloudflare 403) stay unresolved — that is expected, not a failure.

Commit the curated tables in the same push as the claim-level append (step 5), listing the
map IDs in the commit body.

## One technology per row

**A row describes one measurement of one tissue on one platform.** If a paper
reports the same tissue on several platforms, that is several datasets, and the
row is duplicated once per platform rather than written as
`Visium, Visium HD, Xenium, and MERFISH`.

A merged row breaks everything downstream at once: platform counts under-report,
no builder can be selected for it, and the row cannot carry more than one
`download_url` — so at most one of the platforms is fetchable and the rest look
staged when they are not.

When extraction produces one, split it: keep the original `dataset_id` for the
first platform, give the others `<stem>_<platform-slug>`, copy every other field
unchanged, and note in `notes` what it was split from. Duplicate the row's
entries in `model_dataset_usage.csv` too — a model that used the merged dataset
used each of the split ones.

`scripts/split_multiplatform_rows.py` does this, and is deliberately timid: it
only splits when **every** part is a platform the registry already uses on its
own, and lists the rest for a human. That matters because punctuation is not a
reliable signal — `LC-MS/MS` is one technique, `Xenium 5K + custom panel` is a
platform plus a qualifier, and `VisiumHD / 10X Genomics` is a platform plus its
vendor. Splitting those would invent platforms that do not exist.

## Platform normalization

`10x Visium`, `Visium`, and `Visium Spatial Gene Expression` are the same platform written three ways, and the existing table contains all three. That fragmentation makes the column useless for grouping, which is most of what anyone wants it for.

The script maps known aliases to canonical forms and leaves anything unrecognized untouched rather than guessing. When it reports an unmapped platform, decide whether it's genuinely new or another spelling of something present — and if it's a new spelling, add it to `PLATFORM_ALIASES` so the next run handles it. The vocabulary is meant to grow.

Don't retroactively rewrite existing rows as part of a harvest run. That's a separate, reviewable change; bundling it into an append makes the diff impossible to read.

## What tends to go wrong

**Re-mining a paper with a different prompt and getting different datasets.** The extraction is not deterministic. If you re-run a paper, delete its old rows first — otherwise the table holds two inconsistent accounts of the same paper's claims with no way to tell which is current.

**Trusting `map` on dataset counts.** `n_samples` is the field it most often gets wrong, usually by conflating a cohort size with a section count. Blank is better than wrong, and the prompt above asks for exactly that.

**Treating a paper's mention as a dataset.** Reviews and benchmark papers name dozens of datasets they never touched. `origin` is meant to capture generated vs reused, but neither applies to a passing citation. When the paper didn't actually use the data, it shouldn't produce a row.

**Assuming Paperclip has everything.** Its bioRxiv ingestion lags publication by roughly three months as of August 2026 — TERRA (posted 2026-08-04) is absent entirely. Recent work has to be added by hand, and a search returning nothing recent reflects the index, not the field.

**A modeling/benchmark paper that extracts ZERO datasets.** This is almost always a
false negative, not a paper without data. Modeling papers keep their dataset inventory in
a *table* ("Table 1: Overview of datasets"), and Paperclip's text extraction routinely
drops table bodies while keeping the caption — so the reader sees "summarized in Table 1"
with no rows. Hit this twice: DRIFT and SpatialProp both extracted nothing on the first
pass and both had a full dataset table.

Recovery — fetch the PDF, which parses tables the corpus copy lost:

```bash
paperclip fetch "https://www.biorxiv.org/content/<doi>v1" --into /clipboard/somics/
# wait for indexing, then read the table region
paperclip grep -B 1 -A 3 "Table 1" /clipboard/somics/<usr_id>/content.lines
paperclip cat /clipboard/somics/<usr_id>/content.lines > /tmp/paper.txt   # then parse locally
```

The table gives dataset → reference number; resolve each number in the bibliography
(`grep -oE "\[N\][^[]{40,300}"` over the local copy) to get the original publication, then
Crossref the title for the journal DOI. This is the same alias → reference → original chain
`trace_originals.py` automates for the normal case; here you feed it by hand.

**Searching only by method and modality, and missing resource papers.** The seed
vocabulary was topic- and model-centric ("spatial transcriptomics", "foundation model",
"pretrained representation"), which retrieves *methods* papers and misses *resource* papers
that describe themselves as an atlas, reference, consortium or data release. SAHA (the
Spatial Atlas of Human Anatomy, a multi-organ CosMx/Xenium/GeoMx reference) never appeared in
any of the first ~1,300 papers retrieved, and an "atlas / reference / consortium" sweep in
Aug 2026 came back **92% unmined** (117 of 126) — the worst-covered axis found so far. Search
the artefact as well as the method.

**Treating "no perturbation vocabulary" as "no perturbation datasets."** Transcriptomics/
proteomics/foundation-model queries do not surface CRISPR screens, drug-treatment series,
transgenic-model or injury time-course data — a perturbation-specific probe in Aug 2026
returned 54 papers of which 47 were unmined, and a wider one 246 of which 182 were unmined.
When harvesting perturbation work, add `perturbation` to the claim schema (what was applied:
knockout, compound, transgene, injury; null if observational) and record it inline in
`dataset_name` as `[perturbation: ...]` so the rows stay findable.
