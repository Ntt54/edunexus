"""Thinking modèle exposé dans la réponse ask (ThinkBox front).

Le front affiche déjà « Réflexion pendant Xs » SI la réponse ask contient
`thinking` (chaîne non vide) — mais `ask_notion` ne le renvoyait pas.
Désormais : thinking capté quand le fournisseur le donne (Ollama champ
`thinking`, cloud `reasoning_content`/`reasoning`), `""` sinon ; clés
existantes inchangées ; pas de persistance (schéma messages sans colonne).

100 % offline (transports mockés, aucun réseau).
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


def _mock_transport(calls: dict, think_ollama=None, think_cloud=None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "gemma4:e2b-t3"}]})
        if url.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "kg/kilo-auto/free"}]})
        if url.endswith("/api/chat"):
            body = json.loads(request.content.decode("utf-8"))
            calls.setdefault("chat", []).append(body)
            message = {"content": "réponse ollama"}
            if think_ollama is not None:
                message["thinking"] = think_ollama
            return httpx.Response(200, json={"message": message})
        if url.endswith("/chat/completions"):
            body = json.loads(request.content.decode("utf-8"))
            calls.setdefault("completions", []).append(body)
            message = {"content": "réponse cloud"}
            if think_cloud is not None:
                message["reasoning_content"] = think_cloud
            return httpx.Response(
                200, json={"choices": [{"message": message}]}
            )
        return httpx.Response(404, json={"error": "route inconnue"})

    return httpx.MockTransport(handler)


def _service(tmp_path: Path, calls: dict, **kwargs) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    svc = TutorService(store, None, config)
    svc.lesson_http_transport = _mock_transport(calls, **kwargs)
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


def test_thinking_ollama_remonte(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls, think_ollama="réflexion interne ollama")
    svc.config.tutor_model = "gemma4:e2b-t3"
    text, thinking = svc.generate_lesson_text(
        "lesson_answer", "Boucles", ["extrait"], question="Q ?",
        return_thinking=True,
    )
    assert text == "réponse ollama"
    assert thinking == "réflexion interne ollama"


def test_reasoning_cloud_remonte(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls, think_cloud="raisonnement cloud")
    svc.config.llm_provider = "openai"
    svc.config.llm_base_url = "https://passerelle.example/v1"
    svc.config.llm_api_key = "sk-test"
    svc.config.tutor_model = "kg/kilo-auto/free"
    text, thinking = svc.generate_lesson_text(
        "lesson_answer", "Boucles", ["extrait"], question="Q ?",
        return_thinking=True,
    )
    assert text == "réponse cloud"
    assert thinking == "raisonnement cloud"


def test_sans_thinking_chaine_vide(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.tutor_model = "gemma4:e2b-t3"
    text, thinking = svc.generate_lesson_text(
        "lesson_answer", "Boucles", ["extrait"], question="Q ?",
        return_thinking=True,
    )
    assert text == "réponse ollama"
    assert thinking == ""


def test_e2e_ask_expose_thinking_cles_inchangees(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls, think_ollama="réflexion sur les boucles")
    svc.config.tutor_model = "gemma4:e2b-t3"
    lesson, disc = _seed_ask(svc)
    result = lesson.ask_notion(disc.id, "comment marche for ?", "alice")
    assert result["thinking"] == "réflexion sur les boucles"
    assert result["answer"] == "réponse ollama"
    assert result["fallback"] is False
    assert "sources" in result


def test_e2e_ask_sans_thinking_chaine_vide(tmp_path: Path) -> None:
    calls: dict = {}
    svc = _service(tmp_path, calls)
    svc.config.tutor_model = "gemma4:e2b-t3"
    lesson, disc = _seed_ask(svc)
    result = lesson.ask_notion(disc.id, "comment marche for ?", "alice")
    assert result["thinking"] == ""
    assert result["answer"] == "réponse ollama"


def test_e2e_ask_fallback_thinking_vide(tmp_path: Path) -> None:
    svc = _service(tmp_path, {})
    lesson = LessonDiscussionService(svc.store, tutor_service=None)
    _, disc = _seed_ask(svc)
    disc2 = lesson.get_or_create_discussion(disc.path_step_id, "bob")
    result = lesson.ask_notion(disc2.id, "question ?", "bob")
    assert result["fallback"] is True
    assert result["thinking"] == ""


def test_hook_custom_sans_thinking_param(tmp_path: Path) -> None:
    """Hook tiers sans `return_thinking` : ask marche, thinking ''."""
    calls: dict = {}
    svc = _service(tmp_path, calls)
    lesson, disc = _seed_ask(svc)

    class VieuxHook:
        def generate_lesson_text(self, kind, notion, excerpts, question=None):
            return "réponse vieux hook"

    lesson.tutor_service = VieuxHook()
    result = lesson.ask_notion(disc.id, "question ?", "alice")
    assert result["answer"] == "réponse vieux hook"
    assert result["thinking"] == ""
    assert result["fallback"] is False


def test_route_ask_relaie_thinking(tmp_path: Path) -> None:
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
        # Sans LLM : repli, thinking vide mais PRÉSENT (contrat front).
        body = client.post(
            f"/api/tutor/lesson-discussions/{disc_id}/ask",
            headers={"X-Learner-Id": "alice"},
            json={"question": "question ?"},
        ).json()
        assert body["fallback"] is True
        assert body["thinking"] == ""
        assert "answer" in body and "sources" in body
