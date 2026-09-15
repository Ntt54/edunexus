"""generate_path_from_books SANS livres quand les embeddings sont désactivés.

100 % offline : transport httpx.MockTransport injecté dans OllamaClient
(chat NDJSON scripté), aucun démon Ollama. Couvre :
- RAG off (config.tutor_embedding_model="disabled") + book_ids=[] ⇒ steps
  générés depuis les connaissances du modèle (matière + objectif),
  parcours créé, AUCUN appel ``client.embed`` (transport qui LÈVE sur
  /api/embed) ;
- RAG on (défaut) + book_ids=[] ⇒ PathGenerationError (comportement
  RAG on préservé : la TOC des livres est requise).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import PathGenerationError, TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _make_knowledge_transport(payload: str, captured: dict | None = None):
    """Transport scripté : chat NDJSON valide, /api/embed LÈVE, /api/tags vide."""

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path.endswith("/api/embed"):
            raise AssertionError(
                "client.embed appelé alors que les embeddings sont désactivés"
            )
        if path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": []}, request=request)
        if captured is not None:
            try:
                captured["request_body"] = json.loads(request.content)
            except (TypeError, ValueError):
                pass
        lines = (
            json.dumps({"message": {"content": payload}, "done": False})
            + "\n"
            + json.dumps({"done": True})
            + "\n"
        )
        return httpx.Response(
            200,
            content=lines.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


def _make_service(
    tmp_path: Path,
    *,
    embedding_disabled: bool,
    payload: str = "",
    captured: dict | None = None,
) -> SimpleNamespace:
    config = Config(config_dir=tmp_path)
    if embedding_disabled:
        config.tutor_embedding_model = "disabled"
    store = LibraryStore(tmp_path)
    client = OllamaClient(
        transport=_make_knowledge_transport(payload, captured)
    )
    service = TutorService(store, client, config)
    return SimpleNamespace(store=store, config=config, service=service)


def _valid_steps(n: int = 6) -> list[dict]:
    return [
        {
            "title": f"Leçon {i}",
            "type": "concept",
            "duration": 15,
            "objectives": [f"Objectif {i}"],
        }
        for i in range(1, n + 1)
    ]


@pytest.mark.asyncio
async def test_rag_off_empty_books_generates_from_knowledge(
    tmp_path: Path,
) -> None:
    """RAG off + book_ids=[] ⇒ steps LLM persistés, aucun appel embed."""
    captured: dict = {}
    svc = _make_service(
        tmp_path, embedding_disabled=True,
        payload=json.dumps(_valid_steps()), captured=captured,
    )
    sid = svc.store.create_subject("Informatique").id
    result = await svc.service.generate_path_from_books(sid, [])
    assert result["fallback"] is False
    assert result["filled"] is False
    assert result["id"]
    assert result["title"] == "Parcours — Informatique"
    assert len(result["steps"]) == 6
    assert all(s["title"].strip() for s in result["steps"])
    assert all(not s["activity_id"] or s["activity_id"] == "Général" for s in result["steps"])
    stored = svc.store.list_path_steps(result["id"])
    assert len(stored) == 6
    # La requête est bien partie vers /api/chat (jamais /api/embed).
    assert captured["request_body"]["model"] == svc.config.tutor_model


@pytest.mark.asyncio
async def test_rag_off_empty_books_uses_knowledge_prompt_with_goal(
    tmp_path: Path,
) -> None:
    """L'objectif guide le prompt « sans sources » (matière + niveau inclus)."""
    captured: dict = {}
    svc = _make_service(
        tmp_path, embedding_disabled=True,
        payload=json.dumps(_valid_steps()), captured=captured,
    )
    sid = svc.store.create_subject("Mathématiques").id
    goal = "Réviser les boucles pour le contrôle"
    result = await svc.service.generate_path_from_books(sid, [], description=goal)
    assert result["description"] == goal
    payload = captured["request_body"]
    system = payload["messages"][0]["content"]
    assert "Mathématiques" in system
    assert "intermediate" in system
    assert "Objectif de l'élève" in system
    assert goal in system
    assert "N'invente AUCUNE citation de livre" in system
    stored = svc.store.get_learning_path(result["id"])
    assert stored is not None and stored.description == goal


@pytest.mark.asyncio
async def test_rag_on_empty_books_raises_path_generation_error(
    tmp_path: Path,
) -> None:
    """RAG on + book_ids=[] ⇒ PathGenerationError (comportement actuel)."""
    svc = _make_service(tmp_path, embedding_disabled=False)
    assert svc.service.is_embedding_disabled is False
    sid = svc.store.create_subject("Histoire").id
    with pytest.raises(PathGenerationError, match="insuffisants"):
        await svc.service.generate_path_from_books(sid, [])
    assert svc.store.list_learning_paths(sid) == [], "aucun parcours coquille"


@pytest.mark.asyncio
async def test_rag_off_empty_books_degenerate_llm_raises(tmp_path: Path) -> None:
    """RAG off sans livres : sortie sous le minimum viable ⇒ erreur explicite."""
    svc = _make_service(
        tmp_path, embedding_disabled=True,
        payload=json.dumps([{"title": "Leçon unique", "type": "concept", "duration": 15}]),
    )
    sid = svc.store.create_subject("Biologie").id
    with pytest.raises(PathGenerationError, match="insuffisante"):
        await svc.service.generate_path_from_books(sid, [])
    assert svc.store.list_learning_paths(sid) == [], "aucun parcours coquille"