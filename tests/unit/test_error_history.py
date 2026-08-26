"""Unit tests for error history recording (US11, T066-T067).

Verifies that:
- Quiz incorrect answers are recorded in error_history via the store.
- Exercise errors are recorded in error_history via the store.
- ``get_error_history`` returns the recorded errors correctly.

Offline: pure logic over ``LibraryStore`` with a ``tmp_path`` config dir. No
LLM, no network, no daemon.
"""

from __future__ import annotations

from pathlib import Path

from src.ollama_tutor.tutor.store import LibraryStore


def _make_store(tmp_path: Path) -> LibraryStore:
    """Create a LibraryStore with a subject and concept for testing."""
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "Fractions")
    return store, subj.id, concept


# ---------------------------------------------------------------------------
# T062: quiz incorrect answers are recorded via store
# ---------------------------------------------------------------------------

def test_record_error_quiz(tmp_path: Path) -> None:
    """Recording a quiz error via store persists it and retrieves it."""
    store, subject_id, concept = _make_store(tmp_path)

    store.record_error(
        subject_id=subject_id,
        concept_name=concept.name,
        question_text="Quel est 1/2 + 1/3 ?",
        given_answer="2/5",
        correct_answer="5/6",
        error_type="incorrect",
    )

    errors = store.get_error_history(subject_id)
    assert len(errors) == 1
    e = errors[0]
    assert e["concept_name"] == "Fractions"
    assert e["question_text"] == "Quel est 1/2 + 1/3 ?"
    assert e["given_answer"] == "2/5"
    assert e["correct_answer"] == "5/6"
    assert e["error_type"] == "incorrect"
    assert e["subject_id"] == subject_id


# ---------------------------------------------------------------------------
# T063: exercise errors are recorded via store
# ---------------------------------------------------------------------------

def test_record_error_exercise(tmp_path: Path) -> None:
    """Recording an exercise error via store persists it and retrieves it."""
    store, subject_id, concept = _make_store(tmp_path)

    # Simulate an exercise attempt that was incorrect.
    store.record_error(
        subject_id=subject_id,
        concept_name=concept.name,
        question_text="Simplifie 4/8",
        given_answer="1/2",
        correct_answer="1/2",
        error_type="partial",
    )

    errors = store.get_error_history(subject_id, concept_name="Fractions")
    assert len(errors) == 1
    e = errors[0]
    assert e["error_type"] == "partial"
    assert e["question_text"] == "Simplifie 4/8"

    # Different concept should not appear when filtered.
    errors_other = store.get_error_history(subject_id, concept_name="Algebra")
    assert len(errors_other) == 0


# ---------------------------------------------------------------------------
# T064: get_error_history returns recorded errors correctly
# ---------------------------------------------------------------------------

def test_get_error_history_returns_data(tmp_path: Path) -> None:
    """get_error_history returns multiple errors ordered by recency."""
    store, subject_id, concept = _make_store(tmp_path)

    # Record several errors.
    store.record_error(subject_id, concept.name, "Q1", "A1", "C1", error_type="incorrect")
    store.record_error(subject_id, concept.name, "Q2", "A2", "C2", error_type="partial")
    store.record_error(subject_id, concept.name, "Q3", "A3", "C3", error_type="incorrect")

    # All returned by default.
    errors = store.get_error_history(subject_id)
    assert len(errors) == 3

    # Most recent first (ORDER BY created_at DESC).
    assert errors[0]["question_text"] == "Q3"
    assert errors[1]["question_text"] == "Q2"
    assert errors[2]["question_text"] == "Q1"

    # Limit works.
    errors_limited = store.get_error_history(subject_id, limit=2)
    assert len(errors_limited) == 2

    # Concept filter works.
    errors_concept = store.get_error_history(subject_id, concept_name="Algebra")
    assert len(errors_concept) == 0


def test_get_error_history_empty(tmp_path: Path) -> None:
    """get_error_history returns empty list when no errors exist."""
    store, subject_id, _concept = _make_store(tmp_path)
    errors = store.get_error_history(subject_id)
    assert errors == []


def test_record_error_with_source_refs(tmp_path: Path) -> None:
    """Error recording with source_refs persists them as JSON."""
    store, subject_id, concept = _make_store(tmp_path)

    refs = ["book/chapter1", "book/chapter2"]
    store.record_error(
        subject_id, concept.name, "Q1", "A1", "C1",
        source_refs=refs, error_type="incorrect",
    )

    errors = store.get_error_history(subject_id)
    assert len(errors) == 1
    import json
    stored_refs = json.loads(errors[0]["source_refs"])
    assert stored_refs == refs
