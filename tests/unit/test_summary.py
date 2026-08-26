"""Tests for US1 — document summary prompt and service flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.client import StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.prompts import build_summary_prompt
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def test_build_summary_prompt_contains_book_title():
    prompt = build_summary_prompt(["chunk1"], "Mon Livre")
    assert "Mon Livre" in prompt


def test_build_summary_prompt_with_chapter():
    prompt = build_summary_prompt(["chunk1"], "Mon Livre", chapter="Chapitre 3")
    assert "Chapitre 3" in prompt
    assert "chapitre" in prompt.lower()


def test_build_summary_prompt_without_chapter():
    prompt = build_summary_prompt(["chunk1", "chunk2"], "Mon Livre")
    assert "Extrait 1" in prompt
    assert "Extrait 2" in prompt


class _SummaryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list, str]] = []

    async def chat_stream(self, messages, model, **kwargs):
        self.calls.append((messages, model))
        yield StreamEvent(kind="content", text="Résumé généré")
        yield StreamEvent(kind="done")


@pytest.mark.asyncio
async def test_summarize_book_resolves_subject_through_join(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subject = store.create_subject("Mathématiques")
    source = tmp_path / "cours.txt"
    source.write_text("Contenu de cours sur les fonctions.", encoding="utf-8")
    book = store.import_document(subject.id, source)
    store.add_chunks(
        subject.id,
        book.id,
        ["Contenu de cours sur les fonctions."],
        [[1.0, 0.0]],
        "embeddinggemma",
    )
    store.mark_indexed(book.id, 1)

    client = _SummaryClient()
    service = TutorService(store, client, Config(config_dir))

    result = await service.summarize_book(book.id)

    assert result == {
        "summary": "Résumé généré",
        "book_title": "cours",
    }
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_summarize_book_rejects_unknown_book(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    client = _SummaryClient()
    service = TutorService(store, client, Config(tmp_path / "config"))

    with pytest.raises(KeyError, match="Livre introuvable"):
        await service.summarize_book("missing-book")
