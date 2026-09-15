"""Embeddings désactivés : contrat REST des sentinelles ("", disabled, none, off).

100 % offline (TestClient + httpx.MockTransport, aucun démon Ollama). Couvre :
- PUT /api/tutor/models {"embedding": "disabled"|""|"none"} → 200, current
  canonique persisté ;
- POST /api/tutor/search en mode RAG off → repli scoring par mots-clés pur,
  AUCUN appel ``client.embed`` (transport mocké qui LÈVE sur /api/embed) ;
- POST /api/tutor/search en mode RAG on → chemin vectoriel existant.

Aides ``_post_book`` / ``_wait_indexed`` dupliquées localement depuis
``tests/contract/test_tutor_rest_api.py`` (isolation du module).
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


def _make_embed_fails_transport():
    """Transport dont ``/api/embed`` LÈVE — prouve l'absence d'appel embed."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/api/embed"):
            raise AssertionError(
                "client.embed appelé alors que les embeddings sont désactivés"
            )
        # Appels LLM divers (auto-classify best-effort…) : NDJSON classique.
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
            super().__init__(transport=_make_embed_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_embed_fails(tmp_path: Path, monkeypatch):
    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_embed_fails_transport())

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


def _put_embedding(c: TestClient, value: str) -> dict:
    r = c.put("/api/tutor/models", json={"embedding": value})
    assert r.status_code == 200, r.text
    return r.json()


_RESULT_KEYS = ("id", "book_id", "text", "chapter", "section", "page", "score")


# ---------------------------------------------------------------------------
# PUT /api/tutor/models — sentinelles embeddings désactivés
# ---------------------------------------------------------------------------

def test_put_models_embedding_disabled(client: TestClient) -> None:
    body = _put_embedding(client, "disabled")
    assert body["current"]["embedding"] == "disabled"


def test_put_models_embedding_empty(client: TestClient) -> None:
    body = _put_embedding(client, "")
    assert body["current"]["embedding"] == ""


def test_put_models_embedding_none_canonicalizes(client: TestClient) -> None:
    body = _put_embedding(client, "none")
    assert body["current"]["embedding"] == "disabled"


# ---------------------------------------------------------------------------
# POST /api/tutor/search — repli mots-clés quand RAG off
# ---------------------------------------------------------------------------

def test_search_rag_off_keyword_fallback_no_embed_call(
    client_embed_fails: TestClient, tmp_path: Path
) -> None:
    _put_embedding(client_embed_fails, "disabled")
    _post_book(client_embed_fails, tmp_path, "Physics", "phys.txt")
    _wait_indexed(client_embed_fails)
    r = client_embed_fails.post(
        "/api/tutor/search",
        json={"subject": "Physics", "query": "fox", "k": 3},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert isinstance(results, list)
    assert len(results) > 0, "le repli mots-clés doit retrouver les chunks"
    for res in results:
        for key in _RESULT_KEYS:
            assert key in res, f"result field manquant: {key}"
    scores = [float(res["score"]) for res in results]
    assert all(s > 0.0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_search_rag_off_empty_when_no_match(
    client_embed_fails: TestClient, tmp_path: Path
) -> None:
    _put_embedding(client_embed_fails, "disabled")
    _post_book(client_embed_fails, tmp_path, "Biology", "bio.txt")
    _wait_indexed(client_embed_fails)
    r = client_embed_fails.post(
        "/api/tutor/search",
        json={"subject": "Biology", "query": "zygomatique", "k": 3},
    )
    assert r.status_code == 200
    assert r.json()["results"] == []


# ---------------------------------------------------------------------------
# POST /api/tutor/search — chemin vectoriel quand RAG on
# ---------------------------------------------------------------------------

def test_search_rag_on_vector_path(client: TestClient, tmp_path: Path) -> None:
    _post_book(client, tmp_path, "Physics", "phys.txt")
    _wait_indexed(client)
    r = client.post(
        "/api/tutor/search",
        json={"subject": "Physics", "query": "fox", "k": 3},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert isinstance(results, list)
    assert len(results) > 0, "le chemin vectoriel doit retrouver les chunks"
    for res in results:
        for key in _RESULT_KEYS:
            assert key in res, f"result field manquant: {key}"