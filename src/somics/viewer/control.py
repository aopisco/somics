"""Control channel that lets an agent drive the viewer while a human watches.

An agent POSTs a partial viewer state plus a note; every connected browser gets it over
Server-Sent Events and applies it, showing a banner naming who is driving. The browser
reports its own state back, so an agent can also ask where the UI currently is.

The shape being passed around is the frontend's `ViewerState` (viewer/src/types.ts).
`ALLOWED_KEYS` below is the server-side half of that contract and has to move with it;
the browser sanitizes again on receipt, since a socket payload is untrusted either way.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SPECIES = frozenset({"human", "rat"})
LODS = frozenset({"orbit", "organ", "section", "cell"})
PAINTS = frozenset({"counts", "gene"})

BUDGET_RANGE = (1_000, 400_000)
PIXEL_RANGE = (0.15, 1.0)

# Keys an agent may set, with the check each one has to pass.
ALLOWED_KEYS: dict[str, str] = {
    "species": "enum_species",
    "node": "opt_str",
    "sample": "opt_str",
    "lod": "enum_lod",
    "gene": "opt_str",
    "paint": "enum_paint",
    "camera": "camera",
    "focusUm": "focus",
    "budget": "budget",
    "pixel": "pixel",
    "sound": "bool",
}

# Bounded so a slow browser cannot make the server buffer without limit. Patches are
# whole-state snapshots, so dropping an intermediate one loses nothing that matters.
_QUEUE_DEPTH = 8

# SSE comment interval. Without it, idle connections die to proxy timeouts.
_HEARTBEAT_SECONDS = 15.0


@dataclass
class ControlMessage:
    revision: int
    patch: dict
    note: str | None = None
    actor: str | None = None

    def as_dict(self) -> dict:
        return {
            "revision": self.revision,
            "patch": self.patch,
            "note": self.note,
            "actor": self.actor,
        }


@dataclass
class ControlChannel:
    """Fan-out of the latest drive command to every connected browser."""

    revision: int = 0
    latest: ControlMessage = field(default_factory=lambda: ControlMessage(revision=0, patch={}))
    browser_state: dict | None = None
    _subscribers: set[asyncio.Queue[str]] = field(default_factory=set)

    def publish(self, patch: dict, note: str | None, actor: str | None) -> ControlMessage:
        """Record a drive command and push it to every subscriber."""
        self.revision += 1
        self.latest = ControlMessage(
            revision=self.revision, patch=sanitize_patch(patch), note=note, actor=actor
        )
        payload = json.dumps(self.latest.as_dict())
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("dropping control message for a subscriber that is behind")
        return self.latest

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_DEPTH)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    @property
    def n_subscribers(self) -> int:
        return len(self._subscribers)

    async def stream(self):
        """SSE body: the current command, then every new one, with heartbeats between."""
        queue = self.subscribe()
        try:
            if self.latest.revision:
                yield _sse(json.dumps(self.latest.as_dict()))
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(payload)
        finally:
            self.unsubscribe(queue)


def _sse(payload: str) -> str:
    return f"data: {payload}\n\n"


def sanitize_patch(patch: object) -> dict:
    """Keep only well-formed, in-range values for keys the viewer actually has."""
    if not isinstance(patch, dict):
        return {}
    clean: dict = {}
    for key, check in ALLOWED_KEYS.items():
        if key not in patch:
            continue
        value = _check(check, patch[key])
        if value is not _REJECT:
            clean[key] = value
    return clean


class _Reject:
    """Sentinel; None is a legal value for the nullable keys, so it cannot double as one."""


_REJECT = _Reject()


def _check(check: str, value: object):
    match check:
        case "opt_str":
            if value is None or isinstance(value, str):
                return value
        case "enum_species":
            if value in SPECIES:
                return value
        case "enum_lod":
            if value in LODS:
                return value
        case "enum_paint":
            if value in PAINTS:
                return value
        case "bool":
            if isinstance(value, bool):
                return value
        case "budget":
            if isinstance(value, int | float) and not isinstance(value, bool):
                return int(min(BUDGET_RANGE[1], max(BUDGET_RANGE[0], value)))
        case "pixel":
            if isinstance(value, int | float) and not isinstance(value, bool):
                return float(min(PIXEL_RANGE[1], max(PIXEL_RANGE[0], value)))
        case "camera":
            if value is None:
                return None
            if isinstance(value, dict):
                position = _vec(value.get("position"), 3)
                target = _vec(value.get("target"), 3)
                if position and target:
                    return {"position": position, "target": target}
        case "focus":
            if value is None:
                return None
            vec = _vec(value, 2)
            if vec:
                return vec
    return _REJECT


def _vec(value: object, length: int) -> list[float] | None:
    if not isinstance(value, list | tuple) or len(value) != length:
        return None
    out = []
    for item in value:
        if not isinstance(item, int | float) or isinstance(item, bool):
            return None
        number = float(item)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        out.append(number)
    return out
