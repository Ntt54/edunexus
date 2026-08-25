"""Unit tests for the spaced-repetition review ladder (T037, US5 / D8).

Covers research D8:
- ``streak_index`` → interval mapping ``[1, 2, 5, 12, 30]`` (capped at 4);
- failure resets ``streak_index`` to 0;
- ``next_due`` date arithmetic (``today + interval``);
- due-query filter (only items with ``next_due <= today``).

All scheduling is pure SQL / arithmetic — the test asserts ZERO model/LLM
calls (SC-008) by running the service against a ``MagicMock`` client and
verifying no ``chat_stream`` / ``embed`` invocations occur.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.review import (
    LADDER_DAYS,
    ReviewScheduler,
    interval_for,
    next_due_for,
)
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _store(tmp_path: Path) -> LibraryStore:
    return LibraryStore(tmp_path / "tutor_store")


def _make_card(store: LibraryStore, subject_id: str, concept_id: str, fid: str, hash_: str) -> None:
    store._conn.execute(
        "INSERT INTO flashcards "
        "(id, subject_id, concept_id, level, question, answer, source_hash, created_at) "
        "VALUES (?, ?, ?, 'beginner', 'q', 'a', ?, 'now')",
        (fid, subject_id, concept_id, hash_),
    )
    store._conn.commit()


def test_ladder_interval_mapping() -> None:
    """streak_index maps onto the D8 ladder, clamped to the last rung."""
    assert interval_for(0) == 1
    assert interval_for(1) == 2
    assert interval_for(2) == 5
    assert interval_for(3) == 12
    assert interval_for(4) == 30
    # out-of-range indices are clamped, never raise
    assert interval_for(5) == 30
    assert interval_for(99) == 30
    assert interval_for(-3) == 1
    assert LADDER_DAYS == [1, 2, 5, 12, 30]


def test_next_due_arithmetic() -> None:
    """next_due = today + ladder[streak_index] (ISO date)."""
    base = date(2026, 1, 1)
    assert next_due_for(0, base) == "2026-01-02"   # +1
    assert next_due_for(1, base) == "2026-01-03"   # +2
    assert next_due_for(2, base) == "2026-01-06"   # +5
    assert next_due_for(3, base) == "2026-01-13"   # +12
    assert next_due_for(4, base) == "2026-01-31"   # +30


def test_grade_walks_ladder(tmp_path: Path) -> None:
    """Success advances the ladder; failure resets streak_index to 0."""
    store = _store(tmp_path)
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "Fractions")
    sched = ReviewScheduler(store)
    fid = "fc1"
    _make_card(store, subj.id, concept.id, fid, "h1")
    sched.seed_schedule(fid, date(2026, 1, 1))

    r = sched.grade_review(fid, True, date(2026, 1, 1))
    assert r["streak_index"] == 1
    assert r["next_due"] == "2026-01-03"            # +2

    r = sched.grade_review(fid, True, date(2026, 1, 1))
    assert r["streak_index"] == 2
    assert r["next_due"] == "2026-01-06"            # +5

    r = sched.grade_review(fid, False, date(2026, 1, 1))
    assert r["streak_index"] == 0                   # reset
    assert r["next_due"] == "2026-01-02"            # +1


def test_due_query_filter(tmp_path: Path) -> None:
    """due_reviews returns only cards whose next_due <= today (pure SQL)."""
    store = _store(tmp_path)
    subj = store.create_subject("Hist")
    concept = store.upsert_concept(subj.id, "C1")
    sched = ReviewScheduler(store)
    today = date.today()

    fid_a = "a"
    _make_card(store, subj.id, concept.id, fid_a, "ha")
    sched.seed_schedule(fid_a, today)              # due today

    fid_b = "b"
    _make_card(store, subj.id, concept.id, fid_b, "hb")
    sched.seed_schedule(fid_b, today)
    # push card B 10 days out → must NOT be due
    store.upsert_review_schedule(fid_b, 0, (today + timedelta(days=10)).isoformat(), None)

    due = sched.due_reviews(subj.id, today)
    ids = {c.id for c in due}
    assert fid_a in ids
    assert fid_b not in ids


def test_zero_model_calls(tmp_path: Path) -> None:
    """Scheduling performs zero LLM/embed calls (SC-008)."""
    store = _store(tmp_path)
    subj = store.create_subject("Phys")
    concept = store.upsert_concept(subj.id, "C")
    client = MagicMock()
    service = TutorService(store, client, Config(tmp_path / "cfg"))

    fid = "z"
    _make_card(store, subj.id, concept.id, fid, "hz")
    service.review.seed_schedule(fid)

    # Both operations must be pure SQL — no model involvement.
    service.due_reviews(subj.id)
    service.grade_review(fid, True)

    assert client.chat_stream.call_count == 0
    assert client.embed.call_count == 0
