"""Contract tests : déplacement de document (US2/FR-012).

Déplacer un livre vers une autre catégorie NE doit NI ré-indexer NI perdre
les chunks : statut ``ready`` conservé, chunks intacts.
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
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def test_move_book_keeps_index_and_status(client, tmp_path: Path):
    p = tmp_path / "livre.txt"
    p.write_text("contenu de test " * 30, encoding="utf-8")
    r = client.post("/api/tutor/import",
                    json={"subject": "Réseaux", "path": str(p)})
    assert r.status_code == 200
    book_id = r.json()["book_id"]

    # Attente de l'indexation (statut ready)
    import time
    status = None
    for _ in range(40):
        books = client.get("/api/tutor/index-status").json().get("books", [])
        entry = next((b for b in books if b["id"] == book_id), None)
        status = entry["status"] if entry else None
        if status == "ready":
            break
        time.sleep(0.2)
    assert status == "ready"

    # Deux catégories
    cat_src = client.post("/api/tutor/categories",
                          json={"name": "Source"}).json()["category"]["id"]
    cat_dst = client.post("/api/tutor/categories",
                          json={"name": "Destination"}).json()["category"]["id"]

    # Rangement initial puis déplacement
    client.put(f"/api/tutor/books/{book_id}/categories",
               json={"category_id": cat_src})
    r = client.put(f"/api/tutor/books/{book_id}/categories",
                   json={"category_id": cat_dst})
    assert r.json() == {"added": True}

    # Aucune ré-indexation : statut inchangé + chunks toujours présents
    books = client.get("/api/tutor/index-status").json().get("books", [])
    entry = next(b for b in books if b["id"] == book_id)
    assert entry["status"] == "ready"

    store = LibraryStore(tmp_path / "config")
    subject_id = next(s["id"] for s in client.get(
        "/api/tutor/subjects").json()["subjects"] if s["name"] == "Réseaux")
    chunks_before = len(store.get_indexed_chunks(subject_id))
    assert chunks_before > 0
    client.delete(f"/api/tutor/books/{book_id}/categories/{cat_src}")
    client.put(f"/api/tutor/books/{book_id}/categories",
               json={"category_id": cat_dst})
    assert len(store.get_indexed_chunks(subject_id)) == chunks_before


import json  # noqa: E402
