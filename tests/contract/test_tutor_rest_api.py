"""Contract tests for the /api/tutor/* REST surface (T011, US1).

Uses FastAPI TestClient with a scripted embed transport (offline). Asserts
the endpoints exist and return the shapes from contracts/tutor-rest-api.md:
import, list books, delete book, search, index-status.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


def _make_embed_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inputs = body.get("input", [])
        n = len(inputs)
        vecs = [[float((i * 3 + j) % 5) / 5 for j in range(dim)] for i in range(n)]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    dim = 4

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_embed_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _wait_indexed(c: TestClient, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        books = c.get("/api/tutor/index-status").json()["books"]
        if books and books[0]["status"] == "ready":
            return
        time.sleep(0.02)
    raise AssertionError("indexing did not complete in time")


def _post_book(c: TestClient, tmp_path: Path, subject: str, name: str) -> str:
    p = tmp_path / name
    p.write_text("The quick brown fox jumps over the lazy dog. " * 50, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": subject, "path": str(p)})
    assert r.status_code == 200
    return r.json()["book_id"]


def test_import_returns_book_shape(tmp_path: Path, client: TestClient) -> None:
    book_id = _post_book(client, tmp_path, "Math", "math.txt")
    _wait_indexed(client)
    r = client.get("/api/tutor/books")
    assert r.status_code == 200
    books = r.json()["books"]
    assert len(books) == 1
    b = books[0]
    for key in ("id", "title", "format", "status", "error", "chunks_done", "chunks_total", "fingerprint"):
        assert key in b, f"missing book field {key}"
    assert b["format"] == "txt"
    assert b["status"] == "indexed"
    assert b["chunks_done"] > 0


def test_search_returns_results(tmp_path: Path, client: TestClient) -> None:
    _post_book(client, tmp_path, "Physics", "phys.txt")
    _wait_indexed(client)
    r = client.post(
        "/api/tutor/search",
        json={"subject": "Physics", "query": "fox", "k": 3},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert isinstance(results, list)
    assert len(results) > 0
    for res in results:
        for key in ("id", "book_id", "text", "score"):
            assert key in res, f"missing result field {key}"


def test_delete_book_cascades(tmp_path: Path, client: TestClient) -> None:
    book_id = _post_book(client, tmp_path, "History", "hist.txt")
    _wait_indexed(client)
    r = client.delete(f"/api/tutor/book/{book_id}")
    assert r.status_code == 204
    r = client.get("/api/tutor/books")
    assert r.json()["books"] == []


def test_index_status_shape(tmp_path: Path, client: TestClient) -> None:
    r = client.get("/api/tutor/index-status")
    assert r.status_code == 200
    body = r.json()
    assert "books" in body and "indexing" in body
    assert body["indexing"] is False
