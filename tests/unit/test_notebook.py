"""Unit tests for the subject notebook (Feature 008, US8).

Covers notebook CRUD + notes (T057) and RAG actions: summarize, quiz without
answer, sources linkage and delete (T058). Offline: no LLM is required — the
service falls back to deterministic content.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.notebook import NotebookService
from src.ollama_tutor.tutor.store import LibraryStore


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path: Path):
    s = LibraryStore(tmp_path)
    yield s
    s.close()


def _seed_subject(store: LibraryStore, tmp_path: Path, name: str = "Maths") -> str:
    """Create a subject with indexed chunks (RAG context + source linkage)."""
    sid = store.create_subject(name).id
    p = tmp_path / "book.txt"
    p.write_text("contenu du livre " * 20, encoding="utf-8")
    book = store.import_document(sid, p)
    chunks = [
        {"text": f"Contenu du chapitre {c}. " * 20, "chapter": c, "section": c, "page": i + 1}
        for i, c in enumerate(["Introduction", "Variables", "Boucles"])
    ]
    embeddings = [[0.1] * 4 for _ in chunks]
    store.add_chunks(sid, book.id, chunks, embeddings, model="test")
    return sid


# ----------------------------------------------------------------------
# T057 — CRUD + notes
# ----------------------------------------------------------------------

def test_get_creates_notebook(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    data = svc.get(sid)
    nb = data["notebook"]
    assert nb["subject_id"] == sid
    assert nb["notes"] == []
    assert nb["outputs"] == []


def test_get_is_idempotent(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    first = svc.get(sid)["notebook"]["id"]
    second = svc.get(sid)["notebook"]["id"]
    assert first == second


def test_add_note(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    svc.add_note(sid, "Revoir les boucles")
    data = svc.get(sid)
    assert data["notebook"]["notes"] == ["Revoir les boucles"]


def test_add_note_appends(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    svc.add_note(sid, "Note 1")
    svc.add_note(sid, "Note 2")
    assert svc.get(sid)["notebook"]["notes"] == ["Note 1", "Note 2"]


def test_add_empty_note_raises(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    with pytest.raises(ValueError):
        svc.add_note(sid, "   ")


def test_get_lists_sources(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    sources = svc.get(sid)["notebook"]["sources"]
    assert len(sources) == 1  # one imported book


# ----------------------------------------------------------------------
# T058 — Actions (summarize, quiz without answer, sources linkage, delete)
# ----------------------------------------------------------------------

def test_summarize_action(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    out = _run(svc.run_action(sid, "summarize_source"))
    assert out["output"]["kind"] == "summary"
    assert "Résumé" in out["output"]["content"]


def test_quiz_without_answer(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    out = _run(svc.run_action(sid, "quiz_without_answer"))
    assert out["output"]["kind"] == "quiz"
    # Questions only, no answers (FR-033).
    assert "Question 1" in out["output"]["content"]
    assert "Réponse" not in out["output"]["content"]


def test_action_links_sources(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    out = _run(svc.run_action(sid, "summarize_source"))
    sources = out["output"]["sources"]
    assert len(sources) == 1  # linked to the imported book (FR-035)
    assert sources[0]["book_id"]


def test_action_persists_output(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    out = _run(svc.run_action(sid, "create_study_sheet"))
    output_id = out["output"]["id"]
    outputs = svc.get(sid)["notebook"]["outputs"]
    assert any(o["id"] == output_id for o in outputs)


def test_delete_output(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    out = _run(svc.run_action(sid, "summarize_source"))
    output_id = out["output"]["id"]
    assert svc.delete_output(output_id) == {"deleted": True}
    outputs = svc.get(sid)["notebook"]["outputs"]
    assert all(o["id"] != output_id for o in outputs)


def test_unknown_action_raises(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path)
    svc = NotebookService(store)
    with pytest.raises(ValueError):
        _run(svc.run_action(sid, "nope"))
