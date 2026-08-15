# somics viewer

A browsable 3D view of the somics spatial atlas: a low-resolution voxel rat (or human) standing in a
grass field, with a glowing pin on every organ the atlas holds data for. Clicking a pin flies the
camera continuously inward through four zoom levels, ending inside real microscopy data.

| Level | What you see |
|---|---|
| `orbit` | The whole body, turning slowly, pins on the organs |
| `organ` | One organ's samples |
| `section` | Every cell in the section as a point, coloured by transcript count |
| `cell` | Morphology image tiles from the microscope, at 0.2125 µm/px |

Snapshot v0 of the atlas holds one sample — a 587,115-cell 10x Xenium section of human colon
adenocarcinoma — so one pin is lit and the other twenty-nine organs render as inert sockets. New
ingests light up automatically: a sample's `tissue` label is matched to an organ by
[`anatomy.py`](../src/somics/viewer/anatomy.py), which routes 278 free-text tissue spellings onto 30
clickable organs.

## Run it

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
