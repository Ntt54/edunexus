"""Déplacement livre→domaine en un appel (orchestration link/unlink).

`PUT /api/tutor/books/{book_id}/subject` {"subject_id"} retire TOUTES les
jointures existantes puis lie la cible, atomiquement : 200
`{"moved": true, "subject_id": ...}`, 404 si livre/domaine inconnu.
Livre/chunks intacts (seules les jointures bougent). 100 % offline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


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


def _import(client: TestClient, tmp_path: Path, name: str, subject: str | None = None) -> str:
    p = tmp_path / name
    p.write_text(f"Contenu specifique {name} sur la photosynthese. " * 60, encoding="utf-8")
    payload: dict = {"path": str(p), "queue": True}
    if subject is not None:
        payload["subject"] = subject
    r = client.post("/api/tutor/import", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["book_id"]


def _wait_job_completed(client: TestClient, book_id: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/api/ingestion/jobs").json()["jobs"]
        if any(
            j.get("book_id") == book_id and j["status"] == "completed" for j in jobs
        ):
            return
        time.sleep(0.05)
    raise AssertionError(f"aucun job completed pour {book_id}")


def _joins(tmp_path: Path, book_id: str) -> list[str]:
    store = LibraryStore(tmp_path / "config")
    rows = store._conn.execute(
        "SELECT subject_id FROM subject_books WHERE book_id = ? ORDER BY subject_id",
        (book_id,),
    ).fetchall()
    return [r["subject_id"] for r in rows]


def _chunks_count(tmp_path: Path, book_id: str) -> int:
    store = LibraryStore(tmp_path / "config")
    return store._conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE book_id = ?", (book_id,)
    ).fetchone()["c"]


def test_move_unclassified_to_python(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "brut.txt", subject=None)
    _wait_job_completed(client, book_id)
    target = client.post("/api/tutor/subjects", json={"name": "Python"}).json()["id"]

    r = client.put(f"/api/tutor/books/{book_id}/subject", json={"subject_id": target})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"moved": True, "subject_id": target}

    # Une seule jointure restante : la cible.
    assert _joins(tmp_path, book_id) == [target]
    assert any(b["id"] == book_id for b in client.get("/api/tutor/books?subject=Python").json()["books"])


def test_move_idempotent(tmp_path: Path, client: TestClient) -> None:
    book_id = _import(client, tmp_path, "phys.txt", subject="Physique")
    _wait_job_completed(client, book_id)
    target = next(
        s["id"]
        for s in client.get("/api/tutor/subjects").json()["subjects"]
        if s["name"] == "Physique"
    )
    r = client.put(f"/api/tutor/books/{book_id}/subject", json={"subject_id": target})
    assert r.status_code == 200
    assert r.json() == {"moved": False, "subject_id": target}
    assert _joins(tmp_path, book_id) == [target]


def test_move_replaces_multiple_joins(tmp_path: Path, client: TestClient) -> None:
    book_id = _import(client, tmp_path, "a.txt", subject="Physique")
    _wait_job_completed(client, book_id)
    subjects = {
        s["name"]: s["id"]
        for s in client.get("/api/tutor/subjects").json()["subjects"]
    }
    chimie = client.post("/api/tutor/subjects", json={"name": "Chimie"}).json()["id"]
    # Double rattachement manuel (cas tordu) puis move : une seule restante.
    assert client.post(
        f"/api/tutor/subjects/{chimie}/books", json={"book_id": book_id}
    ).json() == {"linked": True}
    assert len(_joins(tmp_path, book_id)) == 2
    r = client.put(f"/api/tutor/books/{book_id}/subject", json={"subject_id": chimie})
    assert r.json() == {"moved": True, "subject_id": chimie}
    assert _joins(tmp_path, book_id) == [chimie]


def test_move_unknown_404(tmp_path: Path, client: TestClient) -> None:
    book_id = _import(client, tmp_path, "b.txt", subject="Physique")
    _wait_job_completed(client, book_id)
    assert client.put("/api/tutor/books/nope/subject", json={"subject_id": "x"}).status_code == 404
    assert client.put(
        f"/api/tutor/books/{book_id}/subject", json={"subject_id": "nope"}
    ).status_code == 404
    # Rien n'a bougé.
    assert len(_joins(tmp_path, book_id)) == 1


def test_move_keeps_book_and_chunks_intact(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "c.txt", subject="Physique")
    _wait_job_completed(client, book_id)
    before_chunks = _chunks_count(tmp_path, book_id)
    assert before_chunks > 0
    target = client.post("/api/tutor/subjects", json={"name": "Maths"}).json()["id"]
    assert client.put(f"/api/tutor/books/{book_id}/subject", json={"subject_id": target}).status_code == 200
    store = LibraryStore(tmp_path / "config")
    assert store.get_book(book_id) is not None
    assert _chunks_count(tmp_path, book_id) == before_chunks
    assert store.get_book_subject_id(book_id) == target
