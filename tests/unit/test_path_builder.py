"""Unit tests for explainable path building (Feature 008, US3).

Builds a competency graph via GraphBuilder from seeded chunks, then checks
that PathBuilder produces an ordered, explainable path respecting
prerequisites (FR-013/FR-014).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.graph import GraphBuilder
from src.ollama_tutor.tutor.path_builder import PathBuilder
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def _seed_subject(store: LibraryStore, tmp_path: Path, chapters: list[str]) -> str:
    subject = store.create_subject("Informatique")
    p = tmp_path / "book.txt"
    p.write_text("contenu du livre " * 20, encoding="utf-8")
    book = store.import_document(subject.id, p)
    chunks = [
        {"text": f"Contenu du chapitre {c}. " * 20, "chapter": c, "section": c, "page": i + 1}
        for i, c in enumerate(chapters)
    ]
    store.add_chunks(subject.id, book.id, chunks, [[0.1] * 4 for _ in chunks], model="test")
    return subject.id


def test_generate_empty_graph_returns_empty(store: LibraryStore, tmp_path: Path):
    sid = store.create_subject("Vide").id
    result = PathBuilder(store).generate(sid)
    assert result["path"]["steps"] == []


def test_generate_orders_by_prerequisites(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path, ["Introduction", "Variables", "Boucles"])
    GraphBuilder(store).build(sid)
    result = PathBuilder(store).generate(sid)
    steps = result["path"]["steps"]
    assert len(steps) >= 2
    # First step must have no prerequisites.
    assert steps[0]["prerequisites"] == []
    # Every later step lists earlier titles as prerequisites.
    for i in range(1, len(steps)):
        assert steps[i]["prerequisites"], f"step {i} missing prerequisites"


def test_generate_steps_are_explainable(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path, ["Introduction", "Variables"])
    GraphBuilder(store).build(sid)
    result = PathBuilder(store).generate(sid)
    for step in result["path"]["steps"]:
        assert step["why_now"]
        assert step["planned_activity"]
        assert step["expected_proof"]
        assert "activity_type" in step and "activity_id" in step


def test_generate_is_idempotent(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path, ["Introduction", "Variables"])
    GraphBuilder(store).build(sid)
    first = PathBuilder(store).generate(sid)
    second = PathBuilder(store).generate(sid)
    # Regeneration replaces steps (new ids) but must preserve order/content.
    assert [s["title"] for s in first["path"]["steps"]] == [s["title"] for s in second["path"]["steps"]]


def test_reorder_respects_exclusion(store: LibraryStore, tmp_path: Path):
    sid = _seed_subject(store, tmp_path, ["Introduction", "Variables", "Boucles"])
    GraphBuilder(store).build(sid)
    result = PathBuilder(store).generate(sid)
    steps = result["path"]["steps"]
    assert len(steps) >= 2
    # Exclude the first step.
    payload = [{"id": s["id"], "ordinal": i, "excluded": (i == 0)} for i, s in enumerate(steps)]
    reordered = PathBuilder(store).reorder(sid, payload)
    remaining = reordered["path"]["steps"]
    assert len(remaining) == len(steps) - 1
    assert all(s["id"] != steps[0]["id"] for s in remaining)
