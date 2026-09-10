"""Endpoint SSE de génération de cours (contrat attendu par LessonView).

GET /api/tutor/lesson-discussions/{id}/course/stream?learner_id=…
⇒ ``text/event-stream``, ``data: {"delta":"…"}``*, puis
``data: {"done":true,"fallback":bool}`` ; en échec LLM
``data: {"error":"…"}`` (le front rebascule sur le POST classique).

100 % offline : ``TutorService.stream_lesson_text`` mocké (deltas /
erreur / stall), TestClient pour le transport SSE.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.tutor import lesson_discussion as ld_module
from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _seed(tmp_path: Path):
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subj = store.create_subject("Informatique")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    return config_dir, store, step


def _discuss(client: TestClient, step_id: str) -> str:
    r = client.post(
        f"/api/tutor/path-steps/{step_id}/discussion",
        headers={"X-Learner-Id": "alice"},
    )
    assert r.status_code == 200, r.text
    return r.json()["discussion"]["id"]


def _events(resp) -> list[dict]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


async def _ok_stream(self, kind: str, notion: str, excerpts: list) -> object:
    assert kind == "lesson_course"
    yield "Hello "
    yield "world"


async def _failing_stream(self, kind: str, notion: str, excerpts: list) -> object:
    raise RuntimeError("LLM down")
    yield "inatteignable"


async def _hanging_stream(self, kind: str, notion: str, excerpts: list) -> object:
    await asyncio.sleep(30)
    yield "trop tard"


async def _collect(gen):
    return [event async for event in gen]


def test_stream_deltas_done_and_persisted(tmp_path: Path, monkeypatch) -> None:
    config_dir, _store, step = _seed(tmp_path)
    monkeypatch.setattr(TutorService, "stream_lesson_text", _ok_stream)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = _discuss(client, step.id)
        r = client.get(
            f"/api/tutor/lesson-discussions/{disc_id}/course/stream",
            params={"learner_id": "alice"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _events(r)
        assert [e["delta"] for e in events if "delta" in e] == ["Hello ", "world"]
        done = [e for e in events if e.get("done")]
        assert len(done) == 1 and done[0]["fallback"] is False
        assert not [e for e in events if "error" in e]
        stored = LibraryStore(config_dir).list_generated_contents(disc_id)
        assert len(stored) == 1 and stored[0].kind == "lesson_course"
        assert stored[0].content == "Hello world"


def test_stream_error_event_without_persistence(tmp_path: Path, monkeypatch) -> None:
    config_dir, _store, step = _seed(tmp_path)
    monkeypatch.setattr(TutorService, "stream_lesson_text", _failing_stream)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = _discuss(client, step.id)
        r = client.get(
            f"/api/tutor/lesson-discussions/{disc_id}/course/stream",
            params={"learner_id": "alice"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _events(r)
        assert len(events) == 1 and "error" in events[0]
        assert "LLM down" in events[0]["error"]
        assert LibraryStore(config_dir).list_generated_contents(disc_id) == []


def test_stream_unknown_discussion_404_without_sse(tmp_path: Path) -> None:
    config_dir, _store, _step = _seed(tmp_path)
    with TestClient(create_app(config_dir=config_dir)) as client:
        r = client.get(
            "/api/tutor/lesson-discussions/inconnu/course/stream",
            params={"learner_id": "alice"},
        )
        assert r.status_code == 404
        assert "text/event-stream" not in r.headers.get("content-type", "")


def test_stream_learner_mismatch_403(tmp_path: Path) -> None:
    config_dir, _store, step = _seed(tmp_path)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = _discuss(client, step.id)
        r = client.get(
            f"/api/tutor/lesson-discussions/{disc_id}/course/stream",
            params={"learner_id": "bob"},
        )
        assert r.status_code == 403


def test_stream_course_without_llm_emits_error(tmp_path: Path) -> None:
    _config_dir, store, step = _seed(tmp_path)
    lesson = LessonDiscussionService(store, tutor_service=None)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    events = asyncio.run(_collect(lesson.stream_course(disc.id)))
    assert len(events) == 1 and "error" in events[0]
    assert store.list_generated_contents(disc.id) == []


def test_stream_stall_timeout_error_event(tmp_path: Path, monkeypatch) -> None:
    config_dir, _store, step = _seed(tmp_path)
    monkeypatch.setattr(TutorService, "stream_lesson_text", _hanging_stream)
    monkeypatch.setattr(ld_module, "LESSON_STREAM_TIMEOUT_S", 0.05)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = _discuss(client, step.id)
        r = client.get(
            f"/api/tutor/lesson-discussions/{disc_id}/course/stream",
            params={"learner_id": "alice"},
        )
        assert r.status_code == 200, r.text
        events = _events(r)
        assert len(events) == 1 and "error" in events[0]


def test_stream_timeout_default_at_least_420() -> None:
    assert float(ld_module.LESSON_STREAM_TIMEOUT_S) >= 420.0
