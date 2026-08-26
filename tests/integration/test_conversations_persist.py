"""Integration : les conversations survivent à un redémarrage (FR-006/SC-003).

Deux conversations créées + messages persistés via la couche store, puis
« redémarrage » (nouvelle instance create_app sur le même config_dir) :
titres, ordre et historiques doivent être intégralement restaurés.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


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
def make_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    created: list[TestClient] = []

    def _make() -> TestClient:
        app = web_server.create_app(config_dir=tmp_path / "config")
        c = TestClient(app)
        created.append(c)
        return c

    yield _make
    for c in created:
        try:
            c.close()
        except Exception:
            pass


import json  # noqa: E402  (après définition du transport)


def test_conversations_survive_restart(make_client, tmp_path: Path):
    config_dir = tmp_path / "config"

    # --- « session 1 » : création de deux conversations + messages ---
    c1 = make_client()
    sid = None
    r = c1.post("/api/tutor/import",
                json={"subject": "Réseaux",
                      "path": str(_write(tmp_path, "a.txt"))})
    assert r.status_code == 200
    for s in c1.get("/api/tutor/subjects").json()["subjects"]:
        if s["name"] == "Réseaux":
            sid = s["id"]
    assert sid

    conv_a = c1.post("/api/tutor/conversations",
                     json={"title": "Apprendre Java",
                           "subject_id": sid}).json()["conversation"]
    conv_b = c1.post("/api/tutor/conversations",
                     json={"title": "TCP/IP", "subject_id": sid}).json()["conversation"]

    store = LibraryStore(config_dir)
    store.append_conversation_message(conv_a["id"], "user", "Question Java ?")
    store.append_conversation_message(conv_a["id"], "assistant", "Réponse Java.")
    store.append_conversation_message(conv_b["id"], "user", "Question TCP ?")

    # --- « redémarrage » : nouvelle instance sur le même config_dir ---
    c2 = make_client()

    lst = c2.get("/api/tutor/conversations").json()["conversations"]
    titles = [c["title"] for c in lst]
    assert titles == ["TCP/IP", "Apprendre Java"]  # plus récent d'abord

    hist_a = c2.get(f"/api/tutor/conversations/{conv_a['id']}").json()["messages"]
    hist_b = c2.get(f"/api/tutor/conversations/{conv_b['id']}").json()["messages"]
    assert [(m["role"], m["text"]) for m in hist_a] == [
        ("user", "Question Java ?"), ("assistant", "Réponse Java.")]
    assert [(m["role"], m["text"]) for m in hist_b] == [
        ("user", "Question TCP ?")]


def _write(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("contenu de test " * 30, encoding="utf-8")
    return p
