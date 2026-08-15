# somics demo — run-of-show (paperclip search in a Claude session)

Goal: record a short video of a Claude Code session using the paperclip CLI to
search literature and extract spatial-omics dataset metadata — the workflow that
built `data/literature_datasets.csv`.

All commands below were rehearsed on 2026-08-15 (paperclip 0.7.37, this machine)
with the timings shown. Everything on camera runs in **< 5 seconds**.

---

## 1. Recording setup (nothing to install)

- **⇧⌘5** → "Record Selected Portion" → drag over the terminal window → Record.
  Stop from the menu-bar stop button. Saves a `.mov` to the Desktop.
- Terminal prep: bump the font (⌘+ a few times), full-screen or ~120 columns,
  quiet your prompt if it shows long paths.
- Start the recording *before* launching `claude` so the session opener is captured.
- Optional nicer terminal-native capture: `brew install asciinema agg`
  (records the terminal as text, converts to gif) — not required.

## 2. Pre-flight checklist (off camera, 1 min)

```bash
paperclip --version                      # 0.7.37 at rehearsal
paperclip results --list | head -5       # confirms login + saved results exist
paperclip ls /clipboard/somics           # 3 docs: TERRA, VirTues, KRONOS
```

Already done for you:
- Clipboard PDFs renamed from `tmp*.pdf` to **TERRA.pdf / VirTues.pdf / KRONOS.pdf**
  so search hits look clean on camera.
- Saved artifacts verified live: search set `s_edf954f9` (983 papers),
  map `m_4df4abed` (the structured extraction behind `literature_datasets.csv`).

## 3. Run-of-show

Drive the demo by typing prompts to Claude; Claude runs the paperclip commands.
Suggested prompts (left) and what Claude will run (right).

### Scene 1 — search the public corpus (~0.3 s)
> **Prompt:** "Search the literature for spatial transcriptomics foundation models."

```bash
paperclip search -s pmc,biorxiv "spatial transcriptomics foundation model" -n 5
```
Returns titles, authors, DOIs and one-line summaries; auto-saves a result set id
(`s_…`) and hints the follow-up `map` command.

### Scene 2 — search your own PDF library (~0.2 s)
> **Prompt:** "Now search only the papers in our somics clipboard."

```bash
paperclip search -s clipboard/somics "spatial omics foundation model" -n 3
```
Hits the three uploaded key papers: **TERRA**, **VirTues**, **KRONOS** —
public corpus and private PDFs behind one interface.

### Scene 3 — open a paper like a filesystem (~50 ms)
> **Prompt:** "Show me the TERRA paper's metadata."

```bash
paperclip cat /clipboard/somics/usr_e71f77c1dcf1/meta.json
paperclip cat /clipboard/somics/usr_e71f77c1dcf1/content.lines | head
```
Papers are addressable paths: metadata, full text lines, sections, figures.

### Scene 4 — the wow moment: LLM map over search results (~4 s)
> **Prompt:** "For each of those foundation-model papers, what platforms do they use and what's the largest training dataset?"

```bash
paperclip map --from <s_id_from_scene_1> \
  "What spatial omics platforms does this paper use, and what is the largest dataset it trains or evaluates on?"
```
Per-paper structured answers with line-number citations (L23, L66…), in ~4 s
for 3 papers. (Rehearsal run saved as `m_1d8dede3` if you want to replay it
instantly with `paperclip results m_1d8dede3`.)

### Scene 5 — show the scale: how the inventory was built (~1 s)
> **Prompt:** "Show the full corpus search and the extraction that built our dataset inventory."

```bash
paperclip results s_edf954f9 | head -20     # 983-paper multi-query search set
paperclip results m_4df4abed | head -40     # schema-constrained dataset extraction
```
Talking point: 7 queries → 983 papers → JSON-schema map (~22 min, off camera)
→ 1,028 dataset rows in `data/literature_datasets.csv`.

### Scene 6 — export (optional closer, ~1 s)
```bash
paperclip results m_4df4abed --save /tmp/demo_extract.txt
```

### Scene 7 — alternative closer: the Claude skill
> **Prompt:** `/harvest-datasets "spatial proteomics of the human gut"`

The repo's `harvest-datasets` skill runs the whole search → extract → CSV → PR
pipeline autonomously. Note: it creates a git branch and PR, and a real harvest
takes minutes — for the video, consider just *showing* the prompt and cutting,
or narrating over the earlier `m_4df4abed` results instead.

## 4. Gotchas (so the recording doesn't stall)

- **Don't run `map` on clipboard searches** — the map worker can't load clipboard
  full text on this account ("no loadable full text"); `cat` works fine. Map over
  pmc/biorxiv results (Scene 4) only.
- `--worker structured-extraction` and high `-j` are gated (GXL testers); use the
  default worker + `--output-schema`.
- `paperclip mv --help` errors (`vsh: mv: permission denied`) but `mv` itself works.
- If a scene misfires on camera, every search/map is auto-saved — replay with
  `paperclip results <id>` instead of re-running.

## 5. Slide material

Copy `docs/demo/demo_commands.md` into the team slides — one section per slide.
