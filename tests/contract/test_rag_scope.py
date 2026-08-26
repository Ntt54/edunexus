"""Contract tests : périmètre RAG par sources actives (005-platform-ui-library).

Valide le filtre ``book_ids`` du Retriever (T024) : filtrage AVANT scoring,
liste vide = aucun contexte, None = illimité. Hors-ligne via un client
d'embedding déterministe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.retrieval import Retriever
from src.ollama_tutor.tutor.store import LibraryStore


class FakeEmbedClient:
    """embed() déterministe : 'java'→[1,0], 'reseau'→[0,1], sinon [0.7,0.7]."""

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in inputs:
            low = t.lower()
            if "java" in low:
                out.append([1.0, 0.0])
            elif "reseau" in low or "réseau" in low:
                out.append([0.0, 1.0])
            else:
                out.append([0.7, 0.7])
        return out


@pytest.fixture
def seeded(tmp_path: Path):
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Informatique")
    pa = tmp_path / "java_a.txt"
    pa.write_text("fondamentaux java " * 20, encoding="utf-8")
    pb = tmp_path / "reseau_b.txt"
    pb.write_text("reseaux ip tcp " * 20, encoding="utf-8")
    book_a = store.import_document(subject.id, pa)
    book_b = store.import_document(subject.id, pb)
    store.add_chunks(subject.id, book_a.id, ["chunk java"], [[1.0, 0.0]], "m")
    store.add_chunks(subject.id, book_b.id, ["chunk reseau"], [[0.0, 1.0]], "m")
    return store, subject.id, book_a.id, book_b.id


@pytest.mark.asyncio
async def test_no_scope_returns_both_books(seeded):
    store, sid, a, b = seeded
    r = Retriever(store, FakeEmbedClient(), "m")
    chunks = await r.retrieve(sid, "contenu des cours", 10)
    assert {c.book_id for c in chunks} == {a, b}


@pytest.mark.asyncio
async def test_scope_filters_to_selected_book(seeded):
    store, sid, a, b = seeded
    r = Retriever(store, FakeEmbedClient(), "m")
    chunks = await r.retrieve(sid, "contenu des cours", 10, book_ids=[a])
    assert {c.book_id for c in chunks} == {a}


@pytest.mark.asyncio
async def test_empty_scope_means_no_context(seeded):
    store, sid, a, b = seeded
    r = Retriever(store, FakeEmbedClient(), "m")
    chunks = await r.retrieve(sid, "contenu des cours", 10, book_ids=[])
    assert chunks == []


@pytest.mark.asyncio
async def test_unknown_book_ids_yield_nothing(seeded):
    store, sid, a, b = seeded
    r = Retriever(store, FakeEmbedClient(), "m")
    chunks = await r.retrieve(sid, "contenu des cours", 10, book_ids=["inconnu"])
    assert chunks == []
