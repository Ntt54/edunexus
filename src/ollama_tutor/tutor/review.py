"""Spaced-repetition ladder scheduler (research D8, US5 / T038).

Implements the fixed expanding ladder ``[1, 2, 5, 12, 30]`` days with pure
SQLite queries — NO LLM calls (SC-008: instant, zero model calls). The ladder
index equals the count of consecutive successes (capped at 4); any failure
resets it to 0. ``due_reviews`` is a single ``next_due <= today`` SQL filter.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .models import Flashcard

# D8 spaced-repetition ladder (days). Index = consecutive-success count (0-4).
LADDER_DAYS = [1, 2, 5, 12, 30]
_MAX_INDEX = len(LADDER_DAYS) - 1  # 4


def interval_for_streak(streak_index: int) -> int:
    """Return the due offset (days) for a streak index (clamped to 0-4)."""
    idx = max(0, min(_MAX_INDEX, int(streak_index)))
    return LADDER_DAYS[idx]


# Backwards-compatible alias (some callers used ``interval_for``).
interval_for = interval_for_streak


def next_due_for(streak_index: int, today: date | None = None) -> str:
    """ISO date for ``today + interval_for_streak(streak_index)``."""
    today = today or date.today()
    return (today + timedelta(days=interval_for_streak(streak_index))).isoformat()


class ReviewScheduler:
    """Ladder-based due-review scheduler over a :class:`LibraryStore`."""

    def __init__(self, store: Any) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Pure-SQL due listing (SC-008: zero LLM calls)
    # ------------------------------------------------------------------

    def due_reviews(self, subject_id: str, today: date | None = None) -> list[Flashcard]:
        """Return flashcards in ``subject_id`` whose ``next_due <= today``.

        Pure SQL — no model/LLM involvement. Ordered by soonest due first.
        """
        today_iso = (today or date.today()).isoformat()
        rows = self.store._conn.execute(
            "SELECT f.* FROM flashcards f "
            "JOIN review_schedule rs ON rs.flashcard_id = f.id "
            "WHERE f.subject_id = ? AND rs.next_due <= ? "
            "ORDER BY rs.next_due ASC",
            (subject_id, today_iso),
        ).fetchall()
        return [Flashcard.from_dict(dict(r)) for r in rows]

    def get_review(self, flashcard_id: str) -> dict[str, Any] | None:
        """Return the ``review_schedule`` row for a flashcard, or ``None``."""
        row = self.store._conn.execute(
            "SELECT * FROM review_schedule WHERE flashcard_id = ?", (flashcard_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def seed_schedule(
        self, flashcard_id: str, today: date | None = None
    ) -> dict[str, Any]:
        """Create an initial ``review_schedule`` row (streak 0, due today).

        Idempotent: if a row already exists it is returned unchanged.
        """
        existing = self.get_review(flashcard_id)
        if existing is not None:
            return existing
        today = today or date.today()
        # New cards are due immediately (interval 0 ⇒ today) so they surface in
        # the first due list; the ladder begins on the first successful grade.
        next_due = today.isoformat()
        self.store._conn.execute(
            "INSERT INTO review_schedule "
            "(flashcard_id, streak_index, next_due, last_result) "
            "VALUES (?, 0, ?, NULL)",
            (flashcard_id, next_due),
        )
        self.store._conn.commit()
        return {
            "flashcard_id": flashcard_id,
            "streak_index": 0,
            "next_due": next_due,
            "last_result": None,
        }

    # ------------------------------------------------------------------
    # Grading (D8 ladder walk)
    # ------------------------------------------------------------------

    def grade_review(
        self, flashcard_id: str, success: bool, today: date | None = None
    ) -> dict[str, Any]:
        """Apply the D8 ladder to a flashcard's review schedule.

        On success the streak index advances one rung (capped at 4) and the
        next due date is ``today + ladder[index]``. On failure the streak index
        resets to 0 and the next due date is ``today + ladder[0]`` (1 day).

        Returns ``{flashcard_id, streak_index, next_due}`` so the REST layer can
        echo the new schedule without a second query.
        """
        row = self.get_review(flashcard_id)
        if row is None:
            # Auto-seed so grading a freshly prepared card always works.
            row = self.seed_schedule(flashcard_id, today)
        current = int(row["streak_index"])
        if success:
            new_index = min(current + 1, _MAX_INDEX)
        else:
            new_index = 0
        next_due = next_due_for(new_index, today)
        last_result = "success" if success else "failure"
        self.store._conn.execute(
            "UPDATE review_schedule SET streak_index = ?, next_due = ?, "
            "last_result = ? WHERE flashcard_id = ?",
            (new_index, next_due, last_result, flashcard_id),
        )
        self.store._conn.commit()
        return {
            "flashcard_id": flashcard_id,
            "streak_index": new_index,
            "next_due": next_due,
        }


__all__ = [
    "LADDER_DAYS",
    "MAX_STREAK_INDEX",
    "interval_for_streak",
    "interval_for",
    "next_due_for",
    "ReviewScheduler",
]

# Keep the constant name some callers may expect.
MAX_STREAK_INDEX = _MAX_INDEX
