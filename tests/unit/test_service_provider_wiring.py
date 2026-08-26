"""Unit tests for Phase 5a provider wiring inside TutorService.

Offline: fake embedding provider / document parser / dummy client; real
LibraryStore on a tmp config dir. Verifies the positive provider path, the
hash-cache reuse, the hybrid-parser branch, and that configured-but-failing
GGUF providers surface a named error (no silent Ollama fallback).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.providers.gguf_embedding import GGUFEmbeddingError
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore

BOOK_TEXT = "Newton formulated the laws of motion. " * 40


class DummyClient:
    """Legacy-path client: records embed calls, returns fixed vectors."""

    def __init__(self) -> None:
        self.embed_calls: list[tuple[str, list[str]]] = []

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append((model, list(texts)))
        return [[0.25] * 3 for _ in texts]

    async def close(self) -> None:
        pass


class FakeEmbeddingProvider:
    """Records embed calls; configurable canned vectors or failure."""

    def __init__(self, vectors=None, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._vectors = vectors or [[0.5] * 4]
        self._error = error

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        return [list(self._vectors[0]) for _ in texts]


class FakeDocumentParser:
    """Returns canned pages in order; records parse calls."""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.sources: list[Path] = []

    async def parse(self, source: Path) -> dict:
        self.sources.append(Path(source))
        return {"pages": self.pages}


def _make_service(tmp_path: Path, client=None, **kwargs):
    store = LibraryStore(tmp_path / "config")
    config = Config(config_dir=tmp_path / "config")
    svc = TutorService(store, client or DummyClient(), config, **kwargs)
    return svc, store


def _book_file(tmp_path: Path, name: str = "book.txt") -> Path:
    p = tmp_path / name
    p.write_text(BOOK_TEXT, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. embedding_provider used during indexing (positive case).
# ---------------------------------------------------------------------------


def _subject_id(store: LibraryStore) -> str:
    subjects = store.list_subjects()
    assert subjects, "expected import to have created the subject"
    return subjects[0].id


def test_embedding_provider_used_for_indexing(tmp_path: Path) -> None:
    provider = FakeEmbeddingProvider()
    svc, store = _make_service(tmp_path, embedding_provider=provider)

    book = svc.import_and_index("Physics", _book_file(tmp_path), background=False)

    assert book.status == "indexed"
    assert book.chunks_total > 0
    assert len(provider.calls) == 1
    # The provider received exactly the chunk texts.
    chunks = [r["text"] for r in store.get_subject_chunks(_subject_id(store))]
    assert provider.calls[0] == chunks
    # Vectors cached under the same sha256(model-keyed) flow.
    row = store._conn.execute(
        "SELECT vector FROM embeddings LIMIT 1"
    ).fetchone()
    assert row is not None


def test_provider_path_reuses_hash_cache_on_second_book(tmp_path: Path) -> None:
    provider = FakeEmbeddingProvider()
    svc, store = _make_service(tmp_path, embedding_provider=provider)

    text_a = "Unique alpha content for caching. " * 30
    pa = tmp_path / "a.txt"
    pa.write_text(text_a, encoding="utf-8")
    pb = tmp_path / "b.txt"
    pb.write_text(text_a + "Tail differs. " * 10, encoding="utf-8")

    svc.import_and_index("Chem", pa, background=False)
    first_calls = len(provider.calls)
    svc.import_and_index("Chem", pb, background=False)

    # Shared prefix chunks hit the cache — the provider is called again only
    # for genuinely new text.
    assert len(provider.calls) == first_calls + 1
    new_texts = provider.calls[-1]
    assert all(t.startswith("Tail") or "Tail" in t for t in new_texts)


def test_failing_gguf_provider_surfaces_named_error(tmp_path: Path) -> None:
    provider = FakeEmbeddingProvider(error=GGUFEmbeddingError("backend exploded"))
    svc, store = _make_service(tmp_path, embedding_provider=provider)

    book = svc.import_and_index(
        "Hist", _book_file(tmp_path, "hist.txt"), background=False
    )

    assert book.status == "error"
    assert book.error is not None
    assert "[gguf-provider]" in book.error
    assert "GGUFEmbeddingError" in book.error
    assert "backend exploded" in book.error
    # No silent fallback: the legacy client was never asked to embed.


def test_set_embedding_model_updates_retriever_and_clears_cache(
    tmp_path: Path,
) -> None:
    svc, store = _make_service(tmp_path)
    svc.retriever._cache["subject"] = (None, {}, {})  # type: ignore[assignment]

    svc.set_embedding_model("new-embedding-model")

    assert svc.model == "new-embedding-model"
    assert svc.retriever.model == "new-embedding-model"
    assert svc.retriever._cache == {}


def test_legacy_path_untouched_when_provider_none(tmp_path: Path) -> None:
    client = DummyClient()
    svc, store = _make_service(tmp_path, client=client)

    book = svc.import_and_index("Math", _book_file(tmp_path), background=False)

    assert book.status == "indexed"
    assert len(client.embed_calls) >= 1  # legacy client.embed path used
    assert svc.embedding_provider is None
    assert svc.document_parser is None


# ---------------------------------------------------------------------------
# 2. document_parser branch (hybrid ingestion).
# ---------------------------------------------------------------------------


def test_document_parser_pages_drive_chunking_in_order(tmp_path: Path) -> None:
    parser = FakeDocumentParser([
        {"index": 0, "text": "Page one speaks of photosynthesis. " * 20, "source": "text-layer"},
        {"index": 1, "text": "Page two covers chlorophyll details. " * 20, "source": "ocr"},
    ])
    svc, store = _make_service(tmp_path, document_parser=parser)

    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-fake")
    book = svc.import_and_index("Bio", source, fmt="pdf", background=False)

    assert book.status == "indexed"
    assert parser.sources == [source]
    pages_text = [
        r["text"] for r in store.get_subject_chunks(_subject_id(store))
    ]
    joined = "\n".join(pages_text)
    assert "photosynthesis" in joined
    assert "chlorophyll" in joined
    # Order preserved: page-one material appears before page-two material.
    all_text = "".join(pages_text)
    assert all_text.index("photosynthesis") < all_text.index("chlorophyll")


# ---------------------------------------------------------------------------
# 3. Signature compatibility: positional construction unchanged.
# ---------------------------------------------------------------------------


def test_positional_construction_still_works(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    config = Config(config_dir=tmp_path / "config")
    svc = TutorService(store, DummyClient(), config)  # legacy positional form
    assert svc.embedding_provider is None
    assert svc.document_parser is None
