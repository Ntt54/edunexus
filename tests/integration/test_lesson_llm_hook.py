"""Hook LLM synchrone des leçons (cause prouvée du repli permanent).

`LessonDiscussionService._try_llm_text` appelle
`tutor_service.generate_lesson_text(kind, notion, excerpts)` : ce hook doit
exister sur `TutorService`, être SYNC (chemin appelant sync, jamais
d'asyncio), poster vers Ollama `/api/chat` avec `config.tutor_model`,
timeout borné, et LEVER en échec (le repli vit dans lesson_discussion).

100 % offline : transport HTTP mocké via `httpx.MockTransport`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import httpx
import numpy as np
import pytest

from src.ollama_tutor.client import OllamaAPIError
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _service(tmp_path: Path) -> TutorService:
    store = LibraryStore(tmp_path / "config")
    config = Config(config_dir=tmp_path / "config")
    return TutorService(store, None, config)


def _ok_transport(captured: dict, body: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"message": {"content": body}, "done": True},
        )

    return httpx.MockTransport(handler)


def test_hook_present_and_called_with_kind_notion_excerpts(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    assert callable(getattr(svc, "generate_lesson_text", None))
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, "Texte du cours généré.")
    text = svc.generate_lesson_text(
        "lesson_course", "Boucles", ["extrait un sur les boucles", "extrait deux"]
    )
    assert text == "Texte du cours généré."
    assert captured["url"].endswith("/api/chat")
    payload = captured["payload"]
    assert payload["model"] == svc.config.tutor_model
    assert payload.get("stream") is False
    joined = " ".join(m.get("content", "") for m in payload["messages"])
    assert "Boucles" in joined
    assert "extrait un sur les boucles" in joined


def test_hook_prompts_differ_per_kind(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    captured_course: dict = {}
    svc.lesson_http_transport = _ok_transport(captured_course, "cours")
    svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])
    captured_summary: dict = {}
    svc.lesson_http_transport = _ok_transport(captured_summary, "synthèse")
    svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    course_joined = " ".join(m.get("content", "") for m in captured_course["payload"]["messages"])
    summary_joined = " ".join(m.get("content", "") for m in captured_summary["payload"]["messages"])
    assert "800" in course_joined and "1200" in course_joined
    assert "150" in summary_joined and "250" in summary_joined


def test_hook_http_error_raises_no_fallback_here(tmp_path: Path) -> None:
    svc = _service(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    svc.lesson_http_transport = httpx.MockTransport(handler)
    with pytest.raises(Exception):
        svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])


def test_hook_connection_error_raises(tmp_path: Path) -> None:
    svc = _service(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion impossible")

    svc.lesson_http_transport = httpx.MockTransport(handler)
    with pytest.raises(Exception):
        svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])


def test_hook_empty_content_raises(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, "   ")
    with pytest.raises(Exception):
        svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])


def _seed_lesson(tmp_path: Path, *, step_title: str = "Boucles", activity_id: str = "notion-loops"):
    store = LibraryStore(tmp_path / "config")
    config = Config(config_dir=tmp_path / "config")
    svc = TutorService(store, None, config)
    subj = store.create_subject("Informatique")
    book_id = uuid.uuid4().hex[:8]
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            book_id,
            "Manuel Python",
            str(store.config_dir / "manuel.txt"),
            "txt",
            hashlib.sha256(b"manuel").hexdigest(),
            "indexed",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)",
        (subj.id, book_id),
    )
    store._conn.commit()
    texts = [
        "Les boucles for en Python permettent d'itérer sur une séquence "
        "d'éléments en exécutant un bloc d'instructions à chaque passage.",
        "La boucle while en Python répète un bloc tant qu'une condition reste "
        "vraie, avec un compteur mis à jour pour garantir la terminaison.",
        "Programmer en Python demande de choisir la boucle adaptée : for pour "
        "les séquences connues, while pour les conditions d'arrêt dynamiques.",
    ]
    vec = np.random.randn(4).astype(np.float32).tolist()
    store.add_chunks(subj.id, book_id, texts, [vec] * len(texts), model="test-model")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", activity_id, step_title, ordinal=0)
    return store, svc, step


def test_e2e_generate_course_with_working_hook_no_fallback(tmp_path: Path) -> None:
    store, svc, step = _seed_lesson(tmp_path)
    llm_body = " ".join(["Mot de cours LLM authentique sur les boucles."] * 130)
    assert len(llm_body.split()) >= 800
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, llm_body)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    course = lesson.generate_course(disc.id)
    assert course["fallback"] is False
    assert "Mot de cours LLM authentique" in course["content"]
    assert "hors-ligne" not in course["content"]
    assert captured["payload"]["model"] == svc.config.tutor_model


def test_hook_uses_ollama_client_base_url_when_available(tmp_path: Path) -> None:
    from src.ollama_tutor.client import OllamaClient

    store = LibraryStore(tmp_path / "config")
    config = Config(config_dir=tmp_path / "config")
    client = OllamaClient(base_url="http://ollama-test:11434")
    svc = TutorService(store, client, config)
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, "ok")
    svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])
    assert captured["url"].startswith("http://ollama-test:11434")


# ------------------------------------------------------------------
# ask_notion branché sur le hook LLM (kind lesson_answer)
# ------------------------------------------------------------------

LLM_ANSWER_BODY = " ".join(["Réponse LLM ciblée sur les boucles avec exemple concret."] * 15)


def test_ask_prompt_kind_answer_exists_with_word_range(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, LLM_ANSWER_BODY)
    text = svc.generate_lesson_text("lesson_answer", "Boucles", ["extrait"], question="Comment marche for ?")
    assert text == LLM_ANSWER_BODY
    joined = " ".join(m.get("content", "") for m in captured["payload"]["messages"])
    assert "100" in joined and "200" in joined
    assert "Comment marche for ?" in joined


def test_ask_with_working_hook_returns_llm_answer_persisted(tmp_path: Path) -> None:
    store, svc, step = _seed_lesson(tmp_path)
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, LLM_ANSWER_BODY)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    result = lesson.ask_notion(disc.id, "comment marche la boucle for ?", "alice")
    assert result["fallback"] is False
    assert result["answer"] == LLM_ANSWER_BODY
    assert result["sources"], "les sources RAG existantes sont rendues"
    assert captured["payload"]["model"] == svc.config.tutor_model
    # Question et réponse LLM persistées.
    payload = lesson.get_discussion(disc.id)
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant"]
    assert payload["messages"][0]["content"] == "comment marche la boucle for ?"
    assert payload["messages"][1]["content"] == LLM_ANSWER_BODY


def test_ask_with_failing_hook_keeps_echo(tmp_path: Path) -> None:
    store, svc, step = _seed_lesson(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    svc.lesson_http_transport = httpx.MockTransport(handler)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    result = lesson.ask_notion(disc.id, "comment marche la boucle for ?", "alice")
    assert result["fallback"] is True
    assert result["answer"].startswith("Réponse sur « notion-loops » : comment marche la boucle for ?")
    assert "Manuel Python" in result["answer"]


def test_ask_notion_sanitized_with_and_without_llm(tmp_path: Path) -> None:
    for with_llm in (True, False):
        case_dir = tmp_path / ("with_llm" if with_llm else "without_llm")
        store, svc, step = _seed_lesson(
            case_dir,
            step_title="Programmer en Python",
            activity_id="Programmer-en-python-d010dd1a",
        )
        if with_llm:
            captured: dict = {}
            svc.lesson_http_transport = _ok_transport(captured, LLM_ANSWER_BODY)
        else:

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(500, json={"error": "boom"})

            svc.lesson_http_transport = httpx.MockTransport(handler)
        lesson = LessonDiscussionService(store, tutor_service=svc)
        disc = lesson.get_or_create_discussion(step.id, "alice")
        assert "d010dd1a" not in disc.notion_id
        result = lesson.ask_notion(disc.id, "par où commencer ?", "alice")
        assert "d010dd1a" not in result["answer"], f"notion-fichier, llm={with_llm}"
        if with_llm:
            assert result["fallback"] is False
        else:
            assert result["fallback"] is True
            assert "Programmer-en-python" in result["answer"]


# ------------------------------------------------------------------
# Timeouts par kind + suppression de contenu généré
# ------------------------------------------------------------------

def test_lesson_timeout_per_kind(monkeypatch, tmp_path: Path) -> None:
    svc = _service(tmp_path)
    seen: dict = {}

    RealClient = httpx.Client

    def factory(*args, **kwargs):
        seen.setdefault("calls", []).append(kwargs.get("timeout"))
        kwargs["transport"] = _ok_transport({}, "ok texte")
        return RealClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    svc.generate_lesson_text("lesson_course", "Boucles", ["extrait"])
    svc.generate_lesson_text("lesson_summary", "Boucles", ["extrait"])
    svc.generate_lesson_text("lesson_answer", "Boucles", ["extrait"], question="Comment ?")
    reads = [float(t.read) for t in seen["calls"]]
    assert reads == [360.0, 180.0, 180.0], f"timeouts par kind : {reads}"


# ------------------------------------------------------------------
# Modèle IA persisté par contenu généré (model TEXT, NULL = repli/lignes
# existantes). Payloads : `model: string|null` + `fallback: bool`.
# ------------------------------------------------------------------


def test_model_migration_idempotent_and_legacy_rows_null(tmp_path: Path) -> None:
    from src.ollama_tutor.tutor.store import LibraryStore as LS

    config_dir = tmp_path / "config"
    store = LS(config_dir)
    subj = store.create_subject("Informatique")
    store._conn.execute(
        "INSERT OR IGNORE INTO learner_profiles (id, name, avatar, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("alice", "alice", "", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.commit()
    # Ligne pré-migration (sans model) : lisible, model None.
    disc_id = "disc-legacy"
    store._conn.execute(
        "INSERT INTO lesson_discussions (id, path_step_id, notion_id, subject_id, learner_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (disc_id, "step-x", "Boucles", subj.id, "alice", "active", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO generated_lesson_contents (id, discussion_id, kind, content, sources, confidence, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c-legacy", disc_id, "lesson_course", "ancien cours", "[]", 0.8, "2026-01-01T00:00:00+00:00"),
    )
    store._conn.commit()
    # Migration rejouée 2× : sûre, colonne présente, ligne intacte.
    store._migrate_lesson_model_column()
    store._migrate_lesson_model_column()
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(generated_lesson_contents)")}
    assert "model" in cols
    rows = store.list_generated_contents(disc_id)
    assert len(rows) == 1
    assert rows[0].model is None
    assert rows[0].to_dict()["model"] is None


def test_add_generated_content_with_model(tmp_path: Path) -> None:
    store, _svc, _step = _seed_lesson(tmp_path)
    subj = store.create_subject("Physique")
    store._conn.execute(
        "INSERT OR IGNORE INTO learner_profiles (id, name, avatar, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("alice", "alice", "", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.commit()
    disc_id = "disc-model"
    store._conn.execute(
        "INSERT INTO lesson_discussions (id, path_step_id, notion_id, subject_id, learner_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (disc_id, "step-x", "Boucles", subj.id, "alice", "active", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.commit()
    obj = store.add_generated_content(
        disc_id, "lesson_course", "cours", model="llama3.2:3b"
    )
    assert obj.model == "llama3.2:3b"
    assert obj.to_dict()["model"] == "llama3.2:3b"
    assert store.list_generated_contents(disc_id)[0].model == "llama3.2:3b"
    # Défaut : repli/hors-ligne ⇒ NULL.
    obj2 = store.add_generated_content(disc_id, "lesson_summary", "synthèse")
    assert obj2.model is None


def test_e2e_course_persists_effective_model(tmp_path: Path) -> None:
    store, svc, step = _seed_lesson(tmp_path)
    llm_body = " ".join(["Mot de cours LLM authentique sur les boucles."] * 130)
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, llm_body)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    course = lesson.generate_course(disc.id)
    assert course["fallback"] is False
    assert course["model"] == svc.config.tutor_model
    assert store.list_generated_contents(disc.id)[0].model == svc.config.tutor_model


def test_e2e_course_fallback_model_null(tmp_path: Path) -> None:
    store, svc, step = _seed_lesson(tmp_path)
    lesson = LessonDiscussionService(store, tutor_service=None)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    course = lesson.generate_course(disc.id)
    assert course["fallback"] is True
    assert course["model"] is None
    assert store.list_generated_contents(disc.id)[0].model is None


def test_e2e_summary_persists_effective_model(tmp_path: Path) -> None:
    store, svc, step = _seed_lesson(tmp_path)
    llm_body = " ".join(["Phrase de synthèse LLM sur les boucles."] * 30)
    assert 150 <= len(llm_body.split()) <= 250
    captured: dict = {}
    svc.lesson_http_transport = _ok_transport(captured, llm_body)
    lesson = LessonDiscussionService(store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    summary = lesson.generate_summary(disc.id)
    assert summary["fallback"] is False
    assert summary["model"] == svc.config.tutor_model


def test_route_course_exposes_model_and_fallback(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from src.ollama_tutor.web.server import TutorService as WebTutorService
    from src.ollama_tutor.web.server import create_app

    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subj = store.create_subject("Informatique")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    llm_body = " ".join(["Mot de cours LLM authentique sur les boucles."] * 130)

    def fake_generate(self, kind, notion, excerpts, question=None):
        return llm_body

    monkeypatch.setattr(WebTutorService, "generate_lesson_text", fake_generate)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = client.post(
            f"/api/tutor/path-steps/{step.id}/discussion",
            headers={"X-Learner-Id": "alice"},
        ).json()["discussion"]["id"]
        body = client.post(
            f"/api/tutor/lesson-discussions/{disc_id}/generate-course",
            headers={"X-Learner-Id": "alice"},
        ).json()["content"]
        assert body["fallback"] is False
        assert isinstance(body["model"], str) and body["model"]

    def fake_fail(self, kind, notion, excerpts, question=None):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(WebTutorService, "generate_lesson_text", fake_fail)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = client.post(
            f"/api/tutor/path-steps/{step.id}/discussion",
            headers={"X-Learner-Id": "alice"},
        ).json()["discussion"]["id"]
        body = client.post(
            f"/api/tutor/lesson-discussions/{disc_id}/generate-course",
            headers={"X-Learner-Id": "alice"},
        ).json()["content"]
        assert body["fallback"] is True
        assert body["model"] is None


def test_delete_generated_content_store(tmp_path: Path) -> None:
    store, _svc, step = _seed_lesson(tmp_path)
    lesson = LessonDiscussionService(store, tutor_service=None)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    cid = lesson.generate_course(disc.id)["id"]
    assert store.delete_generated_content(cid) is True
    assert store.list_generated_contents(disc.id) == []
    assert store.delete_generated_content(cid) is False
    assert store.delete_generated_content("contenu-inconnu") is False


def test_delete_content_route_200_and_404(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from src.ollama_tutor.web.server import create_app

    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subj = store.create_subject("Informatique")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    with TestClient(create_app(config_dir=config_dir)) as client:
        disc_id = client.post(
            f"/api/tutor/path-steps/{step.id}/discussion",
            headers={"X-Learner-Id": "alice"},
        ).json()["discussion"]["id"]
        cid = LessonDiscussionService(LibraryStore(config_dir)).generate_course(disc_id)["id"]
        r = client.delete(
            f"/api/tutor/lesson-discussions/{disc_id}/contents/{cid}",
            headers={"X-Learner-Id": "alice"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": True}
        assert LibraryStore(config_dir).list_generated_contents(disc_id) == []
        r2 = client.delete(
            f"/api/tutor/lesson-discussions/{disc_id}/contents/{cid}",
            headers={"X-Learner-Id": "alice"},
        )
        assert r2.status_code == 404
        r3 = client.delete(
            f"/api/tutor/lesson-discussions/{disc_id}/contents/inconnu",
            headers={"X-Learner-Id": "alice"},
        )
        assert r3.status_code == 404
