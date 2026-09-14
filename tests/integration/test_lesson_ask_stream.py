"""Stream SSE des réponses ask (progressif, pas d'un bloc).

`ask_notion` one-shot + `thinking` préservés intacts. Nouveau :
(1) générateur tutor/ sync (thinking → tokens → fin, routé par modèle,
mêmes timeouts, repli transitoire) ; (2) endpoint SSE thin relayant
thinking/tokens/done (+ error explicite, jamais silencieux) ;
(3) persistance finale identique au one-shot.

100 % offline (streams mockés : NDJSON Ollama / SSE OpenAI).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


OLLAMA_MODELS = ["gemma4:e2b-t3"]
CLOUD_MODELS = ["kg/kilo-auto/free"]
CLOUD_BASE = "https://passerelle.example/v1"


def _mock_stream_transport(calls: dict, think_ollama=None, think_cloud=None, fail_chat=False):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": m} for m in OLLAMA_MODELS]})
        if url.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in CLOUD_MODELS]})
        if url.endswith("/api/chat"):
            body = json.loads(request.content.decode("utf-8"))
            calls.setdefault("chat", []).append(body)
            if fail_chat:
                return httpx.Response(500, json={"error": "boom"})
            if body.get("stream") is not True:
                # Appel non-stream (one-shot) : JSON simple comme Ollama.
                message = {"content": "réponse ollama"}
                if think_ollama is not None:
                    message["thinking"] = think_ollama
                return httpx.Response(200, json={"message": message})
            lines = []
            if think_ollama:
                lines.append(json.dumps({"message": {"role": "assistant", "content": "", "thinking": think_ollama}, "done": False}))
            for tok in ("Réponse", " progressive", " ici."):
                lines.append(json.dumps({"message": {"role": "assistant", "content": tok}, "done": False}))
            lines.append(json.dumps({"done": True}))
            return httpx.Response(
                200, content=("\n".join(lines) + "\n").encode(),
                headers={"content-type": "application/x-ndjson"},
            )
        if url.endswith("/chat/completions"):
            body = json.loads(request.content.decode("utf-8"))
            calls.setdefault("completions", []).append(body)
            assert body.get("stream") is True
            chunks = []
            if think_cloud:
                chunks.append(json.dumps({"choices": [{"delta": {"reasoning_content": think_cloud}, "index": 0}]}))
            for tok in ("Cloud", " répond", "."):
                chunks.append(json.dumps({"choices": [{"delta": {"content": tok}, "index": 0}]}))
            chunks.append("[DONE]")
            payload = "".join(f"data: {c}\n\n" for c in chunks)
            return httpx.Response(
                200, content=payload.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404, json={"error": "route inconnue"})

    return httpx.MockTransport(handler)


def _service(tmp_path: Path, calls: dict, **kwargs) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    svc = TutorService(store, None, config)
    svc.lesson_http_transport = _mock_stream_transport(calls, **kwargs)
    return svc


def _seed_ask(svc: TutorService):
    store = svc.store
    subj = store.create_subject("Informatique")
    book_id = uuid.uuid4().hex[:8]
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Manuel", "manuel.txt", "txt", hashlib.sha256(b"m").hexdigest(), "indexed", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subj.id, book_id)
    )
    store._conn.commit()
    vec = np.zeros(4, dtype=np.float32).tolist()
    store.add_chunks(
        subj.id, book_id,
        ["Les boucles for en Python itèrent sur des séquences."],
        [vec], model="test-model",
    )
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    return lesson, lesson.get_or_create_discussion(step.id, "alice")


def _events(lesson, disc_id: str, question: str):
    return list(lesson.iter_ask_tokens(disc_id, question, "alice"))


def test_order_thinking_tokens_done_ollama(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls, think_ollama="réflexion interne")
    svc.config.tutor_model = "gemma4:e2b-t3"
    lesson, disc = _seed_ask(svc)
    events = _events(lesson, disc.id, "comment marche for ?")
    kinds = [("thinking" in e, "delta" in e, "done" in e, "error" in e) for e in events]
    assert events[0] == {"thinking": "réflexion interne"}
    deltas = [e["delta"] for e in events if "delta" in e]
    assert "".join(deltas) == "Réponse progressive ici."
    assert events[-1]["done"] is True and events[-1]["fallback"] is False
    assert not any("error" in e for e in events)
    assert calls["chat"][0]["model"] == "gemma4:e2b-t3"
    assert "completions" not in calls
    # Persistance identique au one-shot.
    payload = lesson.get_discussion(disc.id)
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][1]["content"] == "Réponse progressive ici."


def test_cloud_model_hits_gateway(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls, think_cloud="raisonnement cloud")
    svc.config.llm_provider = "openai"
    svc.config.llm_base_url = CLOUD_BASE
    svc.config.llm_api_key = "sk-test"
    svc.config.tutor_model = "kg/kilo-auto/free"
    lesson, disc = _seed_ask(svc)
    events = _events(lesson, disc.id, "question ?")
    assert events[0] == {"thinking": "raisonnement cloud"}
    assert "".join(e["delta"] for e in events if "delta" in e) == "Cloud répond."
    assert "chat" not in calls


def test_provider_error_yields_explicit_error(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls, fail_chat=True)
    svc.config.tutor_model = "gemma4:e2b-t3"
    lesson, disc = _seed_ask(svc)
    events = _events(lesson, disc.id, "question ?")
    assert len(events) == 1 and "error" in events[0]
    assert events[0]["error"]
    # Rien persisté côté assistant (comme course/stream).
    payload = lesson.get_discussion(disc.id)
    assert [m["role"] for m in payload["messages"]] == ["user"]


def test_oneshot_ask_unchanged(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.tutor_model = "gemma4:e2b-t3"
    lesson, disc = _seed_ask(svc)
    result = lesson.ask_notion(disc.id, "question ?", "alice")
    assert set(result) == {"answer", "sources", "fallback", "thinking"}
    assert result["fallback"] is False


def test_route_ask_stream_relay(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subj = store.create_subject("Informatique")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    with TestClient(web_server.create_app(config_dir=config_dir)) as client:
        disc_id = client.post(
            f"/api/tutor/path-steps/{step.id}/discussion",
            headers={"X-Learner-Id": "alice"},
        ).json()["discussion"]["id"]
        # Pré-vol : 404 avant tout byte.
        r404 = client.get(
            f"/api/tutor/lesson-discussions/nope/ask/stream",
            params={"learner_id": "alice", "question": "q ?"},
        )
        assert r404.status_code == 404
        # Question vide : 400 avant tout byte.
        r400 = client.get(
            f"/api/tutor/lesson-discussions/{disc_id}/ask/stream",
            params={"learner_id": "alice", "question": "  "},
        )
        assert r400.status_code == 400
        # Sans LLM : event error explicite (jamais silencieux).
        r = client.get(
            f"/api/tutor/lesson-discussions/{disc_id}/ask/stream",
            params={"learner_id": "alice", "question": "question ?"},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        assert '"error"' in r.text
