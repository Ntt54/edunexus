"""Contract tests for POST /api/tutor/categories/auto-classify (Phase 6 UX).

Offline: the Ollama client is replaced by a fake whose ``chat_stream``
yields one canned payload per call and counts calls. Covers:
- one LLM call per batch of ``batch_size`` titles (call count == ceil);
- duplicate categories are reused (no duplicate creation, single entry in
  ``categories_created``) and memberships are idempotent;
- fuzzy title matching (casefold + whitespace collapse);
- malformed batch ⇒ recorded in ``failed_batches``, other batches continue;
- empty library ⇒ 200 {"assignments": [], "message": "aucun livre"} with
  ZERO model calls;
- unreachable LLM ⇒ HTTP 502 with a clear French detail.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.client import OllamaConnectionError, StreamEvent
from src.ollama_tutor.tutor.store import LibraryStore


def _embed_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"embeddings": [[0.0] * 4]}, request=request
        )

    return httpx.MockTransport(handler)


class FakeClassifyClient(web_server.OllamaClient):
    """Scripted tutor client: one canned payload per chat call."""

    responses: list[str] = []
    calls: list[list[str]] = []
    explode: bool = False

    def __init__(self, *a, **k):
        super().__init__(transport=_embed_transport())

    async def chat_stream(
        self, messages, model, *, think=False, options=None, format=None, tools=None
    ):
        cls = type(self)
        cls.calls.append([m.content for m in messages])
        if cls.explode:
            raise OllamaConnectionError("Cannot connect to Ollama at fake")
        idx = min(len(cls.calls) - 1, len(cls.responses) - 1)
        yield StreamEvent(kind="content", text=cls.responses[idx])


def _reset(responses: list[str], explode: bool = False) -> None:
    FakeClassifyClient.responses = responses
    FakeClassifyClient.calls = []
    FakeClassifyClient.explode = explode


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", FakeClassifyClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _seed_books(cfg_dir: Path, titles: list[str]) -> dict[str, str]:
    """Insert book rows directly into the app's library DB.

    Files are named after their title so ``import_document`` (title =
    file stem) registers the exact title the canned LLM payloads echo.
    """
    store = LibraryStore(cfg_dir)
    subj = store.create_subject("Classe")
    out: dict[str, str] = {}
    for t in titles:
        p = cfg_dir / f"{t}.txt"
        p.write_text(f"Contenu du livre {t}.", encoding="utf-8")
        b = store.import_document(subj.id, p)
        out[t] = b.id
    return out


def _classify(c: TestClient, payload: dict | None = None):
    body = payload if payload is not None else {}
    return c.post(
        "/api/tutor/categories/auto-classify",
        json=body if payload is not None else None,
    )


# ---------------------------------------------------------------------------
# Batching: ONE LLM call per batch.
# ---------------------------------------------------------------------------


def test_one_llm_call_per_batch(tmp_path: Path, client: TestClient) -> None:
    cfg_dir = tmp_path / "config"
    ids = _seed_books(cfg_dir, ["Algèbre Moderne", "Géographie Humaine", "Histoire Romaine"])
    id1, id2, id3 = ids["Algèbre Moderne"], ids["Géographie Humaine"], ids["Histoire Romaine"]
    _reset([
        json.dumps([
            {"title": "Algèbre Moderne", "category": "Mathématiques"},
            {"title": "Géographie Humaine", "category": "Sciences"},
        ]),
        # Second batch arrives fenced — the parser must strip it.
        "```json\n"
        + json.dumps([{"title": "Histoire Romaine", "category": "Histoire"}])
        + "\n```",
    ])
    r = client.post("/api/tutor/categories/auto-classify", json={"batch_size": 2})
    assert r.status_code == 200
    body = r.json()

    assert set(body) == {
        "assignments",
        "categories_created",
        "failed_batches",
        "total_books",
    }
    assert len(FakeClassifyClient.calls) == 2  # ceil(3 / 2)
    assert body["total_books"] == 3
    assert body["failed_batches"] == []
    assert body["categories_created"] == ["Mathématiques", "Sciences", "Histoire"]
    assert body["assignments"] == [
        {"book_id": id1, "title": "Algèbre Moderne", "category": "Mathématiques"},
        {"book_id": id2, "title": "Géographie Humaine", "category": "Sciences"},
        {"book_id": id3, "title": "Histoire Romaine", "category": "Histoire"},
    ]
    for a in body["assignments"]:
        assert set(a) == {"book_id", "title", "category"}

    # Memberships persisted.
    cats1 = client.get(f"/api/tutor/books/{id1}/categories").json()["categories"]
    assert [c_["name"] for c_ in cats1] == ["Mathématiques"]


def test_duplicate_category_reused_and_membership_idempotent(
    tmp_path: Path, client: TestClient
) -> None:
    cfg_dir = tmp_path / "config"
    ids = _seed_books(cfg_dir, ["Livre Un", "Livre Deux"])
    _reset([
        json.dumps([{"title": "Livre Un", "category": "Sciences"}]),
        json.dumps([{"title": "Livre Deux", "category": "sciences"}]),  # case dup
    ])
    r = client.post("/api/tutor/categories/auto-classify", json={"batch_size": 1})
    assert r.status_code == 200
    body = r.json()
    # The lowercase repeat reused the existing category — created only once.
    assert body["categories_created"] == ["Sciences"]
    assert len(body["assignments"]) == 2

    cats = client.get("/api/tutor/categories").json()["categories"]
    assert [c_["name"] for c_ in cats] == ["Sciences"]

    # Second run: memberships already exist → still fine, nothing new created.
    _reset([
        json.dumps([
            {"title": "Livre Un", "category": "Sciences"},
            {"title": "Livre Deux", "category": "Sciences"},
        ]),
    ])
    r2 = client.post("/api/tutor/categories/auto-classify", json={})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["categories_created"] == []
    assert len(body2["assignments"]) == 2


def test_fuzzy_title_match_normalization(
    tmp_path: Path, client: TestClient
) -> None:
    cfg_dir = tmp_path / "config"
    ids = _seed_books(cfg_dir, ["Algèbre Moderne"])
    _reset([
        json.dumps([{"title": "  ALGÈBRE   moderne  ", "category": "Maths"}]),
    ])
    r = client.post("/api/tutor/categories/auto-classify", json={"batch_size": 25})
    assert r.status_code == 200
    body = r.json()
    assert body["assignments"] == [
        {"book_id": ids["Algèbre Moderne"], "title": "Algèbre Moderne", "category": "Maths"}
    ]


def test_malformed_batch_recorded_others_continue(
    tmp_path: Path, client: TestClient
) -> None:
    cfg_dir = tmp_path / "config"
    ids = _seed_books(cfg_dir, ["Livre A", "Livre B"])
    _reset([
        "Désolé, je ne peux pas répondre dans ce format.",  # batch 0: no JSON
        json.dumps([{"title": "Livre B", "category": "Lettres"}]),  # batch 1 OK
    ])
    r = client.post("/api/tutor/categories/auto-classify", json={"batch_size": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["failed_batches"] == [0]
    assert body["assignments"] == [
        {"book_id": ids["Livre B"], "title": "Livre B", "category": "Lettres"}
    ]
    assert len(FakeClassifyClient.calls) == 2  # batches keep running


def test_empty_library_message_and_no_llm_call(client: TestClient) -> None:
    _reset([])
    r = client.post("/api/tutor/categories/auto-classify", json={"batch_size": 10})
    assert r.status_code == 200
    assert r.json() == {"assignments": [], "message": "aucun livre"}
    assert FakeClassifyClient.calls == []  # zero model calls


def test_llm_unreachable_is_502_with_french_detail(
    tmp_path: Path, client: TestClient
) -> None:
    _seed_books(tmp_path / "config", ["Livre X"])
    _reset([], explode=True)
    r = client.post("/api/tutor/categories/auto-classify", json={"batch_size": 5})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "LLM" in detail or "moteur" in detail.lower()
