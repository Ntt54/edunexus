"""Contract tests : réglages utilisateur (005-platform-ui-library).

GET/PUT /api/tutor/settings — merge partiel des options d'inférence,
réglages pédagogiques, persistance immédiate et inter-restarts.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


def _make_embed_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inputs = body.get("input", [])
        vecs = [[float((i * 3 + j) % 5) / 5 for j in range(dim)]
                for i in range(len(inputs))]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


class ScriptedClient(web_server.OllamaClient):
    def __init__(self, *a, **k):
        super().__init__(transport=_make_embed_transport(4))


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def test_get_settings_shape(client):
    r = client.get("/api/tutor/settings")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["options"], dict)
    assert set(data["tutor"]) == {
        "think", "socratic", "level", "top_k",
        "llm_provider", "llm_base_url", "llm_api_key",
    }


def test_put_merges_options_without_loss(client):
    r = client.put("/api/tutor/settings",
                   json={"options": {"num_thread": 3}})
    assert r.status_code == 200
    assert r.json()["options"]["num_thread"] == 3

    # Merge partiel : une seconde écriture ne perd pas la première clé
    client.put("/api/tutor/settings",
               json={"options": {"temperature": 0.5}})
    data = client.get("/api/tutor/settings").json()
    assert data["options"]["num_thread"] == 3
    assert data["options"]["temperature"] == 0.5


def test_put_unknown_option_400(client):
    r = client.put("/api/tutor/settings",
                   json={"options": {"option_inexistante": 1}})
    assert r.status_code == 400


def test_put_bad_level_400(client):
    r = client.put("/api/tutor/settings", json={"level": "expert+"})
    assert r.status_code == 400


def test_settings_survive_restart(client, tmp_path, monkeypatch):
    client.put("/api/tutor/settings",
               json={"options": {"num_thread": 3}, "think": True})
    app2 = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app2) as c2:
        data = c2.get("/api/tutor/settings").json()
        assert data["options"]["num_thread"] == 3
        assert data["tutor"]["think"] is True
