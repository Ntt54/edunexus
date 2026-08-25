"""Unit tests for session memory & resume briefing (T043-T044, US6 / D9).

Covers research D9:
- ``close_session`` aggregates a session into a persisted ``SessionSummary``
  (studied / mastered / to_review) and closes the row (FR-028);
- ``resume_briefing`` assembles a ``ResumeBriefing`` from the last summary +
  open gaps with ZERO model/LLM calls (FR-029).

Both are pure data assembly — the tests assert no ``chat_stream`` / ``embed``
invocations occur.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from src.ollama_tutor.config import Config
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


def _seed_review(store: LibraryStore, fid: str, last_result: str) -> None:
    store.upsert_review_schedule(fid, 1, date.today().isoformat(), last_result)


def test_close_session_persists_summary(tmp_path: Path) -> None:
    """close_session persists studied/mastered/to_review and closes the row."""
    store = _store(tmp_path)
    subj = store.create_subject("Math")
    a = store.upsert_concept(subj.id, "Fractions")
    b = store.upsert_concept(subj.id, "Decimals")
    store.record_progress(a.id, 80.0)   # mastered
    store.record_progress(b.id, 30.0)   # to_review

    _make_card(store, subj.id, a.id, "fc_a", "ha")
    _make_card(store, subj.id, b.id, "fc_b", "hb")
    _seed_review(store, "fc_a", "success")
    _seed_review(store, "fc_b", "failure")

    session = store.create_tutoring_session(subj.id)
    client = MagicMock()
    service = TutorService(store, client, Config(tmp_path / "cfg"))

    summary = service.close_session(session.id)

    # Persisted and retrievable.
    saved = store.get_session_summary(session.id)
    assert saved is not None
    assert set(saved.concepts_studied) == {"Fractions", "Decimals"}
    assert saved.concepts_mastered == ["Fractions"]
    assert "Decimals" in saved.to_review

    # Row closed.
    reopened = store.get_tutoring_session(session.id)
    assert reopened is not None
    assert reopened.status == "closed"

    # No model calls during aggregation.
    assert client.chat_stream.call_count == 0
    assert client.embed.call_count == 0


def test_resume_briefing_zero_model_calls(tmp_path: Path) -> None:
    """resume_briefing assembles a briefing with ZERO LLM calls (FR-029)."""
    store = _store(tmp_path)
    subj = store.create_subject("Math")
    c = store.upsert_concept(subj.id, "Fractions")
    store.record_progress(c.id, 80.0)
    store.set_gap_flag(c.id, True)   # open gap → difficulty

    _make_card(store, subj.id, c.id, "fc1", "h1")
    _seed_review(store, "fc1", "success")

    session = store.create_tutoring_session(subj.id)
    client = MagicMock()
    service = TutorService(store, client, Config(tmp_path / "cfg"))

    service.close_session(session.id)          # produce a summary
    client.chat_stream.reset_mock()

    briefing = service.resume_briefing(subj.id)

    assert briefing.last_topic == "Fractions"
    assert "Fractions" in briefing.difficulties
    assert briefing.proposal.startswith("Réviser en priorité")
    # Pure data assembly — no model involvement.
    assert client.chat_stream.call_count == 0
    assert client.embed.call_count == 0


def test_resume_briefing_without_history(tmp_path: Path) -> None:
    """Without any prior summary, briefing falls back to the subject itself."""
    store = _store(tmp_path)
    subj = store.create_subject("Physique")
    client = MagicMock()
    service = TutorService(store, client, Config(tmp_path / "cfg"))

    client.chat_stream.reset_mock()
    briefing = service.resume_briefing(subj.id)

    assert briefing.last_topic == "Physique"
    assert briefing.proposal.startswith("Commencer l'apprentissage")
    assert client.chat_stream.call_count == 0
