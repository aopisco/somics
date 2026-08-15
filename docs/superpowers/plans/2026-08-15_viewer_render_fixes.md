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
3. **The live R2 atlas is the viewer's only data source.** Confirmed by the user on 2026-08-15, after
   `data/datasets.csv` and `data/model_dataset_usage.csv` landed on main: "r2 only". Do not blend,
   fall back to, or seed the body from `literature_datasets.csv`, `datasets.csv`, or any other CSV.
   The atlas holds one sample, so one organ lights and 29 render as inert sockets — that is correct
   and intended, not a gap to paper over.
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

## Task 2: Make section and cell render their data

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

**Scope decision (controller ruling):** build it as a floating, in-page window over the canvas —
draggable by its title bar, resizable from a corner, closable and re-openable — not an OS popup
window. Cost if wrong: the user wanted a real browser window and we rebuild the shell; the contents
and the plumbing carry over either way.

**Requirements.**
- Appears at cell level with the sample's morphology crops in it.
- Draggable, resizable, closable, and re-openable from a control.
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

## Suggested order

1, 2, 3 are entangled (camera, placement, scale) and are the difference between a working app and a
broken one — do them first and in that order. 4 and 5 are scenery and touch the same directory, so 5
should read 4's report. 6 and 7 are additive and independent.
