"""Contract test forecast order + thresholds — 013 Vague 1 Socle (US2, T009).

FR-004 : urgences par notion (overdue-first, seuils fixes overdue/urgent≤3j/
warning≤7j/ok) en read-model FSRS existant, exposées via
`/adaptation/stability` (pas de route /forecast). Read-model pur sur
`fsrs.py`/`review.py` (math/datetime), aucune table, aucun second moteur.

100 % offline : ReviewScheduler + LibraryStore tmp, FSRS stability fixtures,
aucun appel réseau/LLM. @pytest.mark.asyncio where async (none here).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.fsrs import retrievability
from src.ollama_tutor.tutor.review import ReviewScheduler
from src.ollama_tutor.tutor.store import LibraryStore


# Fixed reference now for deterministic elapsed computation.
FIXED_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

# 6 notions étalées : 2 overdue, 1 urgent, 1 warning, 2 ok
# stability == total_days_to_threshold when threshold 0.9 and decay 1.0
# remaining = stability - elapsed ; urgency = overdue if remaining<=0,
# urgent if <=3, warning if <=7, else ok.
FIXTURES = [
    # (fid, stability, elapsed_days, expected_urgency)
    ("card-overdue-1", 1.0, 5, "overdue"),
    ("card-overdue-2", 2.4, 5, "overdue"),
    ("card-urgent-1", 5.0, 3, "urgent"),   # remaining 2
    ("card-warning-1", 10.0, 5, "warning"), # remaining 5
    ("card-ok-1", 20.0, 1, "ok"),          # remaining 19
    ("card-ok-2", 30.0, 2, "ok"),          # remaining 28
]

EXPECTED_ORDER = [
    "card-overdue-1",
    "card-overdue-2",
    "card-urgent-1",
    "card-warning-1",
    "card-ok-1",
    "card-ok-2",
]


def _seed_store(tmp_path: Path) -> tuple[LibraryStore, str]:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Maths")
    for fid, stability, elapsed, _exp in FIXTURES:
        concept = store.create_concept(subject.id, f"Concept-{fid}")
        store._conn.execute(
            "INSERT INTO flashcards (id, subject_id, concept_id, level, question, answer, source_hash, created_at) "
            "VALUES (?, ?, ?, 'beginner', 'q', 'a', ?, 'now')",
            (fid, subject.id, concept.id, f"h-{fid}"),
        )
        store._conn.commit()
        sched = ReviewScheduler(store)
        sched._ensure_fsrs_columns()
        sched.seed_schedule(fid)
        # Overwrite FSRS columns to deterministic fixtures
        last_review = (FIXED_NOW - timedelta(days=elapsed)).isoformat()
        fsrs_due = (FIXED_NOW + timedelta(days=1)).isoformat()
        store._conn.execute(
            "UPDATE review_schedule SET stability = ?, last_review = ?, fsrs_due = ?, difficulty = 5.0, fsrs_state='review', reps=1 WHERE flashcard_id = ?",
            (stability, last_review, fsrs_due, fid),
        )
        store._conn.commit()
    return store, subject.id


def test_forecast_order_and_thresholds(tmp_path: Path) -> None:
    store, subject_id = _seed_store(tmp_path)
    sched = ReviewScheduler(store)

    # The forecast read-model must be importable from review.py (T011).
    # It reuses fsrs.retrievability (no second engine).
    assert hasattr(sched, "get_forgetting_queue") or hasattr(sched, "forgetting_forecast"), \
        "ReviewScheduler must expose forgetting forecast read-model"

    # Resolve method (support either name, prefer get_forgetting_queue)
    if hasattr(sched, "get_forgetting_queue"):
        queue = sched.get_forgetting_queue(subject_id, now=FIXED_NOW)
    else:
        queue = sched.forgetting_forecast(subject_id, now=FIXED_NOW)
        # normalize to queue dict if list returned
        if isinstance(queue, list):
            queue = {"items": queue, "order": "overdue-first"}

    # Contract: forgetting_queue shape
    assert "items" in queue, "forgetting_queue must contain 'items'"
    assert queue.get("order") == "overdue-first"
    items = queue["items"]
    assert len(items) == 6, f"expected 6 forecast items, got {len(items)}"

    # Verify thresholds per fixture
    by_id = {it["notion_id"]: it for it in items}
    for fid, _stab, _elapsed, expected_urgency in FIXTURES:
        it = by_id.get(fid)
        assert it is not None, f"missing notion {fid}"
        assert it["urgency"] == expected_urgency, f"{fid}: expected {expected_urgency}, got {it['urgency']}"
        # retrievability via fsrs formula must be present and in [0,1]
        assert 0.0 <= it["retrievability"] <= 1.0
        # days_until_threshold must respect urgency thresholds
        dut = it["days_until_threshold"]
        if expected_urgency == "overdue":
            assert dut == 0 or dut <= 0, f"{fid} overdue must have days_until <=0, got {dut}"
        elif expected_urgency == "urgent":
            assert 0 < dut <= 3, f"{fid} urgent must have 0<dut<=3, got {dut}"
        elif expected_urgency == "warning":
            assert 3 < dut <= 7, f"{fid} warning must have 3<dut<=7, got {dut}"
        else:  # ok
            assert dut > 7, f"{fid} ok must have dut>7, got {dut}"
        assert "predicted_drop_date" in it
        # predicted_drop_date is ISO date YYYY-MM-DD
        assert len(it["predicted_drop_date"]) == 10
        assert it["predicted_drop_date"][4] == "-"

    # Verify exact order (overdue-first, then days_until asc)
    order_ids = [it["notion_id"] for it in items]
    assert order_ids == EXPECTED_ORDER, f"order mismatch: got {order_ids}, expected {EXPECTED_ORDER}"

    # Determinism over 20 draws: same order and urgencies each time
    for _ in range(20):
        if hasattr(sched, "get_forgetting_queue"):
            q2 = sched.get_forgetting_queue(subject_id, now=FIXED_NOW)
        else:
            q2 = sched.forgetting_forecast(subject_id, now=FIXED_NOW)
            if isinstance(q2, list):
                q2 = {"items": q2, "order": "overdue-first"}
        ids2 = [it["notion_id"] for it in q2["items"]]
        assert ids2 == EXPECTED_ORDER
        for fid, _s, _e, exp in FIXTURES:
            it2 = next(x for x in q2["items"] if x["notion_id"] == fid)
            assert it2["urgency"] == exp

    # Verify retrievability uses fsrs formula (spot-check)
    for fid, stability, elapsed, _ in FIXTURES:
        expected_r = retrievability(float(elapsed), float(stability))
        actual_r = by_id[fid]["retrievability"]
        assert actual_r == pytest.approx(expected_r, abs=1e-6), f"{fid} retrievability mismatch"


def test_forecast_urgency_thresholds_edges(tmp_path: Path) -> None:
    """Edge values for thresholds: 0, 3, 7 boundaries."""
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Phys")
    sched = ReviewScheduler(store)

    # Helper to seed one card and check urgency
    def urgency_for(stability: float, elapsed: float) -> str:
        fid = f"fid-{stability}-{elapsed}"
        concept = store.create_concept(subject.id, f"C-{fid}")
        store._conn.execute(
            "INSERT INTO flashcards (id, subject_id, concept_id, level, question, answer, source_hash, created_at) VALUES (?, ?, ?, 'beginner','q','a',?, 'now')",
            (fid, subject.id, concept.id, f"h-{fid}"),
        )
        store._conn.commit()
        sched._ensure_fsrs_columns()
        sched.seed_schedule(fid)
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
        return item["urgency"]

    # remaining = stability - elapsed
    # overdue when remaining <=0
    assert urgency_for(5.0, 5.0) == "overdue"   # exactly 0
    assert urgency_for(5.0, 5.1) == "overdue"   # negative
    # urgent ≤3
    assert urgency_for(5.0, 2.0) == "urgent"    # remaining 3
    assert urgency_for(5.0, 2.5) == "urgent"    # remaining 2.5
    assert urgency_for(10.0, 7.0) == "urgent"   # remaining 3
    # warning ≤7
    assert urgency_for(10.0, 3.0) == "warning"  # remaining 7
    assert urgency_for(10.0, 5.0) == "warning"  # remaining 5
    # ok >7
    assert urgency_for(20.0, 5.0) == "ok"       # remaining 15
    assert urgency_for(10.0, 1.0) == "ok"       # remaining 9
