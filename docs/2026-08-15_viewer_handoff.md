# Handoff: somics 3D viewer

Written 2026-08-15. Everything below is verified unless marked otherwise. Read the "Not verified"
section before you touch anything — the single biggest gap is that **nobody has ever seen this render**.

## What was asked for

Issue [#2](https://github.com/aopisco/somics/issues/2), refined over several rounds in chat:

1. A 3D viewer as the UI for somics: body on the left, detail panel on the right.
2. "Google Earth for spatial transcriptomics" — samples are points, clicking zooms continuously in
   until you are looking at the sample itself.
3. A **spinning rat** (with a human toggle), not just a human.
4. A **low-pixel** (voxel) rat/human **with organs**.
5. Standing in a **low-pixel wild grass field**.
6. **High whimsy.**
7. The **URL captures the complete viewer state**, so a copy-pasted link reproduces a view exactly.
8. An **agent passthrough**: an agent drives the UI over HTTP and you watch it happen.

All eight are implemented. Whether they *look* good is unverified — see below.

## Where it lives

```
src/somics/viewer/          FastAPI service (Python, needs homeobox — browsers cannot read LanceDB/R2)
  anatomy.py                278 tissue spellings -> 30 organs; per-species blob geometry
  atlas_source.py           cached reads: samples, cell coordinates, morphology crops, gene columns
  api.py                    HTTP surface
  control.py, drive.py      agent control channel + CLI
viewer/                     Vite + React 19 + @react-three/fiber 9 + three 0.185
  src/types.ts              ViewerState and the API's data shapes — the contract everything shares
  src/state.ts              zustand store; every component reads from here
  src/theme.ts              palette + VOXEL size
  src/body/                 signed-distance silhouettes -> voxelizer -> instanced cubes
  src/scene/                sky + lighting, grass field, pollen motes
  src/camera/               zoom-level model (lod.ts) + camera rig
  src/layers/               point cloud, morphology tiles, viridis/magma
  src/markers/              organ pins
  src/panel/                sample detail column
  src/url/                  state codec + history sync
  src/agent/                control-stream client + banner
  src/whimsy/               shared motion curves, loading copy, sound
```

`viewer/README.md` is the user-facing doc: how to run it, the URL format, how to drive it from an agent.

## Run it

```bash
uv sync
uv run python -m somics.viewer            # API on 8787
cd viewer && npm install && npm run dev   # UI on 5273
```

The API also serves `viewer/dist` at `/` when that directory exists, so `npm run build` plus the
Python process alone is enough for a single-process demo.

## Verified

- **244 tests pass, none touch the network.** `uv run pytest src/somics/viewer` (107) and
  `cd viewer && npm run test` (137).
- `npx tsc --noEmit` clean; `npm run build` succeeds (1.14 MB / 316 kB gzip, one chunk).
- `uv run ruff check src/` and `ruff format --check src/` clean. Pre-commit hooks ran on the commit.
- **Every API endpoint answers correctly against the live atlas over R2.** Checked by hand with curl:
  `/api/anatomy` (30 organs, 47 rat blobs), `/api/samples` (1 sample, routed to `colon`),
  `/api/samples/{uid}/points?max_points=40000` (480,000 bytes = 40,000 x 3 x float32, correct
  `X-Somics-Meta`), `/api/samples/{uid}/crops` (12 tiles, 27.2 µm square), `/api/samples/{uid}/genes`
  (425 genes).
- **Voxel counts measured** against the real anatomy payload at `VOXEL = 0.26`: rat 2,241 shell +
  1,431 organ cubes, human 2,523 + 2,652, build ~23 ms, and **no organ comes out empty** on either
  body. This was a real bug: at the original 0.42 four to five organs rounded away to nothing, and
  the human eye was being swallowed whole by an oversized brain blob. `test_every_organ_claims_voxels`
  now pins it.

### Performance numbers worth keeping (measured against snapshot v0 over R2)

| Operation | Time |
|---|---|
| `checkout_latest` | 4.2 s |
| sample-index scan (9 columns, 587k rows) | 2.4 s |
| one section's coordinates | 2.5 s |
| 16 morphology crops in a window | 2.2 s |
| one gene across all 587k cells | 49.3 s |
| `samples()` end to end, first call | 12.4 s |

**The trap worth remembering:** `where("section_uid = '...'")` costs **21–27 s** where the same
unfiltered scan plus a polars filter costs **2.5 s** — LanceDB has no index on that column. Every obs
read in `atlas_source.py` is therefore a full scan filtered in polars. This is documented in the
module docstring; do not "optimise" it back into a SQL predicate.

## NOT verified — start here

1. **No pixels have ever been rendered.** I could not get a headless browser up: Playwright's Chromium
   fails with `libgbm.so.1: cannot open shared object file`, and installing system libraries needs
   root. Everything visual — whether the rat looks like a rat, whether the grass reads as a field,
   whether the camera flight feels like Google Earth, whether the pixelation looks good, whether the
   point cloud reads as tissue — is **unconfirmed**. Either run it in a real browser, or get
   `libgbm` (plus likely `libxkbcommon`, `libnss3`, `libasound2`) available and re-run a Playwright
   screenshot pass. A script that captures orbit/organ/section/cell states by URL hash is easy to
   rebuild; the URL codec makes each state directly addressable.
2. **The camera rig has never run.** `CameraRig.tsx` tweens the camera while OrbitControls stays
   enabled, and writes `setCamera`/`setLod` back into the store that drives it. The author closed the
   feedback loop by reading most state through `useStore.getState()` instead of subscribing, but that
   interplay is exactly the kind of thing that only misbehaves live. Watch for: the camera fighting
   the user, `lod` oscillating at a threshold, or the tween restarting itself.
3. **The click-to-cell micron round trip is untested at runtime.** `PointCloud.tsx` inverts the
   server's normalisation to turn a click into micron coordinates, and `CropTiles.tsx` maps tiles back
   the other way. If the tiles appear in the wrong place or not at all, that inverse is where to look.
4. **The agent channel has never been exercised by a real browser.** The Python side is tested
   (including the SSE generator) and the client's message parsing has 38 tests, but no EventSource has
   ever connected. Try: open the UI, then `uv run python -m somics.viewer.drive --tour`.
5. **Frame rate is unmeasured.** `GrassField` rewrites all 6,000 blade instance matrices every frame.
   That may be fine or may be the first thing to cut; the author noted it deliberately.

## Known gaps and rough edges

- **Gene painting has no cancel.** Picking a gene fires a ~49 s cold read; changing your mind leaves
  it running. It is cached on disk afterwards (`~/.cache/somics-viewer`).
- **`_section_coords` caches every section's coordinates in memory** (23 MB at v0). That is the wrong
  shape for a hundred-million-cell atlas; it is called out in the docstring as the scaling ceiling.
- **Sound only fires on navigation** (a squeak going out to orbit, a whoosh going in). No squeak on pin
  hover or click yet.
- **The whimsy list is only partly built.** Present: idle spin with wobble, breathing, pin
  squash-stretch entrance with stagger, grass wind, pollen motes, rat-flavoured loading lines,
  hover labels. Never built: the nose twitch and tail sway as *distinct* animations (the curves exist
  in `whimsy/motion.ts` and are used generically), the barrel-roll easter egg.
- **One bundle of 1.14 MB.** Mostly three.js. Code-splitting would help first load.
- **The atlas has one sample**, so the multi-sample paths in the panel and markers (an organ with
  several sections) have never been exercised with real data.

## Branch and PR situation — read this

The work was committed on branch `viewer` (base `origin/hox-schema`) as `762d581`, then merged into
local `main` as `a0c88b0`. **`main` has not been pushed.**

The merge also brings in five commits from **open PR [#5](https://github.com/aopisco/somics/pull/5)**
(`hox-schema`, conradry's atlas schema, ingest script, query skill, and the `homeobox` dependency).
That is not incidental — the viewer needs `homeobox` to read the atlas at all, so it cannot land on
main without at least that part of #5. Pushing `main` as it stands would effectively merge someone
else's open PR. Decide deliberately:

- push `main` and close #5 as merged, or
- push `viewer` and open a PR against `hox-schema` so it stacks behind #5, or
- rebase just `762d581` plus the `homeobox` dependency onto `main`.

## Design decisions you might want to revisit

- **Live atlas rather than the literature CSV.** `data/literature_datasets.csv` has 1,028 rows across
  278 tissues and would light up the whole body; the atlas has one colon section. Live data was chosen
  deliberately. `anatomy.resolve_tissue` already handles the CSV's spellings (there are tests for
  them), so switching or blending is cheap.
- **Species mismatch is surfaced, not hidden.** The one sample's donor is human while the default body
  is a rat. The panel prints an explicit note. Don't "fix" it by silently forcing the body to match.
- **Data is never decorated.** Cell colours are viridis and magma; the warm golden-hour palette is
  scene-only. Keep that separation.
- **Bodies are procedural, no asset files.** Signed-distance functions in `body/silhouette.ts`,
  voxelized at runtime. No licensing question, no binary in git. The cost is that the silhouettes are
  hand-tuned numbers — if the rat looks wrong, that file is where to fix it.
