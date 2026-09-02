"""Unit tests for conversation photo import (Feature 008, US7).

Covers OCR recognition, confirmation gate (FR-031) and source linkage
(FR-032). Offline: no OCR provider is required — the service falls back to
reading the source file as text.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.conversation_photo import ConversationPhotoService
from src.ollama_tutor.tutor.store import LibraryStore


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def _write_photo(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "photo.txt"
    p.write_text(content, encoding="utf-8")
    return p


def _conv(store: LibraryStore, conv_id: str = "conv-1") -> str:
    """Create a real tutoring session row so the FK is satisfied."""
    subject = store.create_subject("Maths")
    store.create_tutoring_session(subject.id, session_id=conv_id)
    return conv_id


def test_import_photo_recognizes_text(store: LibraryStore, tmp_path: Path):
    src = _write_photo(tmp_path, "2x + 3 = 7\nRésoudre pour x")
    svc = ConversationPhotoService(store)
    photo = _run(svc.import_photo(_conv(store), str(src)))
    assert photo["conversation_id"] == "conv-1"
    assert photo["confirmation_status"] == "pending"
    assert "2x + 3 = 7" in photo["recognized_text"]


def test_import_photo_persists(store: LibraryStore, tmp_path: Path):
    src = _write_photo(tmp_path, "contenu")
    svc = ConversationPhotoService(store)
    photo = _run(svc.import_photo(_conv(store), str(src)))
    fetched = svc.get(photo["id"])
    assert fetched["id"] == photo["id"]
    assert fetched["recognized_text"] == "contenu"


def test_confirm_photo_links_source(store: LibraryStore, tmp_path: Path):
    src = _write_photo(tmp_path, "contenu")
    svc = ConversationPhotoService(store)
    photo = _run(svc.import_photo(_conv(store), str(src)))
    confirmed = svc.confirm(photo["id"])
    assert confirmed["confirmation_status"] == "confirmed"
    # Source linkage: the photo path is recorded on the photo row (FR-032).
    assert confirmed["source_linkage"] == str(src)


def test_confirm_unknown_photo_raises(store: LibraryStore):
    svc = ConversationPhotoService(store)
    with pytest.raises(KeyError):
        svc.confirm("nope")
