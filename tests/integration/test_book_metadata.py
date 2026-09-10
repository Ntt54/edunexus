"""Métadonnées d'indexation par livre sur GET /api/tutor/books.

`last_indexed_at: string|null` (fin du dernier job completed du livre)
et `embed_model: string|null` (modèle unique distinct des embeddings de
ses chunks) — calculés à la volée, SANS migration. 100 % offline.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


BOOK_KEYS = {
    "id", "title", "format", "status", "error", "chunks_done",
    "chunks_total", "fingerprint",
}


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
    p.write_text(f"Contenu specifique {name} sur la photosynthese. " * 60, encoding="utf-8")
    # queue=true : seul chemin d'import qui trace une ligne ingestion_jobs
    # (le chemin direct reste aveugle — comportement existant conservé).
    r = client.post(
        "/api/tutor/import", json={"subject": subject, "path": str(p), "queue": True}
    )
    assert r.status_code == 200, r.text
    return r.json()["book_id"]


def _wait_job_completed(client: TestClient, book_id: str, timeout: float = 30.0) -> None:
    """Attend le job completed du livre (le statut livre passe à ready un
    write AVANT la ligne completed — poller le job, pas le livre)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/api/ingestion/jobs").json()["jobs"]
        if any(
            j.get("book_id") == book_id and j["status"] == "completed" for j in jobs
        ):
            return
        time.sleep(0.05)
    raise AssertionError(f"aucun job completed pour {book_id}")


def _books_by_id(client: TestClient) -> dict:
    books = client.get("/api/tutor/books").json()["books"]
    return {b["id"]: b for b in books}


def test_indexed_book_exposes_date_and_model(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_job_completed(client, book_id)
    book = _books_by_id(client)[book_id]
    assert book["last_indexed_at"] is not None
    datetime.fromisoformat(book["last_indexed_at"])  # format ISO exigé
    assert isinstance(book["embed_model"], str) and book["embed_model"]


def test_never_indexed_book_nulls(tmp_path: Path, client: TestClient) -> None:
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("SVT")
    p = tmp_path / "brut.txt"
    p.write_text("contenu brut jamais indexe " * 10, encoding="utf-8")
    book = store.import_document(subj.id, str(p))
    book_row = _books_by_id(client)[book.id]
    assert book_row["last_indexed_at"] is None
    assert book_row["embed_model"] is None


def test_payload_shape_unchanged_apart_from_additions(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_job_completed(client, book_id)
    book = _books_by_id(client)[book_id]
    assert BOOK_KEYS <= set(book), "clés historiques conservées"
    assert "last_indexed_at" in book and "embed_model" in book


def test_mixed_models_yield_null_embed_model(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_job_completed(client, book_id)
    store = LibraryStore(tmp_path / "config")
    store._conn.execute(
        "UPDATE chunks SET embedding_model = 'autre-modele' WHERE book_id = ?"
        " AND ordinal = (SELECT MIN(ordinal) FROM chunks WHERE book_id = ?)",
        (book_id, book_id),
    )
    store._conn.commit()
    book = _books_by_id(client)[book_id]
    assert book["embed_model"] is None, "mélange ⇒ pas de modèle unique"
    assert book["last_indexed_at"] is not None


def test_scoped_listing_also_enriched(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_job_completed(client, book_id)
    books = client.get("/api/tutor/books?subject=SVT").json()["books"]
    row = next(b for b in books if b["id"] == book_id)
    assert row["last_indexed_at"] is not None
    assert isinstance(row["embed_model"], str) and row["embed_model"]
