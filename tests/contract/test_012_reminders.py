"""T007 Contract: GET /api/tutor/reminders (012 US1 — mémorisation active).

012-real-learning-packs, FR-001 : vue « À réviser ».
- 200: {"due": [{kind, id, title, overdue_days}], "due_count": N, "stale_plan": bool}
- stale_plan true quand un retard > 7 j.
- 400 subject_id vide / learner_id vide, 404 sujet inconnu.

100 % offline : TestClient + store tmp partagé (même config_dir), aucun
démon Ollama (transport d'embed scripté).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.models import Flashcard
from src.ollama_tutor.tutor.store import LibraryStore


def _make_embed_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        inputs = body.get("input", [])
        vecs = [[float((i * 3 + j) % 5) / 5 for j in range(dim)] for i in range(len(inputs))]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_embed_transport())

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _seed_subject_with_cards(tmp_path: Path, *, overdue_days: int = 0) -> str:
    """Crée une matière + 2 cartes (1 due aujourd'hui, 1 future), backdate
    la première de ``overdue_days`` jours. Retourne le subject_id."""
    store = LibraryStore(tmp_path / "config")
    subject = store.create_subject("Maths")
    concept = store.create_concept(subject.id, "Addition")
    due_card = Flashcard(
        id="card-due", subject_id=subject.id, concept_id=concept.id,
        level="beginner", question="2 + 2 = ?", answer="4", source_hash="h1",
    )
    future_card = Flashcard(
        id="card-future", subject_id=subject.id, concept_id=concept.id,
        level="beginner", question="3 + 3 = ?", answer="6", source_hash="h2",
    )
    store.add_flashcard(due_card)
    store.add_flashcard(future_card)
    past = (date.today() - timedelta(days=overdue_days)).isoformat()
    store._conn.execute(
        "UPDATE review_schedule SET next_due = ? WHERE flashcard_id = ?",
        (past, "card-due"),
    )
    store._conn.execute(
        "UPDATE review_schedule SET next_due = ? WHERE flashcard_id = ?",
        ((date.today() + timedelta(days=3)).isoformat(), "card-future"),
    )
    store._conn.commit()
    return subject.id


def test_reminders_returns_due_list(tmp_path: Path, client: TestClient):
    sid = _seed_subject_with_cards(tmp_path)
    r = client.get("/api/tutor/reminders", params={"subject_id": sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["due_count"] == 1
    assert body["stale_plan"] is False
    assert len(body["due"]) == 1
    item = body["due"][0]
    assert item["kind"] == "carte"
    assert item["id"] == "card-due"
    assert item["title"] == "2 + 2 = ?"
    assert item["overdue_days"] == 0


def test_reminders_overdue_and_stale_plan(tmp_path: Path, client: TestClient):
    sid = _seed_subject_with_cards(tmp_path, overdue_days=10)
    r = client.get("/api/tutor/reminders", params={"subject_id": sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["due_count"] == 1
    assert body["due"][0]["overdue_days"] >= 10
    assert body["stale_plan"] is True


def test_reminders_empty_subject_id_400(tmp_path: Path, client: TestClient):
    r = client.get("/api/tutor/reminders", params={"subject_id": "   "})
    assert r.status_code == 400


def test_reminders_unknown_subject_404(tmp_path: Path, client: TestClient):
    r = client.get("/api/tutor/reminders", params={"subject_id": "nope-unknown"})
    assert r.status_code == 404


def test_reminders_empty_learner_id_400(tmp_path: Path, client: TestClient):
    # Même sémantique que le dashboard : query vide explicite + header
    # présent ⇒ 400 (via _log_error).
    sid = _seed_subject_with_cards(tmp_path)
    r = client.get(
        "/api/tutor/reminders",
        params={"subject_id": sid, "learner_id": ""},
        headers={"x-learner-id": "l1"},
    )
    assert r.status_code == 400
