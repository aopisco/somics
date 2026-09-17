# somics viewer

A browsable 3D view of the somics spatial atlas: a low-resolution voxel rat, human or zebrafish
standing in a photographic alpine valley, with a glowing pin on every organ the atlas holds data for.
Clicking a pin selects that organ's samples; the measured data itself is drawn in a floating 2D
panel, not in the 3D scene.

| Level | What you see |
|---|---|
| `orbit` | The whole body, turning slowly, pins on the organs |
| `organ` | One organ's samples |
| `section` | The section's cells or spots, as a 2D plot in the panel, coloured by transcript count |
| `cell` | The section's imagery in the panel, placed at the point you clicked |

**Measured data lives in the panel, the body lives in 3D.** The section is a flat thing, so it is
drawn flat: a canvas plot of every cell or spot, viridis by transcript count and magma by gene. The
3D scene stays on the body, its organs and its pins. Clicking a point in the plot places the imagery
view at those microns.

New ingests light up automatically: a sample's `tissue` label is matched to an organ by
[`anatomy.py`](../src/somics/viewer/anatomy.py), which routes 278 free-text tissue spellings onto 30
clickable organs. As of 2026-08-15 the atlas holds 57 sections across three of them — query
`/api/samples` rather than trusting a number in a document, because this has been wrong three times:

| organ | technology | sections | unit |
|---|---|---|---|
| `brain` | visium | 12 | ~4k spots each |
| `colon` | xenium | 1 | 587,115 cells |
| `lung` | codex / cosmx | 36 / 8 | morphology |

## Imagery

Sections carry one of two kinds, and they are not interchangeable:

- **H&E** — stained colour, on the twelve Visium brain sections
- **Morphology** — detector intensity, greyscale, on the other 45

`/api/samples/{uid}/crops` returns whichever the section has, and every tile carries a `kind` and a
server-supplied `label`, so nothing downstream has to guess which stain it is looking at.

## The floating panel

The spatial information — metadata, the cell/spot plot, and the imagery — sits in a window that
floats over the canvas in screen space. It is a DOM overlay, not attached to the 3D scene, so
orbiting and zooming leave it exactly where you put it. Drag it by the title bar, resize it from the
corner, close it, and reopen it from the `panel` chip. Its position, size and open state live in the
URL with everything else.

## Run it against the current atlas (73M rows, 983 sections)

The API was written for a 587k-cell atlas and scanned the whole obs table for
its sample index and per-section coordinates. At the current size that is
minutes and gigabytes per request, so those two scans are precomputed once, on
EC2 next to the bucket, and the API reads the result per section:

```bash
# once per new atlas prefix (r5.4xlarge, ~30 min): writes
#   s3://somics-dev/viewer_cache/<stamp>/{samples.json, coords/<section_uid>.parquet, corpus_index.json}
export SOMICS_ATLAS_DIR=s3://somics-dev/ingest/<family>/atlas/<stamp> SOMICS_BRANCH=protein-adapters
curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/build_viewer_cache_ec2.sh | bash   # as EC2 user-data

# then, on the laptop: an SSO session, exported so lance and pyarrow see it.
# data/atlas_pointer.json names the current prefix and index; no env vars needed.
eval "$(aws configure export-credentials --profile sci-data-dev-poweruser --format env)"
uv run python -m somics.viewer          # API on http://127.0.0.1:8787, serves viewer/dist at / and web/dist at /corpus/
# (after a new index build: update data/atlas_pointer.json and copy its corpus_index.json into data/)
```

Crops and gene painting still read the atlas live, so the first crop request
on a section costs a filtered scan of 73M rows (seconds on EC2, longer on a
laptop link). To run the API in-region instead, start it on an EC2 box and
forward the port over SSM: `aws ssm start-session --target <id>
--document-name AWS-StartPortForwardingSession --parameters
'{"portNumber":["8787"],"localPortNumber":["8787"]}'`.

The atlas is private by decision (2026-09-14) and is not published to R2; the
public `epiblast-public` copy is the frozen 59-section hackathon atlas, reachable
by setting `SOMICS_ATLAS_DIR=s3://epiblast-public/somics_spatial_atlas` and
`SOMICS_ATLAS_STORE=r2`.

## Run it (hackathon atlas on R2)

Two processes. The API reads the atlas over Cloudflare R2 and needs no credentials of its own.

```bash
uv run python -m somics.viewer          # API on http://127.0.0.1:8787
cd viewer && npm install && npm run dev  # UI on http://127.0.0.1:5273
```

Open http://127.0.0.1:5273. The first organ click takes a few seconds — the section's cells are
being read off R2 — and is instant afterwards.

To serve the UI from the API as a single process instead:

```bash
cd viewer && npm run build
uv run python -m somics.viewer          # now also serves viewer/dist at /
```

## The URL is the whole state

Every view is a link. Species, selected organ, selected sample, zoom level, camera position and
target, painted gene, cell budget and pixel scale all live in the hash, so copying the URL hands
someone the exact view you are looking at. The `copy link` button does this; hand-editing the hash
works too.

```
#sp=rat&n=colon&s=183c734af72b51e0&lod=section&g=EPCAM&p=gene&b=80000&cam=1.24,3.51,5.62&tgt=0,1.2,0
```

Defaults are omitted, so an untouched view has a clean URL. Malformed values fall back to their
default rather than breaking the page.

## Driving it from an agent

The viewer can be operated over HTTP while a human watches. An agent POSTs a partial state and a
note; every open browser applies it and shows a banner naming who is driving.

```bash
uv run python -m somics.viewer.drive --node colon --lod section --note "having a good sniff"
uv run python -m somics.viewer.drive --state    # read back what the browser is showing
uv run python -m somics.viewer.drive --tour     # a scripted six-step tour
```

Or straight over HTTP:

```bash
curl -X POST localhost:8787/api/control \
  -H 'content-type: application/json' \
  -d '{"patch": {"gene": "EPCAM", "paint": "gene"}, "note": "painting EPCAM", "actor": "claude"}'
```

Unknown or malformed keys are dropped on both ends and the response lists what it dropped, so a
typo is visible rather than silent.

## Layout

```
viewer/src
  types.ts state.ts api.ts theme.ts    contracts, zustand store, API client, palette
  body/                                signed-distance silhouettes, voxelizer, instanced bodies
  scene/                               sky, grass field, pollen
  camera/                              zoom-level model, camera rig
  layers/                              point cloud, morphology tiles, colormaps
  markers/                             organ pins
  panel/                               sample detail column
  url/                                 state codec, history sync
  agent/                               control-stream client
  whimsy/                              shared motion curves
```

## Tests

```bash
npm run test        # 137 tests, pure logic only
npm run typecheck
uv run pytest src/somics/viewer   # 107 tests, no network
```

The suites cover what can be checked without a GPU: the voxelizer, the zoom-level model, the URL
codec, the colormaps, the motion curves, the control-message parser, and the API against a fake
atlas. Rendering is not covered — verify visually.

## Notes

- The atlas is a fixed read-only snapshot, so everything the API reads is cached for the process
  lifetime. First request warms it: ~4 s to check out, ~2.5 s per section scan.
- Painting a gene reads that gene's whole column off R2, which takes ~49 s the first time and is
  then cached on disk under `~/.cache/somics-viewer`. The UI says so while it waits.
- The canvas renders at a fraction of device resolution and is upscaled with nearest-neighbour
  filtering — that is where the pixel look comes from. The `pixels` slider controls it.
- Cell colours are viridis and magma; the warm palette is reserved for the scene. Measured data is
  never coloured decoratively.
