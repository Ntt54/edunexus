"""Embeddings désactivés : TutorService/Retriever + flux de leçon hors REST.

100 % offline (httpx.MockTransport injecté, aucun démon Ollama). Couvre :
- ``is_embedding_disabled`` True quand config.tutor_embedding_model="disabled" ;
- ``retriever.retrieve()`` → [] SANS appel ``client.embed``
  (transport mocké qui LÈVE sur /api/embed) ;
- ``_ingestion_skip_mode()`` True (RAG off ⇒ skip d'ingestion) ;
- ``set_embedding_model("")`` ne lève pas (sentinelle vide acceptée) ;
- RAG off sur les leçons (``LessonDiscussionService``) : MÊME avec des chunks
  vectorisés présents en base (indexés avant la bascule), ``_filtered_chunks``
  renvoie [] → les prompts LLM n'embarquent AUCUN extrait de livre, pas de
  « Sources » ni de citation possible (bug de cité TOC « Apprenez à
  programmer en Java »). Contrôle RAG on : les chunks sont bien retournés.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _embed_raises_transport() -> httpx.MockTransport:
    """Transport dont ``/api/embed`` LÈVE — prouve l'absence d'appel embed."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/api/embed"):
            raise AssertionError(
                "client.embed appelé alors que les embeddings sont désactivés"
            )
        return httpx.Response(200, json={"models": []}, request=request)

    return httpx.MockTransport(handler)


def _llm_transport(captured: dict, body: str) -> httpx.MockTransport:
    """Capture le payload ``/api/chat`` et répond le texte LLM ``body``."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"message": {"content": body}, "done": True},
            request=request,
        )

    return httpx.MockTransport(handler)


def _seed_vectorized_chunks(store: LibraryStore, subject_id: str) -> str:
    """Un livre + 1 chunk AVEC vecteur factice (comme indexé AVANT RAG off)."""
    book_id = uuid.uuid4().hex[:8]
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Apprenez a programmer en Java", "manuel.txt", "txt",
         hashlib.sha256(b"m").hexdigest(), "indexed", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)",
        (subject_id, book_id),
    )
    store._conn.commit()
    text = ("Les variables en Java se déclarent avec un type explicite comme "
            "int ou String ; le compilateur vérifie le typage statiquement.")
    store.add_chunks(subject_id, book_id, [text], [[0.1, 0.2]], model="test-model")
    return book_id


def _lesson_step(store: LibraryStore, subject_id: str):
    path = store.create_learning_path(subject_id, "Parcours")
    return store.add_path_step(
        path.id, "concept", "notion-java", "Variables Java", ordinal=0
    )


@pytest.fixture
def svc(tmp_path: Path) -> TutorService:
    config = Config(config_dir=tmp_path)
    config.tutor_embedding_model = "disabled"
    store = LibraryStore(tmp_path)
    client = OllamaClient(transport=_embed_raises_transport())
    return TutorService(store, client, config)


def test_service_is_embedding_disabled(svc: TutorService) -> None:
    assert svc.is_embedding_disabled is True


@pytest.mark.asyncio
async def test_retriever_retrieve_returns_empty_no_embed_call(
    svc: TutorService, tmp_path: Path
) -> None:
    subj = svc.store.create_subject("Informatique")
    results = await svc.retriever.retrieve(subj.id, "une question quelconque", k=3)
    assert results == []


def test_ingestion_skip_mode_true_when_disabled(svc: TutorService) -> None:
    assert svc._ingestion_skip_mode() is True


def test_set_embedding_model_empty_does_not_raise(svc: TutorService) -> None:
    svc.set_embedding_model("")
    assert svc.is_embedding_disabled is True


def test_set_embedding_model_none_canonicalizes(svc: TutorService) -> None:
    svc.set_embedding_model("none")
    assert svc.model == "disabled"
    assert svc.is_embedding_disabled is True


# ---------------------------------------------------------------------------
# Leçons en RAG off : chunks vectorisés présents mais JAMAIS transmis au LLM
# ---------------------------------------------------------------------------

def test_filtered_chunks_empty_when_rag_off_even_with_vectors(
    svc: TutorService,
) -> None:
    subj = svc.store.create_subject("Informatique")
    _seed_vectorized_chunks(svc.store, subj.id)
    lesson = LessonDiscussionService(svc.store, tutor_service=svc)
    chunks = lesson._filtered_chunks(subj.id, ["java"])
    assert chunks == []


def test_filtered_chunks_keeps_history_when_no_tutor_service(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Informatique")
    _seed_vectorized_chunks(store, subj.id)
    lesson = LessonDiscussionService(store, tutor_service=None)
    chunks = lesson._filtered_chunks(subj.id, ["java"])
    assert chunks, "sans tutor_service on n'a pas l'info RAG → comportement historique"


def test_filtered_chunks_returns_chunks_when_rag_on(tmp_path: Path) -> None:
    # Contrôle : RAG ON (modèle NON sentinelle ⇒ le toggle fait LA différence).
    config = Config(config_dir=tmp_path)
    config.tutor_embedding_model = "embeddinggemma"
    store = LibraryStore(tmp_path)
    svc_on = TutorService(
        store, OllamaClient(transport=_embed_raises_transport()), config
    )
    subj = store.create_subject("Informatique")
    _seed_vectorized_chunks(store, subj.id)
    lesson = LessonDiscussionService(store, tutor_service=svc_on)
    chunks = lesson._filtered_chunks(subj.id, ["java"])
    assert chunks, "RAG on : les chunks vectorisés doivent être retournés"
    assert "Java" in chunks[0]["text"]


def test_ask_notion_rag_off_no_sources_and_no_chunk_in_prompt(
    svc: TutorService,
) -> None:
    subj = svc.store.create_subject("Informatique")
    _seed_vectorized_chunks(svc.store, subj.id)
    step = _lesson_step(svc.store, subj.id)
    captured: dict = {}
    svc.lesson_http_transport = _llm_transport(
        captured, "Réponse générique du tuteur sans aucune citation de livre."
    )
    lesson = LessonDiscussionService(svc.store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    result = lesson.ask_notion(disc.id, "comment déclarer une variable ?", "alice")
    assert result["sources"] == []
    joined = " ".join(m.get("content", "") for m in captured["payload"]["messages"])
    # Nouveau contrat RAG off (_build_lesson_prompts sans extraits) : le
    # prompt n'évoque NI extraits NI sources (l'ancien « aucun extrait
    # indexé » faisait écrire au modèle « selon les extraits, aucune
    # définition n'est fournie… » au lieu d'enseigner) ; il ordonne
    # d'enseigner et ne contient aucun texte de chunk.
    low = joined.lower()
    assert "extrait" not in low
    assert "source" not in low
    assert "enseigne" in low
    assert "Les variables en Java" not in joined
    assert "Apprenez a programmer en Java" not in joined


def test_generate_course_rag_off_sources_empty_no_chunk_in_prompt(
    svc: TutorService,
) -> None:
    subj = svc.store.create_subject("Informatique")
    _seed_vectorized_chunks(svc.store, subj.id)
    step = _lesson_step(svc.store, subj.id)
    body = " ".join(["Mot de cours générique sans aucune citation de livre."] * 140)
    captured: dict = {}
    svc.lesson_http_transport = _llm_transport(captured, body)
    lesson = LessonDiscussionService(svc.store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    course = lesson.generate_course(disc.id)
    assert course["sources"] == []
    assert course["confidence"] == 0.0
    assert course["fallback"] is False
    joined = " ".join(m.get("content", "") for m in captured["payload"]["messages"])
    # Même contrat RAG off que ask_notion (voir ci-dessus) : ni extraits ni
    # sources évoqués, directive d'enseignement, aucun texte de chunk.
    low = joined.lower()
    assert "extrait" not in low
    assert "source" not in low
    assert "enseigne" in low
    assert "Les variables en Java" not in joined
    assert "Apprenez a programmer en Java" not in joined


def test_generate_exercises_rag_off_never_uses_chunk_text(
    svc: TutorService,
) -> None:
    subj = svc.store.create_subject("Informatique")
    _seed_vectorized_chunks(svc.store, subj.id)
    step = _lesson_step(svc.store, subj.id)
    lesson = LessonDiscussionService(svc.store, tutor_service=svc)
    disc = lesson.get_or_create_discussion(step.id, "alice")
    attempt = lesson.generate_exercises(disc.id, "alice")
    joined = " ".join(q.get("statement", "") for q in attempt["questions"])
    assert "Les variables en Java" not in joined
    assert "Apprenez a programmer en Java" not in joined