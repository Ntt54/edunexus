"""Unit tests for the competency graph builder (Feature 008, US2).

Offline: pure logic over ``LibraryStore`` with a ``tmp_path`` config dir.
No LLM, no network, no daemon.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.graph import GraphBuilder
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path):
    s = LibraryStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def builder(store):
    return GraphBuilder(store)


def _seed_subject(store, name: str) -> str:
    return store.create_subject(name).id


def _seed_chunks(store, subject_id: str, chapters: list[str], tmp_path) -> None:
    """Insert indexed chunks with chapter titles (deterministic graph input).

    A real book is imported first so ``add_chunks`` satisfies the FK on
    ``book_id`` (mirrors test_embedding_provenance.py).
    """
    p = tmp_path / "book.txt"
    p.write_text("contenu du livre " * 20, encoding="utf-8")
    book = store.import_document(subject_id, p)
    chunks = [
        {"text": f"Contenu du chapitre {c}. " * 20, "chapter": c, "section": c, "page": i + 1}
        for i, c in enumerate(chapters)
    ]
    embeddings = [[0.1] * 4 for _ in chunks]
    store.add_chunks(subject_id, book.id, chunks, embeddings, model="test")


def test_build_creates_nodes_from_chapters(builder, store, tmp_path):
    sid = _seed_subject(store, "Java")
    _seed_chunks(store, sid, ["Introduction", "Variables", "Boucles"], tmp_path)
    result = builder.build(sid)
    assert result["nodes"] == 3
    graph = builder.get_graph(sid)
    titles = {n["title"] for n in graph["nodes"]}
    assert titles == {"Introduction", "Variables", "Boucles"}


def test_build_merges_duplicate_titles(builder, store, tmp_path):
    sid = _seed_subject(store, "Java")
    _seed_chunks(store, sid, ["Variables", "Variables", "Boucles"], tmp_path)
    result = builder.build(sid)
    assert result["nodes"] == 2  # "Variables" fusionné par identifiant conceptuel


def test_build_detects_prerequisite_edges(builder, store, tmp_path):
    sid = _seed_subject(store, "Java")
    _seed_chunks(store, sid, ["Introduction", "Variables", "Boucles"], tmp_path)
    builder.build(sid)
    graph = builder.get_graph(sid)
    relations = {e["relation"] for e in graph["edges"]}
    assert "requires" in relations
    # L'introduction (prérequis) doit être source d'une arête requires.
    intro = next(n for n in graph["nodes"] if n["title"] == "Introduction")
    assert any(e["source_node_id"] == intro["id"] for e in graph["edges"])


def test_build_is_idempotent(builder, store, tmp_path):
    sid = _seed_subject(store, "Java")
    _seed_chunks(store, sid, ["Introduction", "Variables"], tmp_path)
    first = builder.build(sid)
    second = builder.build(sid)
    assert first["nodes"] == second["nodes"] == 2
    assert first["edges"] == second["edges"]


def test_validate_node_marks_user_confirmed(builder, store, tmp_path):
    sid = _seed_subject(store, "Java")
    _seed_chunks(store, sid, ["Introduction"], tmp_path)
    builder.build(sid)
    node = builder.get_graph(sid)["nodes"][0]
    assert node["validation_status"] == "extracted"
    builder.validate_node(node["id"])
    graph = builder.get_graph(sid)
    assert graph["nodes"][0]["validation_status"] == "user_confirmed"


def test_dashboard_aggregates_categories(builder, store, tmp_path):
    sid = _seed_subject(store, "Java")
    _seed_chunks(store, sid, ["Introduction", "Variables"], tmp_path)
    builder.build(sid)
    dash = builder.dashboard(sid)
    # Aucun nœud maîtrisé ni proposé par IA au départ.
    assert dash["covered"] == []
    assert dash["unconfirmed"] == []
    assert len(dash["uncovered"]) == 2
