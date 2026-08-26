"""Offline unit tests for the US7 knowledge tools (T046–T049).

Covers:
(a) ``Retriever.locate`` returns book/chapter/page rows from the index with
    ZERO LLM calls (chat_stream is monkeypatched to raise — locate must not
    invoke it).
(b) ``Retriever.rank_books`` aggregates per-chunk scores per book.
(c) ``TutorService.build_knowledge_map`` returns nodes+edges assembled from
    stored relations with no LLM call.
(d) compare mode (``ask(mode="compare")``) streams a synthesis whose ``sources``
    frame cites >=2 books and whose content references both (offline, scripted
    chat frames).

All tests are fully offline: a scripted fake Ollama client (embed + chat) and a
tmp_path-backed LibraryStore. No daemon, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ollama_tutor.client import InferenceStats, StreamEvent
from ollama_tutor.config import Config
from ollama_tutor.tutor.models import _uid
from ollama_tutor.tutor.service import TutorService
from ollama_tutor.tutor.store import LibraryStore

DIM = 4
VEC_A = [1.0, 0.0, 0.0, 0.0]
VEC_B = [0.0, 1.0, 0.0, 0.0]
VEC_BOTH = [0.5, 0.5, 0.0, 0.0]

COMPARE_FRAMES = [
    {
        "message": {
            "content": (
                "Synthèse citant [LivreA — chapitre 1, p. 1] et "
                "[LivreB — chapitre 2, p. 2]. Différences entre les ouvrages : "
                "le livre A présente la notion simplement, le livre B l'approfondit."
            )
        },
        "done": False,
    },
    {
        "done": True,
        "prompt_eval_count": 20,
        "eval_count": 12,
        "eval_rate": 15.0,
    },
]


class FakeToolsClient:
    """Scripted Ollama client: embed maps a keyword to a vector, chat streams
    fixed frames. No network, no daemon."""

    def __init__(self, embed_map: dict[str, list[float]], chat_frames: list[dict[str, Any]]):
        self.embed_map = embed_map
        self.chat_frames = chat_frames
        self.chat_calls = 0

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        text = inputs[0] if inputs else ""
        for key, vec in self.embed_map.items():
            if key and key in text:
                return [vec]
        return [self.embed_map.get("", [0.0] * DIM)]

    async def chat_stream(self, messages, model, *, think=False, options=None,
                          format=None, tools=None):
        self.chat_calls += 1
        for f in self.chat_frames:
            msg = f.get("message", {})
            if msg.get("thinking"):
                yield StreamEvent(kind="thinking", text=msg["thinking"])
            if msg.get("content"):
                yield StreamEvent(kind="content", text=msg["content"])
            if f.get("done"):
                yield StreamEvent(
                    kind="done",
                    stats=InferenceStats(
                        model=model,
                        prompt_tokens=f.get("prompt_eval_count", 0),
                        generated_tokens=f.get("eval_count", 0),
                        eval_duration=1.0,
                    ),
                )


def seed_chunk(store: LibraryStore, subject_id: str, book_id: str, text: str,
               vec: list[float], chapter: str | None, page: int | None,
               ordinal: int = 0, embedding_model: str = "embeddinggemma") -> str:
    """Insert a chunk row with an explicit chapter/page and embedding (bypassing
    the NULL-chapter default of ``add_chunks``)."""
    import hashlib

    import numpy as np

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    blob = np.array(vec, dtype=np.float32).tobytes()
    cid = _uid()
    store._conn.execute(
        "INSERT INTO chunks "
        "(id, subject_id, book_id, ordinal, text, text_hash, chapter, page, "
        "position, content_type, embedding, embedding_model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prose', ?, ?)",
        (cid, subject_id, book_id, ordinal, text, text_hash, chapter, page,
         0.0, blob, embedding_model),
    )
    store._conn.commit()
    return cid


def seed_subject_book_chunk(store: LibraryStore, tmp_path: Path, name: str,
                            title: str, text: str, vec: list[float],
                            chapter: str | None, page: int | None):
    """Create a subject + one indexed book + one chunk (with chapter/page)."""
    subject = store.create_subject(name)
    p = tmp_path / f"{title}.txt"
    p.write_text(text)
    book = store.import_document(subject.id, p)
    store.mark_indexed(book.id, 1)
    seed_chunk(store, subject.id, book.id, text, vec, chapter, page)
    return subject, book


@pytest.mark.asyncio
async def test_locate_no_llm_calls(tmp_path: Path, monkeypatch):
    """T046 (a): locate returns book/chapter/page rows with ZERO LLM calls."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = FakeToolsClient(
        embed_map={"notion A": VEC_A, "": [0.0] * DIM},
        chat_frames=COMPARE_FRAMES,
    )
    # locate must NOT call chat_stream — make any such call explode.
    async def boom(*args, **kwargs):
        raise RuntimeError("LLM chat_stream must not be called by locate")

    monkeypatch.setattr(client, "chat_stream", boom)
    service = TutorService(store, client, config)

    subj, _book = seed_subject_book_chunk(
        store, tmp_path, "SujetX", "LivreA", "Contenu A.", VEC_A, "Chapitre 1", 1
    )

    rows = service.retriever.locate(subj.id, "contenu")
    assert rows, "expected located rows"
    # Every row carries the grouped book/chapter/page coordinates (FR-031).
    for r in rows:
        assert "book" in r and "chapter" in r and "page" in r and "score" in r
    assert rows[0]["book"] == "LivreA"
    assert rows[0]["chapter"] == "Chapitre 1"
    assert rows[0]["page"] == 1
    # No LLM call happened (chat_stream was monkeypatched to raise).
    assert client.chat_calls == 0


@pytest.mark.asyncio
async def test_rank_books_aggregates(tmp_path: Path):
    """T046 (b): rank_books aggregates per-chunk scores per book (FR-032)."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = FakeToolsClient(
        embed_map={"both": VEC_BOTH, "": [0.0] * DIM},
        chat_frames=COMPARE_FRAMES,
    )
    service = TutorService(store, client, config)

    subj, _a = seed_subject_book_chunk(
        store, tmp_path, "SujetY", "LivreA", "Contenu A.", VEC_A, "C1", 1
    )
    p2 = tmp_path / "LivreB.txt"
    p2.write_text("Contenu B.")
    book_b = store.import_document(subj.id, p2)
    store.mark_indexed(book_b.id, 1)
    seed_chunk(store, subj.id, book_b.id, "Contenu B.", VEC_B, "C2", 2)

    ranked = service.retriever.rank_books(subj.id, "contenu")
    books = {r["book"] for r in ranked}
    assert books == {"LivreA", "LivreB"}
    # Each book contributed exactly one chunk, so the aggregated score equals
    # that chunk's cosine score; both chunks are equally relevant to "both".
    assert len(ranked) == 2
    assert abs(ranked[0]["score"] - ranked[1]["score"]) < 1e-6
    assert ranked[0]["score"] > 0


@pytest.mark.asyncio
async def test_build_knowledge_map(tmp_path: Path):
    """T048 (c): build_knowledge_map returns nodes+edges with no LLM call."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = FakeToolsClient(embed_map={"": [0.0] * DIM}, chat_frames=COMPARE_FRAMES)
    service = TutorService(store, client, config)

    subj = store.create_subject("SujetZ")
    c1 = store.upsert_concept(subj.id, "Concept1")
    c2 = store.upsert_concept(subj.id, "Concept2")
    store.upsert_relation(subj.id, c1.id, c2.id, "prerequisite")

    graph = service.build_knowledge_map(subj.id)
    node_ids = {n["id"] for n in graph["nodes"]}
    assert node_ids == {c1.id, c2.id}
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["relation"] == "prerequisite"
    assert edge["from_concept_id"] == c1.id
    assert edge["to_concept_id"] == c2.id
    # Pure data assembly — the LLM was never touched.
    assert client.chat_calls == 0


@pytest.mark.asyncio
async def test_compare_mode_streams_synthesis(tmp_path: Path):
    """T047 (d): compare mode cites >=2 books and streams a synthesis."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = FakeToolsClient(
        embed_map={"both": VEC_BOTH, "": [0.0] * DIM},
        chat_frames=COMPARE_FRAMES,
    )
    service = TutorService(store, client, config)

    subj, _a = seed_subject_book_chunk(
        store, tmp_path, "SujetC", "LivreA", "Contenu A.", VEC_A, "Chapitre 1", 1
    )
    p2 = tmp_path / "LivreB.txt"
    p2.write_text("Contenu B.")
    book_b = store.import_document(subj.id, p2)
    store.mark_indexed(book_b.id, 1)
    seed_chunk(store, subj.id, book_b.id, "Contenu B.", VEC_B, "Chapitre 2", 2)

    frames = [f async for f in service.ask(subj.name, "both", mode="compare")]
    types = [f["type"] for f in frames]

    # sources frame fires first and cites both books (FR-033).
    assert types[0] == "sources"
    cited = {s["book"] for s in frames[0]["sources"]}
    assert cited >= {"LivreA", "LivreB"}

    # A synthesis was streamed and references both sources.
    assert "delta" in types
    assert types[-1] == "end"
    content = "".join(f["text"] for f in frames if f["type"] == "delta")
    assert "LivreA" in content and "LivreB" in content
