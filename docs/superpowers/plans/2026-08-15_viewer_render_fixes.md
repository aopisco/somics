# Plan: make the somics viewer actually look right

Context: the viewer was built on a headless node and had never rendered a frame. It now renders
(see commit `e9992b3`, which fixed an infinite zustand render loop and a 422 on the control channel).
This plan fixes what the first real look revealed, and adds four things the user asked for while
watching it.

Branch: `gjohnson/viewer_render_fixes`. Base for this plan: `e9992b3`.

## How to see your work — read this before starting any task

There is a Playwright harness at `tmp/20260815_viewer_render_check/`. Two processes must be running:

```bash
uv run python -m somics.viewer            # API on 8787
cd viewer && npm run dev                  # UI on 5273
```

Then, from `tmp/20260815_viewer_render_check/`:

```bash
node shoot.mjs <outdir>    # screenshots all four zoom levels + human, prints console errors
```

It captures by URL hash, so every state is directly addressable:

```
#sp=rat&lod=orbit
#sp=rat&n=colon&lod=organ
#sp=rat&n=colon&s=183c734af72b51e0&lod=section
#sp=rat&n=colon&s=183c734af72b51e0&lod=cell
#sp=human&lod=orbit
```

Chromium runs with SwiftShader (`--use-gl=angle --use-angle=swiftshader
--enable-unsafe-swiftshader`); keep those flags or you get no WebGL.

**You must look at the PNGs you produce.** Read them with the Read tool. A visual task is not done
because the tests pass — it is done when the screenshot shows the thing. Attach before/after
observations to your report.

## Global Constraints

These are non-negotiable and come from the previous author's measurements and the user's direction.
Violating one fails review regardless of anything else.

1. **Do not put a SQL predicate on `section_uid` in `atlas_source.py`.** It full-scans and filters in
   polars on purpose: the predicate was measured at 21-27s against a 2.5s scan, because LanceDB has
   no index on that column. Do not "optimise" it.
2. **Measured data is never decorated.** Cell colours are viridis (counts) and magma (gene). The warm
   golden-hour palette is scene-only. Do not colour data with the scene palette or vice versa.
3. **The live R2 atlas is the viewer's only data source.** Confirmed twice by the user on 2026-08-15:
   "r2 only", and "you should be querying from r2". Do not blend, fall back to, or seed the body from
   `literature_datasets.csv`, `datasets.csv`, or any other CSV. Query the atlas.
4. **The species-mismatch note stays visible.** The atlas's one sample is human; the default body is a
   rat. The panel says so explicitly. Do not hide it, and do not silently force the body to match.
5. **Bodies stay procedural — no body/organ asset files.** Silhouettes are signed-distance functions
   voxelized at runtime; this is a deliberate licensing decision from the previous author. No model
   files for bodies or organs.
   **Amended 2026-08-15 by the user:** the sky backdrop is exempt. Task 5 uses a real 360
   equirectangular photograph. It must be **CC0 or public domain** (Poly Haven is CC0 and is the
   expected source) — check the licence before committing, and record it in the repo. Nothing else in
   this constraint changes.
6. **Verify visually, and say what you saw.** Every task here changes what is on screen. "137 tests
   pass" is not evidence that a visual bug is fixed.
7. **Green gates:** `npx tsc --noEmit` clean, `npm run test` passing, and for Python changes
   `uv run ruff check src/`, `uv run ruff format --check src/`, `uv run pytest src/somics/viewer`.
8. Tests live alongside source (`foo.ts` -> `foo.test.ts`). Type hints throughout on Python.

## The atlas changed under this plan — read this

Written when the plan was drafted: "the atlas holds one sample". **That is no longer true.** The LIBD
DLPFC Visium dataset was ingested on 2026-08-15 (`ff2245b`). `/api/samples` now returns **13 samples**:

| organ | tissue | technology | sections | scale |
|---|---|---|---|---|
| `colon` | colon | xenium | 1 | 587,115 cells |
| `brain` | dorsolateral prefrontal cortex | visium | 12 | 3,460–4,789 spots each |

Three consequences every task should know:

1. **Two organs light up now, not one.** 28 inert sockets, not 29.
2. **The multi-sample path is live.** One organ (`brain`) carries twelve sections. The handoff flagged
   the panel's and markers' multi-sample handling as never having been exercised with real data —
   it now is, and it is the most likely place for a new bug to show up. An organ with 12 samples must
   be selectable, listable, and navigable.
3. **A second spatial unit is live.** Visium is spot-based at ~4k spots per section; Xenium is
   cell-based at 587k. Anything that assumed one scale or the word "cell" — point sizing, budgets,
   labels, `spatial_unit` handling — now has a second case, three orders of magnitude smaller. A point
   size tuned for 587k cells may be invisible or absurd at 4k spots.

Do not treat the one-sample statements elsewhere in this document as current. Query the API.

## Task 1: Stop the LOD/camera feedback loop

**The bug.** The model flickers in and out, and the zoom level drifts on its own. Loading
`#sp=rat&n=colon&s=183c734af72b51e0&lod=section` and waiting 45s ends with the hash at
`#n=colon&s=183c734af72b51e0&cam=13.011,12.49,19.398&tgt=0,3.5,0` — `lod=section` silently gone and
the camera back at orbit framing.

**Where.** `viewer/src/camera/CameraRig.tsx`, with `viewer/src/camera/lod.ts` as the shared model.

Every frame, `useFrame` derives a level from the live camera-to-target distance
(`lodForDistance`) and writes it into the store when it differs. The store's `lod` drives
`focusFor`, which is what the tween flies to. Two failure modes to check for and fix:

- The URL's `lod` is discarded on cold load. The tween effect returns early when `currentBounds()` is
  null, which it is while `/api/anatomy` is still in flight. `flyRequest` does not change again once
  anatomy lands, so no fly ever happens for the initial URL state — and then the per-frame
  distance check overwrites the URL's level with whatever the default camera distance maps to.
- The per-frame writeback fights the user and itself near a threshold.

**What "fixed" means.**
- Loading any of the five URLs above and waiting 60s leaves the hash's `lod` as it was set. Prove it
  with the harness; the probe script `probe.mjs` already does the long wait and prints the final hash.
- The body does not visibly flicker between levels while idle.
- Manual orbit/wheel zoom still changes the level (that behaviour is wanted — do not fix the drift by
  deleting the distance-to-level mapping).
- Add unit tests to `viewer/src/camera/lod.test.ts` for whatever pure logic you extract. The
  feedback loop itself needs the browser; state that you checked it there.

## Task 2 (REDIRECTED 2026-08-15): the spots and cells belong in the floating panel

**Superseding direction from the user**, given after Task 6's floating panel landed:

> "the floating panel is great but the spots/cells should appear in the floating panel, not rendered
> in 3d in the 3d viewer"

The point cloud stops being a 3D layer. Cells and spots are drawn as a **2D view inside the floating
panel**; morphology imagery is already there via `panel/Morphology.tsx`. The 3D scene keeps the body,
organs and pins.

**Ruling (controller):** the 3D view must not go blank at section and cell level — a blank 3D stage is
what made the app feel broken in the first place. Body and organs stay visible while the data lives in
the panel.

This is arguably the better architecture anyway: a tissue section *is* 2D, so drawing it in a 2D panel
removes an entire class of 3D placement, scaling and frustum bugs — which is precisely what this task
had been stuck on.

What survives the redirect, unchanged:
- The data was never the problem: `/api/samples/.../points` returns 200 every time.
- **Two spatial units, three orders of magnitude apart** — Xenium 587k cells, Visium ~4k spots. Both
  must look right.
- **viridis by transcript count, magma by gene.** Measured data is never decorated (Constraint 2).
- Twelve brain sections; switching must load and draw the newly selected one.
- **The micron round trip** — clicking a cell drives the morphology view to that location. It becomes a
  simple pixel↔micron mapping in 2D. Pin it with tests.

What changes:
- A 2D canvas is almost certainly right; 587k points is not a DOM or SVG workload. The colour-mapping
  in `PointCloud.tsx` is reusable, the placement maths is not.
- `viewer/src/layers/PointCloud.tsx` and `CropTiles.tsx` are likely dead. Removing them is in scope.

### Original text, kept for the diagnosis it records

**The bug.** Both data levels are a flat empty peach field. Nothing is drawn. This is the whole point
of the application, so treat it as the most important task here.

`GET /api/samples/183c734af72b51e0/points?max_points=80000` returns 200 with real data, so the fetch
is fine — the geometry is being built, placed, or scaled wrong, or drawn outside the camera frustum.

**Where.** `viewer/src/layers/PointCloud.tsx` and `viewer/src/layers/CropTiles.tsx`, against
`sectionTransform`/`SECTION_SIZE` in `viewer/src/camera/lod.ts`.

The handoff flagged this exact area as never having run: `PointCloud.tsx` inverts the server's
normalisation to turn a click into micron coordinates and `CropTiles.tsx` maps tiles back the other
way. Check the forward direction too — where the points land in world space relative to the organ
anchor the camera flies to.

Note Task 1 may share a root cause: if the camera is not where the store thinks, the plane could be
correctly placed and simply off-screen. Coordinate with what Task 1 found (read
`docs/superpowers/plans/` sibling reports if present), but do not assume — verify where the geometry
actually is. Logging the computed world-space bounding box of the point cloud and comparing it to the
camera position is the fastest way to settle it.

**What "fixed" means.**
- `#sp=rat&n=colon&s=183c734af72b51e0&lod=section` shows a recognisable tissue section: a dense cloud
  of 80,000 points in viridis, filling a good fraction of the frame.
- `...&lod=cell` shows morphology image tiles.
- Clicking a point at section level moves to cell level at that location (the micron round trip).
- Screenshots of both, in your report.

## Task 3: Fix the body's apparent scale and the organ pin

**The bug.** At `#sp=human&lod=orbit` the human is a ~60px speck in a huge grass field. The
anatomy payload declares human bounds 9 x 18 x 5 world units and `buildBodyVoxels` voxelizes in those
same units, so the geometry should be 18 units tall and fill most of the frame at the orbit distance
`focusFor` picks (~24.9 away). It does not. Find out why: measure the actual world-space bounding box
of the built `InstancedMesh` and compare it to `body.bounds` and to the camera.

The rat at orbit frames much better, so compare the two and find what differs.

Also in scope, same file and same look-at-it pass:
- **The rat reads as a ghost.** `SHELL_OPACITY = 0.16` in `viewer/src/body/Body.tsx` makes the shell
  nearly invisible, so the body reads as a floating cloud of pastel organ blobs rather than an
  animal. Raise it until the silhouette reads as a rat while organs still show through. Judge this
  from screenshots, not from the number.
- **The organ pin renders as a flat washed-out grey square** rather than a glowing pin
  (`viewer/src/markers/`). Make it read as a pin.

**What "fixed" means.** Human and rat both fill a sensible fraction of the frame at orbit. The
silhouette reads as the animal it is. The colon pin reads as a glowing marker. Screenshots of
`#sp=rat&lod=orbit` and `#sp=human&lod=orbit` in your report.

## Task 4: Fix the grass

**The bug.** The grass blades render near-black. The bottom third of the frame is solid black, and the
blades occlude the body at orbit. The user's words: "the grass is black".

**Where.** `viewer/src/scene/` (grass field), with the palette in `viewer/src/theme.ts`.

Likely causes to check: blades lit by a light that does not reach them, a material that ignores the
scene lighting, vertex colours defaulting to black, or blades simply far too tall for the body.

**What "fixed" means.** The field reads as sunlit wild grass in the warm scene palette. The body is
not buried in it at orbit level. Screenshot at `#sp=rat&lod=orbit`.

Constraint 2 still applies: this is scene, so the warm palette is correct here.

Performance note from the handoff, worth acting on if it is cheap while you are in this file:
`GrassField` rewrites all 6,000 blade instance matrices every frame. If the wind animation can be
moved into the material or the update throttled without changing the look, do it; if not, leave it
and say so.

## Task 5: A Swiss-valley backdrop

**What the user asked for:** "make the background like one of those 360 figures in like a swiss
valley" — a surrounding panoramic alpine scene rather than the current flat orange sky band with a
few dark cubes on the horizon.

**Direction (user, overriding an earlier controller ruling): "for the valley just find a 360 image."**
Use a real 360 equirectangular photograph of an alpine/Swiss valley as an environment map, not a
procedural mountain ring. The earlier procedural ruling is withdrawn.

**Licensing — this is the one hard requirement.** The image must be **CC0 or public domain**.
[Poly Haven](https://polyhaven.com/hdris/nature) publishes CC0 HDRIs and equirectangular photos and
is the expected source; `kloofendal_43d_clear`, `alps_field`, `mountain_*` and similar are the right
kind of thing. Verify the licence on the asset page before committing. Add a short
`viewer/public/env/CREDITS.md` recording the asset name, author, source URL, and licence, even for
CC0 where attribution is not legally required.

**Where.** `viewer/src/scene/Sky.tsx`, `viewer/src/theme.ts` for palette, and the image under
`viewer/public/`.

**Implementation notes.**
- drei's `<Environment />` with `background` is the straightforward route and drei is already a
  dependency; `useTexture` + a large inverted sphere with `THREE.EquirectangularReflectionMapping`
  also works. Either is fine.
- **Watch the file size.** The bundle is already 1.14 MB. A 4K HDR `.hdr` can be 10-40 MB; do not
  commit one. Prefer a 2K JPEG (`.jpg`) equirectangular, ideally under ~2 MB. If you can only find
  the right image as an HDR, downsample and convert it, and say so in your report.
- The scene palette is a warm golden hour. Pick an image whose light direction and warmth are
  consistent with the existing lighting, or retune the lights to match the plate — a body lit from
  the opposite side to the sky is the main way this goes wrong.
- Keep the low-pixel aesthetic of the bodies and grass intact; the photographic backdrop sitting
  behind voxel geometry is the intended contrast, but make sure the body still reads clearly against
  it rather than getting lost in a busy plate.

**What "done" means.** Looking around at orbit level in any direction shows a coherent alpine valley
with a real horizon — it must work at every azimuth, which is the point of using a 360 image. Check
at least three camera azimuths. The body remains clearly readable against it. Screenshots from three
angles in your report, plus the licence line from CREDITS.md.

## Task 6: Morphology imagery in its own configurable window

**What the user asked for:** "the image should be in it's own window that is user configurable."

The morphology crops currently render as tiles in the 3D scene at cell level. The user wants the
image surfaced in a panel the user can control.

**Scope, from the user (2026-08-15), stated three times and consistently:**
- "the data should be in it's own window whose position should be locked to the frame, not the 3d viewer"
- "i want a floating panel for the spatial information"
- "floating panel should be 2D locked to the frame, not in the 3d viewer"

So this is not only the morphology imagery — it is **the spatial information** for the selected
sample: the metadata the right-hand column shows today (organism, tissue, disease, technology,
spatial unit, cell/spot count, section extent, donor, panel) together with the morphology imagery
when there is any. It lives in a floating 2D panel, not the fixed right-hand column and not the 3D
scene.

That settles the one real design question here. The window is a **DOM overlay in screen space**,
positioned relative to the viewport frame — *not* an object in the three.js scene, and not a thing
that tracks a point in 3D. Orbiting, zooming, or flying between levels must not move it. It stays
where the user put it while the scene moves underneath.

Concretely: an absolutely-positioned element over the canvas, offsets anchored to a frame corner so
it survives window resizes, drag and resize in CSS pixels. Do not use drei's `<Html>` or any
scene-graph attachment — that is exactly the "locked to the 3d viewer" behaviour being ruled out.

**Requirements.**
- Shows the selected sample's **spatial information** — the metadata fields the panel renders today —
  plus its morphology imagery where the sample has any.
- Position is locked to the frame: unaffected by camera movement, orbit, or zoom level changes.
- Draggable, resizable, closable, and re-openable from a control.
- **Also fix, since you own `panel/` for this task:** `speciesCrumbLabel` in
  `viewer/src/panel/Panel.tsx:25-26` is `species === "rat" ? "🐀 Rat" : "🧍 Human"`, so with the
  zebrafish selected the back control reads "Back to 🧍 Human". Export `SPECIES_CRUMB` from
  `App.tsx` and use it, collapsing the two-sources-of-truth for that emoji+name string. This defect
  was created by a merge, not by the panel's author.
- The atlas now has a twelve-section organ (brain) and two spatial units (Xenium cells, Visium
  spots), so check the panel against both a 587k-cell Xenium sample and a ~4k-spot Visium one.
- Its geometry (position, size, open/closed) is part of the URL state, like everything else in this
  viewer — see `viewer/src/url/` and the `ViewerState` contract in `viewer/src/types.ts`. Follow the
  existing codec conventions: defaults omitted from the hash, malformed values fall back to the
  default rather than breaking the page.
- Add codec tests alongside the existing URL tests for the new fields.
- Constraint 2 applies: the image is measured data, so it is presented undecorated.

## Task 7: Zebrafish as a third species

The user asked for a zebrafish. Today `Species` is `"human" | "rat"`.

**Where.** `src/somics/viewer/anatomy.py` (per-species blob geometry and bounds),
`viewer/src/types.ts` (the `Species` union), `viewer/src/body/silhouette.ts` (signed-distance body),
and the species toggle in the UI.

**Requirements.**
- A zebrafish signed-distance silhouette: fusiform body, tail fin, dorsal and anal fins, large eye.
  Procedural, per constraint 4.
- Zebrafish organ blobs in `anatomy.py` with anchors, colours and systems consistent with how the rat
  and human are authored. Use organ ids that already exist in the 30-organ vocabulary where the
  anatomy is homologous (brain, eye, heart, liver, intestine, kidney, spleen, gill/lung...), so
  `resolve_tissue` keeps working. Do not invent a parallel vocabulary.
- The body toggle offers rat / human / zebrafish, and `sp=zebrafish` round-trips through the URL.
- **Every organ must claim at least one voxel** at the configured `VOXEL` size. There is an existing
  test, `test_every_organ_claims_voxels`, that pins this for the other two bodies — extend it to
  cover zebrafish. This was a real bug before: at a coarser voxel size several organs rounded away to
  nothing.
- The zebrafish frames sensibly at orbit level (it is a small animal — check `focusFor`'s distance
  floor behaves).
- Python tests for the new anatomy; TS tests for the union and codec.
- Screenshot of `#sp=zebrafish&lod=orbit` in your report.

## Task 8: A back button in the right-hand panel

**What the user asked for (2026-08-15):** "right hand panel should have a back button to go up a
level."

**The gap.** Going *up* a level is already implemented and already correct — `zoomOut()` in
`viewer/src/state.ts:174` steps cell -> section -> organ -> orbit and clears the right state at each
step (dropping `sample`, `points`, `geneValues` when leaving section). But its only binding is the
Escape key, in `viewer/src/App.tsx:54`. There is no visible control, so nobody discovers it.

**Requirements.**
- A back control in the right-hand panel (`viewer/src/panel/Panel.tsx`, `Panel.css`) that goes up one
  zoom level.
- **Call the existing `zoomOut()`.** Do not reimplement the level-stepping or the state cleanup, and
  do not change `zoomOut`'s behaviour — the Escape key and the button must do the same thing.
- It should say where it goes, not just "back" — at section level it is going up to the organ, at
  cell level up to the section. The breadcrumb at the top of the screen already names these levels;
  be consistent with the words it uses.
- At `orbit` there is nowhere to go up to. Hide it or disable it — your call, but it must not be a
  dead control that looks live.
- Match the existing panel styling. The panel is a dark column with small-caps labels; the existing
  controls (`copy link`, the pixel and cell sliders) set the idiom.
- The atlas now has an organ with twelve sections (brain), so check the button at every level with
  both a single-sample organ (colon) and a multi-sample one (brain).

**What "done" means.** From cell level, four clicks walks you back out to orbit, with the panel
content correct at each step. Screenshot of the button at section level in your report.

## Task 9: Higher-resolution voxel bodies

**What the user asked for (2026-08-15):** "can the 3d models be higher resolution?"

**Where.** `VOXEL = 0.26` in `viewer/src/theme.ts` is the sampling size; `buildBodyVoxels` in
`viewer/src/body/voxelize.ts` samples the signed-distance silhouette over each body's world-space
bounds at that size and emits a shell plus per-organ voxel fields for instanced rendering. Smaller
voxel means more, finer cubes and a silhouette that reads as the actual animal instead of a lumpy
approximation.

**Facts you need, already measured — do not rediscover them.**

- **There is a grid cap.** `voxelize.ts` has `MAX_GRID_SAMPLES` and throws past it with "use a larger
  voxel size". Grid samples scale as the **cube** of the resolution increase.
- At `0.26`: the human (bounds 9 x 18 x 5) is about 35 x 69 x 19 ≈ 46k samples; the zebrafish was
  measured at 74 x 21 x 10 = 15,540 against a 400k cap.
- Halving to `0.13` puts the human at roughly 362k — right against that cap. `0.15`–`0.18` gives
  4–5x the cubes with real headroom.
- **Cube counts at 0.26:** rat 2,241 shell + 1,431 organ; human 2,523 + 2,652. Build ~23 ms.

**Requirements.**

- Pick a resolution by looking at the result, not by lowering the constant until something throws.
  Say where you landed and why.
- **Budget and report the new build time.** This runs once per (anatomy, species) in a `useMemo`, not
  per frame, so a few hundred ms is fine; several seconds is not, because it blocks the body appearing
  when the user switches species. Measure it, don't estimate.
- Report the new cube counts per body, and the frame rate at orbit if you can measure it. Instanced
  meshes handle tens of thousands of cubes, but this is the change most likely to cost frames.
- If the cap needs raising, raise it deliberately and justify the new number.
- **Keep the low-pixel aesthetic.** The user asked for higher resolution, not smooth meshes. There is
  a floor below which it stops reading as voxel art and becomes a noisy blob — find it by looking.
- **Watch the per-organ voxel floor.** `test_every_organ_claims_voxels` exists because at a coarser
  size several organs rounded away to nothing. Going finer only helps, but confirm organs stay
  distinguishable rather than dissolving into speckle — the fish's smallest organs currently claim as
  few as 2–3 voxels.
- The `pixels` slider in the UI controls render-buffer pixelation. That is a **different** thing from
  voxel size; do not conflate them.

**What "done" means.** Rat, human and zebrafish all read more clearly as the animals they are, at a
measured build cost. The zebrafish is the sharpest test: its fins, tail and snout are shell-only and
are the first things to disappear at a coarse voxel size. Before/after screenshots of all three.

## Task 10: Replace the voxel rat with the photoreal Poly Haven mesh

**What the user asked for (2026-08-15):** after twice asking for higher-resolution bodies, they
downloaded a model and said **"just use this guy instead: /Users/gjohnson/Downloads"** — Poly Haven's
`street_rat`, 4K glTF. Offered the choice between voxelizing that mesh (keeping the low-pixel look)
and rendering it photoreal, they chose **photoreal, explicitly accepting that it breaks the voxel
aesthetic and forces a rethink of the organ view.**

**Source.** `/Users/gjohnson/Downloads/rat/`
- `source/street_rat_4k_gltf.zip` — `street_rat_4k.gltf` (2.7 KB), `street_rat.bin` (1.37 MB
  geometry), and 4K JPEG textures totalling ~29 MB
- `textures/` — the same maps loose, including an `arm` map already split to `@channels=G`

**Constraint 5 is amended again for this task.** Bodies were procedural for licensing reasons; the
user has now supplied a licensed model. The rat may ship as an asset. **Human and zebrafish stay
procedural** — we have models for neither, and this task does not touch them.

### The hard part: the organs must survive

This viewer exists to let you click an organ and fly into its data. Organs are authored in
`src/somics/viewer/anatomy.py` as ellipsoid blobs positioned in each body's world-space `bounds`, and
today they are rendered *inside* a translucent voxel shell. An opaque photoreal rat hides them
completely, which would delete the application's core interaction.

**Ruling (controller):** the mesh replaces the *shell*, not the organs. Render the rat mesh
translucent — a glass body with the existing organ blobs glowing inside, which is exactly the
relationship the voxel shell had at `SHELL_OPACITY = 0.16`, just with better geometry. The organ
blobs, pins, hover, and click-to-fly behaviour all stay as they are. If translucency on this mesh
looks bad, a cutaway or a toggle between solid and glass is an acceptable alternative — but organs
being visible and clickable is not negotiable, and a solid rat with no way to see inside fails this
task. Say what you chose and show it.

**The alignment problem, which is the real work.** Organ anchors are defined against the rat's
declared `bounds` in `anatomy.py`. The downloaded mesh has its own units, origin, and orientation, and
they will not match. You must fit the mesh into those same bounds — scale, centre, and rotate it so
that the existing organ blobs land in anatomically sensible places inside it (brain in the head, colon
in the abdomen, not floating outside the body). **Verify this visually, organ by organ**, and treat
"an organ pokes outside the rat" as a failure. Prefer transforming the mesh to fit the existing
anatomy over rewriting every organ anchor.

### Requirements

- **Verify the licence.** Poly Haven publishes CC0, and the naming convention here matches, but check
  the asset page before committing. Add `viewer/public/models/CREDITS.md` with asset, author, source
  URL and licence — same as `viewer/public/env/CREDITS.md` does for the valley plate.
- **Downsample the textures.** The bundle is ~1.14 MB today and the valley plate already added 1.25 MB.
  29 MB of 4K maps is not acceptable. Go to 1K or 2K, prefer a single combined `.glb`, and target a
  few MB total. State the final committed size. If a map is not visible at the sizes the rat is
  actually rendered, drop it entirely rather than shipping it small.
- Keep the existing lighting coherent — the scene is lit from the valley plate's own sun.
- The species toggle keeps all three bodies working; only the rat changes.
- `npx tsc --noEmit` clean, `npm run test` passing.

**What "done" means.** `#sp=rat&lod=orbit` shows a recognisable photoreal rat, correctly scaled and
oriented in the grass, with its organs visible inside it and still clickable. `#sp=human&lod=orbit`
and `#sp=zebrafish&lod=orbit` are unchanged. Screenshots of all three, plus a close view showing
organs inside the rat.

## Suggested order

1, 2, 3 are entangled (camera, placement, scale) and are the difference between a working app and a
broken one — do them first and in that order. 4 and 5 are scenery and touch the same directory, so 5
should read 4's report. 6 and 7 are additive and independent.
