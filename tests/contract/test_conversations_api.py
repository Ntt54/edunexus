"""Contract tests : API conversations nommées (005-platform-ui-library).

Formes exactes de contracts/api.md §1. Hors-ligne via TestClient avec un
client Ollama scripté (même canevas que test_tutor_admin_api.py).
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
        vecs = [
            [float((i * 3 + j) % 5) / 5 for j in range(dim)]
            for i in range(len(inputs))
        ]
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


def _subject_id(c: TestClient, name: str = "Réseaux") -> str:
    r = c.get("/api/tutor/subjects")
    assert r.status_code == 200
    for s in r.json().get("subjects", []):
        if s.get("name") == name:
            return s["id"]
    raise AssertionError(f"espace {name!r} introuvable")


def _import_book(c: TestClient, tmp_path: Path, subject: str, name: str) -> str:
    p = tmp_path / name
    p.write_text("contenu de test " * 30, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": subject, "path": str(p)})
    assert r.status_code == 200
    return r.json()["book_id"]


def test_create_list_rename_delete_isolation(client, tmp_path):
    _import_book(client, tmp_path, "Réseaux", "livre_a.txt")
    sid = _subject_id(client)

    # Création
    r = client.post("/api/tutor/conversations",
                    json={"title": "Apprendre Java", "subject_id": sid})
    assert r.status_code == 200
    conv = r.json()["conversation"]
    assert conv["title"] == "Apprendre Java" and conv["id"]

    # Deuxième conversation (isolation)
    r2 = client.post("/api/tutor/conversations",
                     json={"title": "Autre", "subject_id": sid})
    conv2 = r2.json()["conversation"]

    # Liste : les deux présentes, la plus récente d'abord
    lst = client.get("/api/tutor/conversations").json()["conversations"]
    ids = [c["id"] for c in lst]
    assert ids == [conv2["id"], conv["id"]]

    # Renommage persistant
    r = client.patch(f"/api/tutor/conversations/{conv['id']}",
                     json={"title": "Java — semaine 1"})
    assert r.status_code == 200
    titles = {c["id"]: c["title"] for c in client.get(
        "/api/tutor/conversations").json()["conversations"]}
    assert titles[conv["id"]] == "Java — semaine 1"

    # Suppression : isolation garantie
    r = client.delete(f"/api/tutor/conversations/{conv['id']}")
    assert r.json() == {"deleted": True}
    ids = [c["id"] for c in client.get(
        "/api/tutor/conversations").json()["conversations"]]
    assert ids == [conv2["id"]]


def test_unknown_conversation_404(client):
    assert client.patch("/api/tutor/conversations/inconnu",
                        json={"title": "x"}).status_code == 404
    assert client.put("/api/tutor/conversations/inconnu/sources",
                      json={"book_ids": []}).status_code == 404


def test_delete_unknown_returns_false(client):
    r = client.delete("/api/tutor/conversations/inconnu")
    assert r.status_code == 200
    assert r.json() == {"deleted": False}


def test_sources_roundtrip(client, tmp_path):
    book_id = _import_book(client, tmp_path, "Réseaux", "livre_src.txt")
    sid = _subject_id(client)
    conv = client.post("/api/tutor/conversations",
                       json={"title": "C", "subject_id": sid}).json()["conversation"]

    r = client.put(f"/api/tutor/conversations/{conv['id']}/sources",
                   json={"book_ids": [book_id]})
    assert r.json() == {"active": 1}

    got = client.get(f"/api/tutor/conversations/{conv['id']}/sources").json()
    assert got["book_ids"] == [book_id]
    assert got["books"][0]["title"] == "livre_src"


def test_history_roundtrip(client, tmp_path):
    """FR-006/SC-003 : l'historique persiste et est servi par GET /{id}."""
    book_id = _import_book(client, tmp_path, "Réseaux", "hist.txt")
    sid = _subject_id(client)
    conv = client.post("/api/tutor/conversations",
                       json={"title": "Histo", "subject_id": sid}).json()["conversation"]

    # Initialement vide
    r = client.get(f"/api/tutor/conversations/{conv['id']}")
    assert r.status_code == 200
    assert r.json()["messages"] == []
    assert r.json()["conversation"]["title"] == "Histo"

    # Persistance via la couche store (même config_dir que create_app)
    from src.ollama_tutor.tutor.store import LibraryStore
    store = LibraryStore(tmp_path / "config")
    store.append_conversation_message(conv["id"], "user", "Question ?")
    store.append_conversation_message(conv["id"], "assistant", "Réponse.")

    r = client.get(f"/api/tutor/conversations/{conv['id']}")
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "Question ?"

    # 404 sur inconnue
    assert client.get(
        "/api/tutor/conversations/inconnu").status_code == 404
