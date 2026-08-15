import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from somics.viewer import control
from somics.viewer.api import app, channel, get_source
from somics.viewer.api_test import FakeSource
from somics.viewer.control import ControlChannel, sanitize_patch


@pytest.fixture
def client():
    app.dependency_overrides[get_source] = lambda: FakeSource()
    channel.revision = 0
    channel.browser_state = None
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestSanitizePatch:
    def test_keeps_every_known_key(self):
        patch = {
            "species": "rat",
            "node": "colon",
            "sample": "sec-1",
            "lod": "section",
            "gene": "EPCAM",
            "paint": "gene",
            "camera": {"position": [1, 2, 3], "target": [0, 0, 0]},
            "focusUm": [4679.2, 3384.6],
            "budget": 80_000,
            "pixel": 0.4,
            "sound": True,
        }
        assert sanitize_patch(patch) == {
            **patch,
            "camera": {"position": [1.0, 2.0, 3.0], "target": [0.0, 0.0, 0.0]},
            "focusUm": [4679.2, 3384.6],
            "pixel": 0.4,
        }

    def test_drops_unknown_keys(self):
        assert sanitize_patch({"node": "colon", "wat": 1, "__proto__": "x"}) == {"node": "colon"}

    def test_rejects_bad_enums_but_keeps_good_neighbours(self):
        assert sanitize_patch({"species": "axolotl", "lod": "section"}) == {"lod": "section"}

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(10, 1_000), (999_999, 400_000), (80_000, 80_000), (80_000.7, 80_000)],
    )
    def test_clamps_budget(self, value, expected):
        assert sanitize_patch({"budget": value}) == {"budget": expected}

    @pytest.mark.parametrize(("value", "expected"), [(0.0, 0.15), (9.0, 1.0), (0.4, 0.4)])
    def test_clamps_pixel(self, value, expected):
        assert sanitize_patch({"pixel": value}) == {"pixel": expected}

    @pytest.mark.parametrize("value", ["80000", None, True, [1]])
    def test_rejects_non_numeric_budget(self, value):
        assert sanitize_patch({"budget": value}) == {}

    @pytest.mark.parametrize(
        "camera",
        [
            {"position": [1, 2], "target": [0, 0, 0]},
            {"position": [1, 2, 3]},
            {"position": [1, 2, "x"], "target": [0, 0, 0]},
            {"position": [1, 2, float("inf")], "target": [0, 0, 0]},
            "over there",
        ],
    )
    def test_rejects_malformed_camera(self, camera):
        assert sanitize_patch({"camera": camera}) == {}

    @pytest.mark.parametrize("focus", [[1], [1, 2, 3], ["a", "b"], [float("nan"), 1]])
    def test_rejects_malformed_focus(self, focus):
        assert sanitize_patch({"focusUm": focus}) == {}

    @pytest.mark.parametrize("key", ["node", "sample", "gene", "camera", "focusUm"])
    def test_explicit_null_is_a_legal_value(self, key):
        assert sanitize_patch({key: None}) == {key: None}

    def test_booleans_are_not_numbers(self):
        assert sanitize_patch({"sound": True}) == {"sound": True}
        assert sanitize_patch({"sound": 1}) == {}

    def test_non_dict_input(self):
        assert sanitize_patch(None) == {}
        assert sanitize_patch([("node", "colon")]) == {}

    def test_does_not_mutate_its_input(self):
        patch = {"node": "colon", "junk": 1}
        sanitize_patch(patch)
        assert patch == {"node": "colon", "junk": 1}


class TestChannel:
    def test_revision_increments_and_latest_tracks(self):
        local = ControlChannel()
        assert local.revision == 0
        local.publish({"node": "colon"}, "a note", "claude")
        message = local.publish({"lod": "section"}, None, None)
        assert (local.revision, message.revision) == (2, 2)
        assert local.latest.patch == {"lod": "section"}

    def test_unsubscribe_removes_the_queue(self):
        local = ControlChannel()
        queue = local.subscribe()
        assert local.n_subscribers == 1
        local.unsubscribe(queue)
        assert local.n_subscribers == 0

    def test_publish_survives_a_full_subscriber_queue(self):
        local = ControlChannel()
        local.subscribe()
        for _ in range(50):
            local.publish({"lod": "orbit"}, None, None)
        assert local.revision == 50


class TestEndpoints:
    def test_drive_echoes_what_survived(self, client):
        body = client.post(
            "/api/control",
            json={"patch": {"node": "colon", "lod": "section", "bogus": 1}, "actor": "claude"},
        ).json()
        assert body["patch"] == {"node": "colon", "lod": "section"}
        assert body["dropped"] == ["bogus"]
        assert body["actor"] == "claude"
        assert body["revision"] >= 1

    def test_drive_rejects_a_non_string_note(self, client):
        body = client.post("/api/control", json={"patch": {}, "note": 42}).json()
        assert body["note"] is None

    def test_control_reports_the_browser_state(self, client):
        client.put("/api/control/state", json={"species": "rat", "lod": "orbit", "junk": 2})
        body = client.get("/api/control").json()
        assert body["state"] == {"species": "rat", "lod": "orbit"}

    def test_control_state_is_none_before_a_browser_reports(self, client):
        assert client.get("/api/control").json()["state"] is None


class TestStream:
    """Reads `stream()` directly rather than through TestClient.

    The generator is infinite by design, and pulling it through TestClient's portal
    deadlocks on close — the test hangs instead of failing.
    """

    @staticmethod
    def _first_chunk(local: ControlChannel) -> str:
        async def read() -> str:
            events = local.stream()
            try:
                return await anext(events)
            finally:
                await events.aclose()

        return asyncio.run(read())

    def test_replays_the_latest_command_to_a_new_listener(self):
        local = ControlChannel()
        local.publish({"node": "liver"}, "a look at the liver", "claude")
        chunk = self._first_chunk(local)
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")
        message = json.loads(chunk.removeprefix("data: "))
        assert message["patch"] == {"node": "liver"}
        assert message["note"] == "a look at the liver"
        assert message["revision"] == 1

    def test_a_fresh_channel_sends_a_keepalive_rather_than_a_phantom_command(self, monkeypatch):
        monkeypatch.setattr(control, "_HEARTBEAT_SECONDS", 0.01)
        assert self._first_chunk(ControlChannel()).startswith(":")

    def test_a_live_subscriber_receives_a_later_command(self):
        async def read() -> str:
            local = ControlChannel()
            events = local.stream()
            waiter = asyncio.ensure_future(anext(events))
            await asyncio.sleep(0)  # let stream() reach its first await and subscribe
            local.publish({"lod": "cell"}, None, None)
            try:
                return await asyncio.wait_for(waiter, timeout=2)
            finally:
                await events.aclose()

        assert '"lod": "cell"' in asyncio.run(read())
