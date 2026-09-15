"""Routage sync du hook leçons par modèle (404 passerelle en prod).

`generate_lesson_text` (SYNC) appelait Ollama en direct avec le modèle
configuré ; un id passerelle (`kg/kilo-auto/free`) ⇒ 404 Ollama
« model not found » systématique. Désormais : même règle que le routeur
async (nom Ollama ⇒ Ollama avec strip `openai/`, sinon cloud configuré,
inconnu ⇒ erreur explicite), timeouts par kind conservés.

100 % offline (transport sync mocké routant par path, aucun réseau).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


OLLAMA_MODELS = ["gemma4:e2b-t3", "llama3.2"]
CLOUD_MODELS = ["kg/kilo-auto/free"]
CLOUD_BASE = "https://passerelle.example/v1"


def _mock_transport(calls: dict, course_body: str = "Cours généré.", fail_cloud: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/tags"):
            calls.setdefault("tags", []).append(url)
            return httpx.Response(200, json={"models": [{"name": m} for m in OLLAMA_MODELS]})
        if url.endswith("/models"):
            calls.setdefault("models", []).append(url)
            return httpx.Response(200, json={"data": [{"id": m} for m in CLOUD_MODELS]})
        if url.endswith("/api/chat"):
            body = json.loads(request.content.decode("utf-8"))
            calls.setdefault("chat", []).append(body)
            return httpx.Response(200, json={"message": {"content": "texte ollama"}})
        if url.endswith("/chat/completions"):
            body = json.loads(request.content.decode("utf-8"))
            calls.setdefault("completions", []).append(
                {"body": body, "auth": request.headers.get("authorization")}
            )
            if fail_cloud:
                return httpx.Response(404, json={"error": {"message": "model not found"}})
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "texte cloud"}}]}
            )
        return httpx.Response(404, json={"error": "route inconnue"})

    return httpx.MockTransport(handler)


def _service(tmp_path: Path, calls: dict, **kwargs) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    svc = TutorService(store, None, config)
    svc.lesson_http_transport = _mock_transport(calls, **kwargs)
    return svc


def test_ollama_model_hits_ollama(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.tutor_model = "gemma4:e2b-t3"
    text = svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    assert text == "texte ollama"
    assert calls["chat"][0]["model"] == "gemma4:e2b-t3"
    assert "completions" not in calls


def test_openai_prefix_stripped_for_ollama(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.tutor_model = "openai/llama3.2"
    text = svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    assert text == "texte ollama"
    assert calls["chat"][0]["model"] == "llama3.2"


def test_gateway_model_hits_cloud_with_key(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.llm_provider = "openai"
    svc.config.llm_base_url = CLOUD_BASE
    svc.config.llm_api_key = "sk-test"
    svc.config.tutor_model = "kg/kilo-auto/free"
    text = svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    assert text == "texte cloud"
    sent = calls["completions"][0]
    assert sent["body"]["model"] == "kg/kilo-auto/free"
    assert sent["auth"] == "Bearer sk-test"
    assert "chat" not in calls


def test_unknown_model_explicit_error_no_chat_call(tmp_path: Path) -> None:
    from src.ollama_tutor.client import OllamaAPIError

    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.llm_provider = "openai"
    svc.config.llm_base_url = CLOUD_BASE
    svc.config.llm_api_key = "sk-test"
    svc.config.tutor_model = "modele-xyz-inconnu"
    with pytest.raises(OllamaAPIError) as excinfo:
        svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    assert "modele-xyz-inconnu" in str(excinfo.value)
    assert "chat" not in calls
    assert "completions" not in calls


def test_timeouts_per_kind_kept(tmp_path: Path, monkeypatch) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    seen: dict = {}
    RealClient = httpx.Client

    def factory(*args, **kwargs):
        seen.setdefault("calls", []).append(kwargs.get("timeout"))
        kwargs["transport"] = svc.lesson_http_transport
        return RealClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])
    svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    svc.generate_lesson_text("lesson_answer", "Boucles", ["extrait"], question="Comment ?")
    reads = [float(t.read) for t in seen["calls"]]
    assert reads == [360.0, 300.0, 300.0], f"timeouts par kind : {reads}"


def test_e2e_generate_course_mocked_no_fallback(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.tutor_model = "gemma4:e2b-t3"
    llm_body = " ".join(["Mot de cours LLM authentique sur les boucles."] * 130)
    assert len(llm_body.split()) >= 800

    # Le mock répond le cours quel que soit le path chat.
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": m} for m in OLLAMA_MODELS]})
        return httpx.Response(200, json={"message": {"content": llm_body}})

    svc.lesson_http_transport = httpx.MockTransport(handler)
    store = svc.store
    subj = store.create_subject("Informatique")
    import hashlib
    import uuid

    import numpy as np

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
        subj.id, book_id, ["Les boucles for en Python itèrent."],
        [vec], model="test-model",
    )
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    course = lesson.generate_course(disc.id)
    assert course["fallback"] is False
    assert "Mot de cours LLM authentique" in course["content"]
