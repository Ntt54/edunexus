"""Gestion fine des matières : création + détachement livre (contrats front).

100 % offline (TestClient, aucun démon). Couvre :
- POST /api/tutor/subjects {"name"} ⇒ 201 {"id","name"} ;
- nom vide ⇒ 400 ; doublon insensible à la casse ⇒ 409 ;
- DELETE /subjects/{sid}/books/{bid} ⇒ 200 {"removed": true|false},
  404 si inconnu — le livre survit (orphelin), seule la jointure part ;
- sujet supprimé ⇒ livres orphelins visibles via GET /api/tutor/books.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


def _make_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        if "api/embed" in str(request.url):
            body = json.loads(request.content) if request.content else {}
            inputs = body.get("input", [])
            vecs = [
                [float((i * 3 + j) % 5) / 5 for j in range(dim)]
                for i in range(len(inputs))
            ]
            return httpx.Response(200, json={"embeddings": vecs}, request=request)
        ndjson = (
            json.dumps(
                {"message": {"content": '{"domaine": "generique"}'}, "done": False}
            )
            + "\n"
            + json.dumps({"done": True})
            + "\n"
        )
        return httpx.Response(
            200,
            content=ndjson.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    dim = 4

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _import(client: TestClient, tmp_path: Path, subject: str, name: str) -> str:
    p = tmp_path / name
    p.write_text(f"contenu specifique {name} " * 50, encoding="utf-8")
    r = client.post(
        "/api/tutor/import",
        json={"subject": subject, "path": str(p), "queue": True},
    )
    assert r.status_code == 200, r.text
    return r.json()["book_id"]


# ---------------------------------------------------------------------------
# POST /api/tutor/subjects
# ---------------------------------------------------------------------------


def test_create_subject_201(client: TestClient) -> None:
    r = client.post("/api/tutor/subjects", json={"name": "Astronomie"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "name"}
    assert body["name"] == "Astronomie"
    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    assert any(s["id"] == body["id"] for s in subjects)


def test_create_subject_trims_name(client: TestClient) -> None:
    r = client.post("/api/tutor/subjects", json={"name": "  Botanique  "})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Botanique"


def test_create_subject_empty_400(client: TestClient) -> None:
    for payload in ({"name": ""}, {"name": "   "}):
        r = client.post("/api/tutor/subjects", json=payload)
        assert r.status_code == 400, (payload, r.text)


def test_create_subject_duplicate_409_case_insensitive(
    client: TestClient,
) -> None:
    assert client.post("/api/tutor/subjects", json={"name": "Chimie"}).status_code == 201
    r = client.post("/api/tutor/subjects", json={"name": "CHIMIE"})
    assert r.status_code == 409, r.text
    r = client.post("/api/tutor/subjects", json={"name": "Chimie"})
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# DELETE /api/tutor/subjects/{sid}/books/{bid}
# ---------------------------------------------------------------------------


def test_unlink_book_true_then_false(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "Physique", "phys.txt")
    sid = next(
        s["id"]
        for s in client.get("/api/tutor/subjects").json()["subjects"]
        if s["name"] == "Physique"
    )
    r = client.delete(f"/api/tutor/subjects/{sid}/books/{book_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"removed": True}

    r2 = client.delete(f"/api/tutor/subjects/{sid}/books/{book_id}")
    assert r2.status_code == 200
    assert r2.json() == {"removed": False}

    # Le livre survit (orphelin), seule la jointure est partie.
    books = client.get("/api/tutor/books").json()["books"]
    assert any(b["id"] == book_id for b in books)
    scoped = client.get("/api/tutor/books?subject=Physique").json()["books"]
    assert all(b["id"] != book_id for b in scoped)


def test_unlink_unknown_404(tmp_path: Path, client: TestClient) -> None:
    book_id = _import(client, tmp_path, "Physique", "phys.txt")
    sid = next(
        s["id"]
        for s in client.get("/api/tutor/subjects").json()["subjects"]
        if s["name"] == "Physique"
    )
    assert client.delete(f"/api/tutor/subjects/{sid}/books/nope").status_code == 404
    assert client.delete(f"/api/tutor/subjects/nope/books/{book_id}").status_code == 404


def test_deleted_subject_leaves_visible_orphans(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "Geologie", "geo.txt")
    sid = next(
        s["id"]
        for s in client.get("/api/tutor/subjects").json()["subjects"]
        if s["name"] == "Geologie"
    )
    assert client.delete(f"/api/tutor/subjects/{sid}").status_code == 200
    books = client.get("/api/tutor/books").json()["books"]
    assert any(b["id"] == book_id for b in books)


# ---------------------------------------------------------------------------
# PUT /api/tutor/subjects/{subject_id}
# ---------------------------------------------------------------------------


def _create_subject(client: TestClient, name: str) -> dict:
    r = client.post("/api/tutor/subjects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_rename_subject_200(client: TestClient) -> None:
    created = _create_subject(client, "Physique")
    r = client.put(
        f"/api/tutor/subjects/{created['id']}", json={"name": "Physique-Chimie"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"id", "name"}
    assert body == {"id": created["id"], "name": "Physique-Chimie"}
    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    assert any(
        s["id"] == created["id"] and s["name"] == "Physique-Chimie" for s in subjects
    )


def test_rename_subject_trims_name(client: TestClient) -> None:
    created = _create_subject(client, "Optique")
    r = client.put(
        f"/api/tutor/subjects/{created['id']}", json={"name": "  Optique Avancée  "}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Optique Avancée"


def test_rename_subject_empty_400(client: TestClient) -> None:
    created = _create_subject(client, "Acoustique")
    for payload in ({"name": ""}, {"name": "   "}):
        r = client.put(f"/api/tutor/subjects/{created['id']}", json=payload)
        assert r.status_code == 400, (payload, r.text)


def test_rename_subject_unknown_404(client: TestClient) -> None:
    r = client.put("/api/tutor/subjects/nope", json={"name": "X"})
    assert r.status_code == 404, r.text


def test_rename_subject_duplicate_409_case_insensitive(
    client: TestClient,
) -> None:
    first = _create_subject(client, "Biologie")
    second = _create_subject(client, "Geographie")
    for clash in ("Biologie", "BIOLOGIE"):
        r = client.put(f"/api/tutor/subjects/{second['id']}", json={"name": clash})
        assert r.status_code == 409, (clash, r.text)
    # Renommage vers son propre nom (même casse) : idempotent, pas 409.
    r = client.put(f"/api/tutor/subjects/{first['id']}", json={"name": "Biologie"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": first["id"], "name": "Biologie"}
