"""Unit tests for structured chunking (US4 — T027/T028/T029).

Offline: no daemon, no network. Exercises ``chunk_text_structured`` from
``tutor.extractors``.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.extractors import chunk_text_structured


# ── T027 — paragraph-boundary splitting ──────────────────────────────────

def test_chunk_text_structured_paragraphs() -> None:
    """Chunks split on double-newlines (paragraph boundaries)."""
    text = "Premier paragraphe.\n\nDeuxième paragraphe.\n\nTroisième paragraphe."
    chunks = chunk_text_structured(text, max_chars=1200)
    texts = [c["text"] for c in chunks]
    assert "Premier paragraphe." in texts[0]
    assert "Deuxième paragraphe." in texts[1]
    assert "Troisième paragraphe." in texts[2]


def test_chunk_text_structured_long_paragraph_splits_at_sentences() -> None:
    """A paragraph exceeding max_chars is split at sentence boundaries."""
    sentences = ". ".join(f"Phrase numéro {i}." for i in range(80))
    # Each sentence ≈ 20 chars → total ≈ 1600 chars
    text = sentences
    chunks = chunk_text_structured(text, max_chars=400)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= 400 + 60  # generous margin for a single long sentence


# ── T028 — heading detection ─────────────────────────────────────────────

def test_chunk_text_structured_headings() -> None:
    """Headings are detected and used as ``section`` metadata."""
    text = (
        "# Chapitre 1\n\n"
        "Contenu du chapitre 1.\n\n"
        "## Section 1.1\n\n"
        "Contenu de la section 1.1.\n\n"
        "# Chapitre 2\n\n"
        "Contenu du chapitre 2."
    )
    chunks = chunk_text_structured(text, max_chars=1200)
    sections = [c["section"] for c in chunks]
    assert "Chapitre 1" in sections[0]
    assert "Section 1.1" in sections[1]
    assert "Chapitre 2" in sections[2]


def test_chunk_text_structured_metadata_dict() -> None:
    """The returned dicts contain section, page, and text keys."""
    text = "Contenu simple sans en-tête ni page."
    chunks = chunk_text_structured(text, max_chars=1200)
    assert len(chunks) == 1
    entry = chunks[0]
    assert "text" in entry
    assert "section" in entry
    assert "page" in entry
    assert entry["text"] == "Contenu simple sans en-tête ni page."


def test_chunk_text_structured_metadata_override() -> None:
    """Explicit ``metadata`` dict overrides detected section/page."""
    text = "# Titre\n\nCorps du texte."
    chunks = chunk_text_structured(
        text, max_chars=1200, metadata={"section": "Manuel", "page": 42}
    )
    assert chunks[0]["section"] == "Manuel"
    assert chunks[0]["page"] == 42


# ── T029 — page number detection ─────────────────────────────────────────

def test_chunk_text_structured_page_detection() -> None:
    """``--- Page X ---`` markers are parsed into ``page`` metadata."""
    text = (
        "--- Page 5 ---\n\n"
        "Contenu de la page 5.\n\n"
        "--- Page 6 ---\n\n"
        "Contenu de la page 6."
    )
    chunks = chunk_text_structured(text, max_chars=1200)
    pages = [c["page"] for c in chunks]
    assert 5 in pages
    assert 6 in pages


def test_chunk_text_structured_page_plain() -> None:
    """``Page X`` (without dashes) is also detected."""
    text = "Page 12\n\nPremier paragraphe.\n\nPage 13\n\nDeuxième paragraphe."
    chunks = chunk_text_structured(text, max_chars=1200)
    pages = [c["page"] for c in chunks]
    assert 12 in pages
    assert 13 in pages


# ── overlap ───────────────────────────────────────────────────────────────

def test_chunk_text_structured_overlap() -> None:
    """Consecutive chunks share the last ``overlap`` characters."""
    # Build two long paragraphs that together exceed max_chars
    para1 = "Premier paragraphe. " * 40   # ~800 chars
    para2 = "Deuxième paragraphe. " * 40  # ~880 chars
    text = para1 + "\n\n" + para2
    chunks = chunk_text_structured(text, max_chars=600, overlap=200)
    assert len(chunks) >= 2
    # The overlap tail of the first chunk should appear at the start of the next
    for a, b in zip(chunks, chunks[1:]):
        tail = a["text"][-200:]
        assert tail in b["text"], "consecutive chunks should share overlap text"


# ── edge cases ────────────────────────────────────────────────────────────

def test_chunk_text_structured_empty() -> None:
    assert chunk_text_structured("") == []
    assert chunk_text_structured("   \n\n  ") == []


def test_chunk_text_structured_single_paragraph() -> None:
    text = "Un seul paragraphe court."
    chunks = chunk_text_structured(text, max_chars=1200)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Un seul paragraphe court."


# ── validation ────────────────────────────────────────────────────────────

def test_chunk_text_structured_invalid_max_chars() -> None:
    with pytest.raises(ValueError, match="positif"):
        chunk_text_structured("texte", max_chars=0)


def test_chunk_text_structured_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="≥ 0"):
        chunk_text_structured("texte", overlap=-1)
