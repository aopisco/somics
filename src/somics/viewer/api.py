"""HTTP surface for the viewer.

The browser cannot read LanceDB or Zarr on R2, so this service is the bridge: JSON for
metadata, raw float32 buffers for anything per-cell, base64 PNGs for imagery. Buffer
metadata travels in the `X-Somics-Meta` header rather than a wrapper object, so the
response body is a bare typed array the frontend can hand straight to WebGL.
"""

import json
from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from somics.viewer.anatomy import BODY_BOUNDS, SPECIES, organ_payload
from somics.viewer.atlas_source import AtlasConfig, AtlasSource, GeneNotFound, SampleNotFound
from somics.viewer.control import ControlChannel, sanitize_patch
from somics.viewer.paths import WEB_DIST

META_HEADER = "X-Somics-Meta"

_source: AtlasSource | None = None

channel = ControlChannel()


def get_source() -> AtlasSource:
    """The process-wide atlas reader. Overridden in tests."""
    global _source
    if _source is None:
        _source = AtlasSource(AtlasConfig.from_env())
    return _source


Source = Annotated[AtlasSource, Depends(get_source)]

app = FastAPI(title="somics viewer", docs_url="/api/docs", openapi_url="/api/openapi.json")


def _buffer(payload: bytes, meta: dict) -> Response:
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={META_HEADER: json.dumps(meta), "Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/anatomy")
def anatomy() -> dict:
    """Organ geometry for both bodies. Static — the frontend fetches it once."""
    return {
        "species": list(SPECIES),
        "bodies": {
            name: {
                "bounds": [list(BODY_BOUNDS[name][0]), list(BODY_BOUNDS[name][1])],
                "organs": organ_payload(name),
            }
            for name in SPECIES
        },
    }


@app.get("/api/samples")
def samples(source: Source) -> list[dict]:
    """Every section in the atlas, with the organ node each one lands on."""
    return source.samples()


@app.get("/api/samples/{section_uid}")
def sample(section_uid: str, source: Source) -> dict:
    try:
        return source.sample(section_uid)
    except SampleNotFound:
        raise HTTPException(status_code=404, detail=f"no sample {section_uid}") from None


@app.get("/api/samples/{section_uid}/points")
def points(
    section_uid: str,
    source: Source,
    max_points: Annotated[int, Query(ge=1_000, le=400_000)] = 80_000,
) -> Response:
    """Decimated cell positions: float32 x, then y, then n_counts."""
    try:
        payload, meta = source.point_cloud(section_uid, max_points)
    except SampleNotFound:
        raise HTTPException(status_code=404, detail=f"no sample {section_uid}") from None
    return _buffer(payload, meta)


@app.get("/api/samples/{section_uid}/crops")
def crops(
    section_uid: str,
    source: Source,
    x_um: float,
    y_um: float,
    radius_um: Annotated[float, Query(gt=0, le=5_000)] = 150.0,
    limit: Annotated[int, Query(ge=1, le=64)] = 24,
) -> dict:
    """Morphology tiles near a point in the section's own micron frame."""
    try:
        tiles = source.crops(section_uid, x_um, y_um, radius_um, limit)
    except SampleNotFound:
        raise HTTPException(status_code=404, detail=f"no sample {section_uid}") from None
    return {"section_uid": section_uid, "tiles": tiles}


@app.get("/api/samples/{section_uid}/genes")
def genes(section_uid: str, source: Source) -> dict:
    try:
        return {"section_uid": section_uid, "genes": source.genes(section_uid)}
    except SampleNotFound:
        raise HTTPException(status_code=404, detail=f"no sample {section_uid}") from None


@app.get("/api/samples/{section_uid}/genes/{gene}")
def gene_values(
    section_uid: str,
    gene: str,
    source: Source,
    max_points: Annotated[int, Query(ge=1_000, le=400_000)] = 80_000,
) -> Response:
    """Per-cell counts for one gene, aligned to the point buffer's decimation.

    Cold requests read the whole gene column off R2 and take tens of seconds.
    """
    try:
        payload, meta = source.gene_values(section_uid, gene, max_points)
    except SampleNotFound:
        raise HTTPException(status_code=404, detail=f"no sample {section_uid}") from None
    except GeneNotFound:
        raise HTTPException(status_code=404, detail=f"{gene} is not in this panel") from None
    return _buffer(payload, meta)


@app.get("/api/control")
def control() -> dict:
    """The latest drive command, plus whatever the browser last reported showing."""
    return {**channel.latest.as_dict(), "state": channel.browser_state}


@app.post("/api/control")
async def drive(payload: Annotated[dict, Body()]) -> dict:
    """Push a partial viewer state to every connected browser.

    Unknown or malformed keys are dropped rather than rejected, and the response echoes
    what survived, so a caller can see exactly what it managed to set.
    """
    requested = payload.get("patch")
    patch = sanitize_patch(requested)
    note = payload.get("note")
    actor = payload.get("actor")
    message = channel.publish(
        patch,
        note if isinstance(note, str) else None,
        actor if isinstance(actor, str) else None,
    )
    dropped = sorted(set(requested) - set(patch)) if isinstance(requested, dict) else []
    return {**message.as_dict(), "dropped": dropped, "listeners": channel.n_subscribers}


@app.get("/api/control/stream")
async def control_stream() -> StreamingResponse:
    """Server-Sent Events: one message per drive command."""
    return StreamingResponse(
        channel.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.put("/api/control/state")
async def report_state(state: Annotated[dict, Body()]) -> dict:
    """The browser reporting what it is currently showing, so an agent can read it back."""
    channel.browser_state = sanitize_patch(state)
    return {"ok": True}


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
