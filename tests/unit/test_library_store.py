"""Unit tests for ``LibraryStore`` (T004, Phase 2 foundational).

All tests run offline against a ``tmp_path`` config dir; no network, no daemon.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.store import LibraryStore

EXPECTED_TABLES = {
    "subjects",
    "books",
    "subject_books",
    "chunks",
    "embeddings",
    "concepts",
    "progress",
    "exercises",
    "exercise_attempts",
    "flashcards",
    "review_schedule",
    "quizzes",
    "quiz_questions",
    "quiz_answers",
    "tutoring_sessions",
    "session_summaries",
    "glossary_terms",
    "knowledge_relations",
}


def test_schema_created_on_empty_dir(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    names = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert EXPECTED_TABLES <= names, names - EXPECTED_TABLES
    # WAL mode is enabled
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    store.close()


def test_subject_create_list_rename_delete_cascade(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Math")
    assert subject.name == "Math"

    listed = store.list_subjects()
    assert [s.id for s in listed] == [subject.id]

    # child concept + progress rows
    concept = store.upsert_concept(subject.id, "Derivatives")
    store.record_progress(concept.id, 40.0)
    assert store.list_concepts(subject.id)[0].id == concept.id
    assert store.get_progress(subject.id)[0][1] == 40.0

    # rename
    renamed = store.rename_subject(subject.id, "Mathematics")
    assert renamed.name == "Mathematics"
    assert store.list_subjects()[0].name == "Mathematics"

    # delete cascades child rows
    store.delete_subject(subject.id)
    assert store.list_subjects() == []
    assert store.list_concepts(subject.id) == []
    assert store.get_progress(subject.id) == []
    store.close()


def test_duplicate_subject_name_rejected(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    store.create_subject("Math")
    with pytest.raises(ValueError):
        store.create_subject("math")  # case-insensitive clash
    with pytest.raises(ValueError):
        store.create_subject("  MATH  ")  # whitespace-normalized clash
    store.close()


def test_subject_name_validation(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_subject("")
    with pytest.raises(ValueError):
        store.create_subject("x" * 81)
    store.close()


def test_book_row_defaults(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Physics")
    book = store.import_document(subject.id, _make_tmp_file(tmp_path, "newton.txt"))
    assert book.status == "pending"
    assert book.chunks_done == 0
    assert book.chunks_total == 0
    assert book.error is None
    assert book.format == "txt"
    assert book.fingerprint
    # re-import is a no-op (same fingerprint within subject)
    again = store.import_document(subject.id, _make_tmp_file(tmp_path, "newton.txt"))
    assert again.id == book.id
    assert len(store.list_books(subject.id)) == 1
    store.close()


def test_flashcard_source_hash_unique(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Chem")
    concept = store.upsert_concept(subject.id, "Acids")

    def insert_flashcard() -> None:
        store._conn.execute(
            "INSERT INTO flashcards "
            "(id, subject_id, concept_id, level, question, answer, source_hash, created_at) "
            "VALUES (?, ?, ?, 'beginner', 'q', 'a', 'HASH1', '')",
            ("f1", subject.id, concept.id),
        )
        store._conn.commit()

    insert_flashcard()
    # second insert with same (subject_id, source_hash) must violate UNIQUE
    with pytest.raises(sqlite3.IntegrityError):
        insert_flashcard()
    store.close()


def test_record_progress_clamps(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Bio")
    concept = store.upsert_concept(subject.id, "Cells")

    # starts at 0, delta above 100 clamps to 100
    store.record_progress(concept.id, 150.0)
    assert store.get_progress(subject.id)[0][1] == 100.0

    # negative delta below 0 clamps to 0
    store.record_progress(concept.id, -200.0)
    assert store.get_progress(subject.id)[0][1] == 0.0

    # additive within range
    store.record_progress(concept.id, 30.0)
    store.record_progress(concept.id, 50.0)
    assert store.get_progress(subject.id)[0][1] == 80.0

    # additive that would exceed 100 clamps
    store.record_progress(concept.id, 50.0)
    assert store.get_progress(subject.id)[0][1] == 100.0
    store.close()


def _make_tmp_file(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("Hello world content for embedding.", encoding="utf-8")
    return p
