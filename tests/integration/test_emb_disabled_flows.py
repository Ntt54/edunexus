"""RAG off généralisé : AUCUN extrait de livre n'atteint le LLM.

100 % offline (httpx.MockTransport / LLM factice injecté, pas de daemon).
Couvre les 3 flux de bibliothèque restants (leçons / lesson_discussion
traités dans une lane parallèle) :
- Carnet de matière (NotebookService) : ``_default_rag_context`` → [] avec
  le flag embeddings désactivé, action RAG sans extrait dans le prompt LLM,
  repli honnête en sortie ; contrôle avec le flag off ⇒ extraits présents.
- Fiche de révision (``generate_revision_sheet``) : toggle off ⇒ prompt
  sans les chunks, "(aucun extrait)" ; contrôle toggle on ⇒ extraits passés.
- Résumé de livre (``summarize_book``) : toggle off ⇒ prompt sans les
  chunks, génération depuis les connaissances ; contrôle toggle on ⇒
  extraits passés.

Fixtures : ``Config(config_dir=tmp_path)`` + ``tutor_embedding_model =
"disabled"``, ``LibraryStore(tmp_path)``, transport qui LÈVE sur
/api/embed (prouve zéro appel embed) — cf. test_lesson_emb_disabled.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.notebook import NotebookService
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHUNK_MARKER = "MARQUEUR-SECRET-BOUCLE-WHILE"


def _make_capture_transport(captured: dict, reply_text: str):
    """Transport : /api/embed LÈVE, /api/tags vide, /api/chat → NDJSON capturé."""

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path.endswith("/api/embed"):
            raise AssertionError(
                "client.embed appelé alors que les embeddings sont désactivés"
            )
        if path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": []}, request=request)
        if path.endswith("/api/chat"):
            try:
                captured["chat_body"] = json.loads(request.content)
            except (TypeError, ValueError):
                pass
        ndjson = (
            json.dumps({"message": {"content": reply_text}, "done": False})
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


def _seed_subject(
    store: LibraryStore, tmp_path: Path, name: str = "Informatique"
) -> tuple[str, str]:
    """Create a subject + book + indexed chunks (vecteurs factices)."""
    sid = store.create_subject(name).id
    p = tmp_path / f"{name}.txt"
    p.write_text(f"contenu {name} " * 20, encoding="utf-8")
    book = store.import_document(sid, p)
    chunks = [
        {
            "text": f"{_CHUNK_MARKER} — contenu du chapitre {c}.",
            "chapter": c,
            "section": c,
            "page": i + 1,
        }
        for i, c in enumerate(["Introduction", "Variables", "Boucles"])
    ]
    store.add_chunks(sid, book.id, chunks, [[0.1] * 4 for _ in chunks], model="test")
    return sid, book.id


def _make_tutor_service(
    tmp_path: Path,
    *,
    disabled: bool,
    captured: dict,
    reply: str,
) -> SimpleNamespace:
    config = Config(config_dir=tmp_path)
    if disabled:
        config.tutor_embedding_model = "disabled"
    store = LibraryStore(tmp_path)
    client = OllamaClient(transport=_make_capture_transport(captured, reply))
    service = TutorService(store, client, config)
    return SimpleNamespace(store=store, config=config, service=service)


class _CapturingLLM:
    """Fake LLM for NotebookService: records prompts, yields content events."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat_stream(self, messages, model, **kwargs):
        self.calls.append(messages)
        yield SimpleNamespace(kind="content", text="Réponse carnet")
        yield SimpleNamespace(kind="done", text="")


def _system_content(captured: dict) -> str:
    msgs = captured["chat_body"]["messages"]
    return next(m["content"] for m in msgs if m.get("role") == "system")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Carnet de matière — NotebookService
# ---------------------------------------------------------------------------


def test_notebook_rag_context_empty_when_embeddings_disabled(
    tmp_path: Path,
) -> None:
    store = LibraryStore(tmp_path)
    sid, _ = _seed_subject(store, tmp_path)
    off = NotebookService(store, embeddings_disabled=True)
    on = NotebookService(store, embeddings_disabled=False)
    # Les chunks existent bien (contrôle)…
    assert on._default_rag_context(sid), "les chunks indexés doivent exister"
    # …mais RAG off ⇒ aucun contexte (aucun extrait vers le LLM).
    assert off._default_rag_context(sid) == []


def test_notebook_action_rag_off_no_chunk_in_prompt(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    sid, _ = _seed_subject(store, tmp_path)
    llm = _CapturingLLM()
    svc = NotebookService(store, llm=llm, embeddings_disabled=True)
    out = _run(svc.run_action(sid, "summarize_source"))
    assert out["output"]["content"] == "Réponse carnet"
    assert out["output"]["sources"] == [], "aucune source livre en RAG off"
    prompt = llm.calls[0][0]["content"]
    assert _CHUNK_MARKER not in prompt, "extrait de livre fuit dans le prompt"
    assert "aucun extrait de livre" in prompt.lower()
    assert "N'invente AUCUNE citation" in prompt


def test_notebook_action_rag_off_deterministic_fallback(tmp_path: Path) -> None:
    """Sans LLM, le repli déterministe reste honnête (contexte vide)."""
    store = LibraryStore(tmp_path)
    sid, _ = _seed_subject(store, tmp_path)
    svc = NotebookService(store, llm=None, embeddings_disabled=True)
    out = _run(svc.run_action(sid, "summarize_source"))
    assert "Aucun extrait de livre disponible" in out["output"]["content"]
    assert _CHUNK_MARKER not in out["output"]["content"]


def test_notebook_action_rag_on_chunks_still_flow(tmp_path: Path) -> None:
    """Contrôle : toggle on ⇒ les extraits passent encore au LLM."""
    store = LibraryStore(tmp_path)
    sid, _ = _seed_subject(store, tmp_path)
    llm = _CapturingLLM()
    svc = NotebookService(store, llm=llm, embeddings_disabled=False)
    _run(svc.run_action(sid, "summarize_source"))
    prompt = llm.calls[0][0]["content"]
    assert _CHUNK_MARKER in prompt, "le contexte RAG doit passer quand RAG on"


# ---------------------------------------------------------------------------
# 2. Fiche de révision — generate_revision_sheet
# ---------------------------------------------------------------------------


def test_revision_sheet_rag_off_no_chunk_in_prompt(tmp_path: Path) -> None:
    captured: dict = {}
    svc = _make_tutor_service(
        tmp_path, disabled=True, captured=captured, reply="Fiche depuis connaissances"
    )
    sid, _ = _seed_subject(svc.store, tmp_path)
    result = svc.service.generate_revision_sheet(sid)
    assert result["sheet"] == "Fiche depuis connaissances"
    system = _system_content(captured)
    assert _CHUNK_MARKER not in system, "extrait de livre fuit dans la fiche"
    assert "(aucun extrait)" in system
    assert "Aucun extrait de livre n'est disponible" in system
    assert "sans inventer" in system


def test_revision_sheet_rag_on_chunks_still_flow(tmp_path: Path) -> None:
    """Contrôle : toggle on ⇒ les extraits passent encore dans la fiche."""
    captured: dict = {}
    svc = _make_tutor_service(
        tmp_path, disabled=False, captured=captured, reply="Fiche extraits"
    )
    sid, _ = _seed_subject(svc.store, tmp_path)
    result = svc.service.generate_revision_sheet(sid)
    assert result["sheet"] == "Fiche extraits"
    assert _CHUNK_MARKER in _system_content(captured)


# ---------------------------------------------------------------------------
# 3. Résumé de livre — summarize_book
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_book_rag_off_no_chunk_in_prompt(tmp_path: Path) -> None:
    captured: dict = {}
    svc = _make_tutor_service(
        tmp_path, disabled=True, captured=captured, reply="Résumé depuis connaissances"
    )
    _, book_id = _seed_subject(svc.store, tmp_path)
    result = await svc.service.summarize_book(book_id)
    assert result["summary"] == "Résumé depuis connaissances"
    assert result["book_title"]
    system = _system_content(captured)
    assert _CHUNK_MARKER not in system, "extrait de livre fuit dans le résumé"
    assert "Aucun extrait du document" in system
    assert "sans citer ni inventer" in system


@pytest.mark.asyncio
async def test_summarize_book_rag_on_chunks_still_flow(tmp_path: Path) -> None:
    """Contrôle : toggle on ⇒ les extraits passent encore dans le résumé."""
    captured: dict = {}
    svc = _make_tutor_service(
        tmp_path, disabled=False, captured=captured, reply="Résumé extraits"
    )
    _, book_id = _seed_subject(svc.store, tmp_path)
    result = await svc.service.summarize_book(book_id)
    assert result["summary"] == "Résumé extraits"
    assert _CHUNK_MARKER in _system_content(captured)