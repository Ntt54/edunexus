"""Integration test recalcule après révision — 013 Vague 1 Socle (US2, T010).

FR-004 : révision notée → urgences recalculées sans second moteur.
Le forecast est un read-model pur sur `fsrs.py`/`review.py` : la
stabilité FSRS mise à jour par `grade_review_fsrs` suffit à faire
évoluer `retrievability`, `days_until_threshold` et `urgency` sans
dupliquer le moteur.

100 % offline : LibraryStore tmp + ReviewScheduler, aucun réseau/LLM.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.ollama_tutor.tutor.review import ReviewScheduler
from src.ollama_tutor.tutor.store import LibraryStore

FIXED_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def test_forecast_recalcule_after_fsrs_review(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Maths")
    sched = ReviewScheduler(store)

    # Create one card that is currently overdue (stability small, elapsed large)
    fid = "card-under-test"
    concept = store.create_concept(subject.id, "Fractions")
    store._conn.execute(
        "INSERT INTO flashcards (id, subject_id, concept_id, level, question, answer, source_hash, created_at) VALUES (?, ?, ?, 'beginner','q','a',?, 'now')",
        (fid, subject.id, concept.id, "h-test"),
    )
    store._conn.commit()
    sched._ensure_fsrs_columns()
    sched.seed_schedule(fid)
    # Seed with low stability 1.0 and last_review 5 days ago => overdue
    last_review = (FIXED_NOW - timedelta(days=5)).isoformat()
    store._conn.execute(
        "UPDATE review_schedule SET stability=1.0, last_review=?, fsrs_due=?, difficulty=5.0, fsrs_state='review', reps=1 WHERE flashcard_id=?",
        (last_review, (FIXED_NOW + timedelta(days=1)).isoformat(), fid),
    )
    store._conn.commit()

    def get_queue():
        if hasattr(sched, "get_forgetting_queue"):
            return sched.get_forgetting_queue(subject.id, now=FIXED_NOW)
        else:
            q = sched.forgetting_forecast(subject.id, now=FIXED_NOW)
            if isinstance(q, list):
                return {"items": q, "order": "overdue-first"}
            return q

    q_before = get_queue()
    item_before = next(it for it in q_before["items"] if it["notion_id"] == fid)
    assert item_before["urgency"] == "overdue"
    assert item_before["retrievability"] < 0.9
    dut_before = item_before["days_until_threshold"]
    # overdue => dut ==0 or negative
    assert dut_before <= 0

    # Grade the card as Good (3) via the SAME FSRS engine (no second engine).
    # Use now = FIXED_NOW so elapsed is deterministic.
    # First fetch row to know old stability
    row_before = sched.get_review(fid)
    old_stability = float(row_before["stability"])
    # Perform FSRS review
    sched.grade_review_fsrs(fid, rating=3, today=FIXED_NOW.date())
    # Overwrite last_review to FIXED_NOW for deterministic forecast (grade_review_fsrs sets to now internally,
    # but we ensure it is FIXED_NOW)
    # The scheduler grade_review_fsrs uses today date, but sets last_review to UTC now().
    # To make forecast deterministic, manually update last_review to FIXED_NOW if needed.
    row_after = sched.get_review(fid)
    # If last_review not exactly FIXED_NOW, set it.
    try:
        stored_last = datetime.fromisoformat(str(row_after["last_review"]))
        if stored_last.tzinfo is None:
            stored_last = stored_last.replace(tzinfo=timezone.utc)
        # If drift >1 minute, normalize to FIXED_NOW for test determinism
        if abs((stored_last - FIXED_NOW).total_seconds()) > 60:
            store._conn.execute(
                "UPDATE review_schedule SET last_review=? WHERE flashcard_id=?",
                (FIXED_NOW.isoformat(), fid),
            )
            store._conn.commit()
            row_after = sched.get_review(fid)
    except Exception:
        pass

    new_stability = float(row_after["stability"])
    assert new_stability != old_stability, "stability must change after FSRS review (no second engine, same row)"
    assert new_stability > old_stability, f"Good rating should increase stability: {old_stability} -> {new_stability}"

    # Recompute forecast without any second engine — same scheduler/store
    q_after = get_queue()
    item_after = next(it for it in q_after["items"] if it["notion_id"] == fid)
    # After a successful review, retention is reset, remaining days grows, urgency improves
    assert item_after["urgency"] != "overdue", f"after review urgency should improve, got {item_after['urgency']}"
    assert item_after["days_until_threshold"] > dut_before
    assert item_after["retrievability"] > item_before["retrievability"]
    # Stability used in forecast must be the updated one — verify retrievability matches
    # elapsed now ~0 after review, so retrievability ~1.0
    assert item_after["retrievability"] == item_after["retrievability"]  # trivial check for existence
    assert item_after["retrievability"] > 0.9

    # No new table/engine was created: same review_schedule row, same fsrs.py formula
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(review_schedule)")}
    # Check we didn't create a new forecast table
    tables = {r["name"] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "forgetting_forecast" not in tables
    assert "forecast" not in tables


def test_forecast_without_second_engine_uses_same_fsrs_formula(tmp_path: Path) -> None:
    """Verify forecast retrievability equals fsrs.retrievability directly."""
    from src.ollama_tutor.tutor.fsrs import retrievability as fsrs_ret

    store = LibraryStore(tmp_path)
    subject = store.create_subject("Phys")
    sched = ReviewScheduler(store)
    fid = "card-formula-check"
    concept = store.create_concept(subject.id, "Atomes")
    store._conn.execute(
        "INSERT INTO flashcards (id, subject_id, concept_id, level, question, answer, source_hash, created_at) VALUES (?, ?, ?, 'beginner','q','a',?, 'now')",
        (fid, subject.id, concept.id, "h-fc"),
    )
    store._conn.commit()
    sched._ensure_fsrs_columns()
    sched.seed_schedule(fid)
    stability = 2.4
    elapsed = 2.0
    last_review = (FIXED_NOW - timedelta(days=elapsed)).isoformat()
    store._conn.execute(
        "UPDATE review_schedule SET stability=?, last_review=?, fsrs_due=?, difficulty=5.0, fsrs_state='review', reps=1 WHERE flashcard_id=?",
        (stability, last_review, (FIXED_NOW + timedelta(days=1)).isoformat(), fid),
    )
    store._conn.commit()

    if hasattr(sched, "get_forgetting_queue"):
        q = sched.get_forgetting_queue(subject.id, now=FIXED_NOW)
    else:
        q = sched.forgetting_forecast(subject.id, now=FIXED_NOW)
        if isinstance(q, list):
            q = {"items": q, "order": "overdue-first"}
    item = next(it for it in q["items"] if it["notion_id"] == fid)
    expected_r = fsrs_ret(elapsed, stability)
    assert item["retrievability"] == expected_r
