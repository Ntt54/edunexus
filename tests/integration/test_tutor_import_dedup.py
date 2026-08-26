"""Integration test: import -> index -> dedup -> delete (T018, US1).

Full offline flow with a MockTransport. Verifies:
- import reaches ``indexed`` with chunk_count > 0 and cached embeddings
- re-import of the SAME file is a no-op (fingerprint hit, zero new embeddings)
- delete cascades the book's chunks (and its embedding BLOBs)
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _make_counting_embed_transport(dim: int = 4):
    state = {"calls": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inputs = body.get("input", [])
        n = len(inputs)
        state["calls"] += 1
        vecs = [[float((i * 3 + j) % 5) / 5 for j in range(dim)] for i in range(n)]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler), state


def test_retriever_cache_is_invalidated_after_indexing(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    transport, _state = _make_counting_embed_transport()
    client = OllamaClient(transport=transport)
    config = Config(config_dir=tmp_path)
    service = TutorService(store, client, config)

    first = tmp_path / "first.txt"
    first.write_text("alpha lesson " * 40, encoding="utf-8")
    service.import_and_index("Math", str(first), background=False)
    subject = store.list_subjects()[0]
    _idx, first_meta, _titles = service.retriever._index_for(subject.id)
    assert first_meta

    second = tmp_path / "second.txt"
    second.write_text("beta lesson " * 40, encoding="utf-8")
    service.import_and_index("Math", str(second), background=False)

    _idx, current_meta, _titles = service.retriever._index_for(subject.id)
    assert any("beta lesson" in row["text"] for row in current_meta.values())


def test_import_index_dedup_delete(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    transport, state = _make_counting_embed_transport()
    client = OllamaClient(transport=transport)
    config = Config(config_dir=tmp_path)
    service = TutorService(store, client, config)

    book_file = tmp_path / "algebra.txt"
    book_file.write_text("Algebra is the study of symbols and the rules. " * 40, encoding="utf-8")

    # 1) import + index (synchronous)
    book = service.import_and_index("Math", str(book_file), background=False)
    assert store.get_book_status(book.id) == "indexed"
    assert book.chunks_done > 0

    cached = store._conn.execute("SELECT COUNT(*) AS c FROM embeddings").fetchone()["c"]
    assert cached > 0, "embeddings must be cached after first import"
    assert state["calls"] == 1, "exactly one embed batch expected"

    subjects = store.list_subjects()
    assert len(subjects) == 1
    subj = subjects[0]
    assert len(store.list_books(subj.id)) == 1

    # 2) re-import the SAME file -> immediate no-op (fingerprint hit)
    book2 = service.import_and_index("Math", str(book_file), background=False)
    assert book2.id == book.id, "re-import must return the existing book"
    assert state["calls"] == 1, "re-import must perform ZERO new embed calls"
    assert len(store.list_books(subj.id)) == 1, "no duplicate book row"

    # 3) delete the book -> its chunks vanish (cascade)
    store.delete_book(book.id)
    chunks = store._conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE book_id = ?", (book.id,)
    ).fetchone()["c"]
    assert chunks == 0, "chunks must be purged on delete"
    assert store.list_books(subj.id) == []
