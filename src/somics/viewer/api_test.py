import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from somics.viewer.api import META_HEADER, app, get_source
from somics.viewer.atlas_source import GeneNotFound, SampleNotFound

SECTION = "sec-colon-1"
N_CELLS = 1_200


class FakeSource:
    """Stands in for AtlasSource so the suite never reaches R2."""

    def __init__(self):
        self.gene_calls: list[tuple[str, str]] = []

    def samples(self):
        return [
            {
                "section_uid": SECTION,
                "section_id": "hColon_Cancer_Add_on_FFPE",
                "node_id": "colon",
                "tissue": "colon",
                "organism": "Homo sapiens",
                "species": "human",
                "n_cells": N_CELLS,
                "technology": "xenium",
                "has_morphology_crop": True,
                "extent_um": [0.0, 0.0, 900.0, 700.0],
            }
        ]

    def sample(self, section_uid):
        if section_uid != SECTION:
            raise SampleNotFound(section_uid)
        return self.samples()[0]

    def point_cloud(self, section_uid, max_points):
        self.sample(section_uid)
        n = min(max_points, N_CELLS)
        rng = np.random.default_rng(0)
        blocks = [rng.random(n).astype(np.float32) for _ in range(3)]
        meta = {"n_points": n, "n_cells": N_CELLS, "extent_um": [0.0, 0.0, 900.0, 700.0]}
        return b"".join(block.tobytes() for block in blocks), meta

    def crops(self, section_uid, x_um, y_um, radius_um, limit):
        self.sample(section_uid)
        return [
            {
                "uid": f"c{i}",
                "x_um": x_um,
                "y_um": y_um,
                "width_um": 27.2,
                "height_um": 27.2,
                "kind": "morphology",
                "label": "Morphology",
            }
            for i in range(min(limit, 3))
        ]

    def genes(self, section_uid):
        self.sample(section_uid)
        return ["EPCAM", "MKI67", "PTPRC"]

    def gene_values(self, section_uid, gene, max_points):
        self.sample(section_uid)
        if gene not in self.genes(section_uid):
            raise GeneNotFound(gene)
        self.gene_calls.append((section_uid, gene))
        n = min(max_points, N_CELLS)
        return np.zeros(n, dtype=np.float32).tobytes(), {"gene": gene, "n_points": n}


@pytest.fixture
def fake():
    source = FakeSource()
    app.dependency_overrides[get_source] = lambda: source
    yield source
    app.dependency_overrides.clear()


@pytest.fixture
def client(fake):
    return TestClient(app)


def test_anatomy_serves_every_body(client):
    body = client.get("/api/anatomy").json()
    assert body["species"] == ["human", "rat", "zebrafish"]
    for species in body["species"]:
        organs = body["bodies"][species]["organs"]
        assert any(organ["node_id"] == "colon" for organ in organs)
        assert len(body["bodies"][species]["bounds"]) == 2


def test_anatomy_needs_no_atlas():
    # No dependency override: the route must not touch the data source at all.
    assert TestClient(app).get("/api/anatomy").status_code == 200


def test_samples_carries_its_organ_node(client):
    rows = client.get("/api/samples").json()
    assert [row["node_id"] for row in rows] == ["colon"]


def test_sample_detail(client):
    assert client.get(f"/api/samples/{SECTION}").json()["section_id"].startswith("hColon")


def test_missing_sample_is_404(client):
    assert client.get("/api/samples/nope").status_code == 404
    assert client.get("/api/samples/nope/points").status_code == 404
    assert client.get("/api/samples/nope/genes").status_code == 404


def test_points_returns_three_float32_blocks(client):
    response = client.get(f"/api/samples/{SECTION}/points", params={"max_points": 1000})
    assert response.headers["content-type"] == "application/octet-stream"
    meta = json.loads(response.headers[META_HEADER])
    assert meta["n_points"] == 1000
    assert len(response.content) == 3 * 4 * meta["n_points"]
    assert np.frombuffer(response.content, dtype=np.float32).size == 3 * meta["n_points"]


def test_points_rejects_absurd_budgets(client):
    assert client.get(f"/api/samples/{SECTION}/points", params={"max_points": 5}).status_code == 422
    assert (
        client.get(f"/api/samples/{SECTION}/points", params={"max_points": 10_000_000}).status_code
        == 422
    )


def test_crops_window(client):
    body = client.get(
        f"/api/samples/{SECTION}/crops",
        params={"x_um": 400, "y_um": 300, "radius_um": 120, "limit": 2},
    ).json()
    assert len(body["tiles"]) == 2
    assert body["tiles"][0]["width_um"] == pytest.approx(27.2)
    # The kind survives the HTTP boundary: the panel must not have to guess.
    assert body["tiles"][0]["kind"] == "morphology"
    assert body["tiles"][0]["label"] == "Morphology"


def test_crops_requires_a_position(client):
    assert client.get(f"/api/samples/{SECTION}/crops").status_code == 422


def test_gene_list(client):
    assert "EPCAM" in client.get(f"/api/samples/{SECTION}/genes").json()["genes"]


def test_gene_values_align_with_points(client, fake):
    points = client.get(f"/api/samples/{SECTION}/points", params={"max_points": 1000})
    values = client.get(f"/api/samples/{SECTION}/genes/EPCAM", params={"max_points": 1000})
    n_points = json.loads(points.headers[META_HEADER])["n_points"]
    assert json.loads(values.headers[META_HEADER])["n_points"] == n_points
    assert len(values.content) == 4 * n_points
    assert fake.gene_calls == [(SECTION, "EPCAM")]


def test_unknown_gene_is_404(client):
    assert client.get(f"/api/samples/{SECTION}/genes/NOTAGENE").status_code == 404
