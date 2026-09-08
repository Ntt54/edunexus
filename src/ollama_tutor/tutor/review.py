"""Spaced-repetition scheduler (research D8, US5 / T038 + 010 P2-Adaptatif).

Two scheduling paths share the ``review_schedule`` table and the pure-SQL
``due_reviews`` listing (SC-008: instant, zero model calls) :

- legacy D8 ladder (``grade_review(success: bool)``) — fixed expanding
  ladder ``[1, 2, 5, 12, 30]`` days, unchanged for existing callers ;
- FSRS path (``grade_review_fsrs(flashcard_id, rating 1-4)``) — real
  FSRS-5/6 stability/difficulty via :mod:`tutor.fsrs` (US5 tranche A).

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import fsrs as _fsrs
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

    # FSRS state columns (US5 tranche A). Added lazily by
    # _ensure_fsrs_columns so review.py never needs a store.py migration.
    _FSRS_COLUMNS = (
        ("difficulty", "REAL NOT NULL DEFAULT 5.0"),
        ("stability", "REAL NOT NULL DEFAULT 0.0"),
        ("fsrs_state", "TEXT NOT NULL DEFAULT 'new'"),
        ("reps", "INTEGER NOT NULL DEFAULT 0"),
        ("lapses", "INTEGER NOT NULL DEFAULT 0"),
        ("last_review", "TEXT"),
        ("fsrs_due", "TEXT"),
    )

    def __init__(self, store: Any) -> None:
        self.store = store
        self._fsrs_ready = False

    def _ensure_fsrs_columns(self) -> None:
        """Idempotent PRAGMA backfill of the FSRS state columns."""
        if self._fsrs_ready:
            return
        existing = {
            r["name"]
            for r in self.store._conn.execute("PRAGMA table_info(review_schedule)")
        }
        for name, ddl in self._FSRS_COLUMNS:
            if name not in existing:
                self.store._conn.execute(
                    f"ALTER TABLE review_schedule ADD COLUMN {name} {ddl}"
                )
        self.store._conn.commit()
        self._fsrs_ready = True

    @staticmethod
    def _parse_moment(raw: Any) -> datetime | None:
        if not raw:
            return None
        try:
            moment = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment

    def _card_from_row(self, row: dict[str, Any]) -> _fsrs.FSRSCard:
        """Rebuild an FSRS card from a ``review_schedule`` row (defaults = new)."""
        return _fsrs.FSRSCard(
            difficulty=float(row.get("difficulty") or 5.0),
            stability=float(row.get("stability") or 0.0),
            reps=int(row.get("reps") or 0),
            lapses=int(row.get("lapses") or 0),
            last_review=self._parse_moment(row.get("last_review")),
            due=self._parse_moment(row.get("fsrs_due")),
            state=str(row.get("fsrs_state") or "new"),
        )

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

    # ------------------------------------------------------------------
    # FSRS grading (010 P2-Adaptatif, tranche A)
    # ------------------------------------------------------------------

    def grade_review_fsrs(
        self, flashcard_id: str, rating: int, today: date | None = None
    ) -> dict[str, Any]:
        """Apply a real FSRS-5/6 review (1=Again … 4=Easy) to a flashcard.

        Persists difficulty/stability/state/reps/lapses plus the due
        datetimes; ``next_due`` (date part) keeps feeding the shared
        pure-SQL :meth:`due_reviews` listing. ``last_result`` mirrors the
        legacy vocabulary (``"failure"`` iff rating == 1).

        Returns ``{flashcard_id, next_due, stability, difficulty, state,
        reps, lapses}``.

        Raises:
            ValueError: rating hors 1-4.
        """
        if rating not in (1, 2, 3, 4):
            raise ValueError(f"rating {rating!r} invalide ; attendu 1-4.")
        self._ensure_fsrs_columns()
        row = self.get_review(flashcard_id)
        if row is None:
            row = self.seed_schedule(flashcard_id, today)
            row = self.get_review(flashcard_id)
            assert row is not None  # seeded just above
        card, _log = _fsrs.review_card(self._card_from_row(row), rating)
        assert card.due is not None and card.last_review is not None
        next_due = card.due.date().isoformat()
        last_result = "failure" if rating == 1 else "success"
        self.store._conn.execute(
            "UPDATE review_schedule SET next_due = ?, last_result = ?, "
            "difficulty = ?, stability = ?, fsrs_state = ?, reps = ?, "
            "lapses = ?, last_review = ?, fsrs_due = ? WHERE flashcard_id = ?",
            (
                next_due,
                last_result,
                card.difficulty,
                card.stability,
                card.state,
                card.reps,
                card.lapses,
                card.last_review.isoformat(),
                card.due.isoformat(),
                flashcard_id,
            ),
        )
        self.store._conn.commit()
        return {
            "flashcard_id": flashcard_id,
            "next_due": next_due,
            "stability": card.stability,
            "difficulty": card.difficulty,
            "state": card.state,
            "reps": card.reps,
            "lapses": card.lapses,
        }

    def forgetting_cost(
        self, subject_id: str, now: datetime | None = None
    ) -> float:
        """Expected forgotten cards if nothing is reviewed (≥ 2 = urgent).

        Builds FSRS cards from persisted per-flashcard state; cards never
        graded via FSRS (stability 0) contribute nothing.
        """
        self._ensure_fsrs_columns()
        rows = self.store._conn.execute(
            "SELECT rs.* FROM review_schedule rs "
            "JOIN flashcards f ON f.id = rs.flashcard_id "
            "WHERE f.subject_id = ? AND rs.stability > 0 "
            "AND rs.fsrs_due IS NOT NULL",
            (subject_id,),
        ).fetchall()
        cards = [self._card_from_row(dict(r)) for r in rows]
        return _fsrs.estimate_forgetting_cost(cards, now=now)


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
