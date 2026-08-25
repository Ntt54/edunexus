"""Mastery tracking, derived labels, gap detection, learning-path reorder.

Implements research D7 for User Story 4 (practice). Pure, UI-framework-free
logic operating over :class:`LibraryStore` — no textual/fastapi imports
(contract invariant 1).

D7 weights (research D7 + US4 extensions):
- ``correct`` / ``incorrect`` follow research D7 "exercise success ±12";
- ``partial`` grants half credit (+6);
- ``hint_used`` applies a small penalty (−4) when a hint is revealed.

Label thresholds follow the task T030 specification:
``non étudié`` < 20, ``faible`` < 50, ``moyen`` < 80, ``maîtrisé`` ≥ 80.
(Note: research.md / data-model.md state <40/<70/≥70; the task's explicit
T030 thresholds are used here — see report deviation note.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Concept
from .store import LibraryStore

# D7 mastery weights per event type (research D7 + US4 extensions).
D7_WEIGHTS: dict[str, float] = {
    "correct": 12.0,
    "incorrect": -12.0,
    "partial": 6.0,
    "hint_used": -4.0,
}

# Label thresholds (task T030): no row / score < 20 ⇒ non étudié,
# < 50 ⇒ faible, < 80 ⇒ moyen, ≥ 80 ⇒ maîtrisé.
_LABEL_NON_ETUDIE = 20
_LABEL_FAIBLE = 50
_LABEL_MOYEN = 80


def label_for(score: float | None) -> str:
    """Derive the mastery label from a 0–100 score (or absence of one)."""
    if score is None:
        return "non étudié"
    if score < _LABEL_NON_ETUDIE:
        return "non étudié"
    if score < _LABEL_FAIBLE:
        return "faible"
    if score < _LABEL_MOYEN:
        return "moyen"
    return "maîtrisé"


@dataclass
class ProgressRow:
    """One concept's progress with its derived label and path position."""

    concept: Concept
    score: float | None
    label: str
    path_rank: int | None


@dataclass
class GapRow:
    """A concept flagged as a gap, with its trailing failure count."""

    concept: Concept
    score: float | None
    recent_failures: int


def _incorrect_runs(attempts: list[Any]) -> tuple[int, int]:
    """Return ``(max_run, trailing_run)`` of consecutive ``incorrect`` verdicts.

    ``max_run`` is the longest run of consecutive incorrect attempts anywhere in
    the history; ``trailing_run`` is the number of incorrect attempts at the end
    (broken by any non-incorrect verdict). Used for gap detection (FR-022).
    """
    max_run = 0
    cur = 0
    for a in attempts:
        if getattr(a, "verdict", None) == "incorrect":
            cur += 1
            if cur > max_run:
                max_run = cur
        else:
            cur = 0
    trailing = 0
    for a in reversed(attempts):
        if getattr(a, "verdict", None) == "incorrect":
            trailing += 1
        else:
            break
    return max_run, trailing


class ProgressTracker:
    """Mastery / gap / path operations over a :class:`LibraryStore`."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Event recording (D7)
    # ------------------------------------------------------------------

    def record_event(self, concept_id: str, event_type: str) -> float:
        """Apply a D7-weighted delta to a concept's mastery.

        Delegates the clamped 0–100 arithmetic to
        ``LibraryStore.record_progress`` and returns the resulting score.
        """
        if event_type not in D7_WEIGHTS:
            raise ValueError(f"Unknown mastery event type: {event_type!r}")
        delta = D7_WEIGHTS[event_type]
        self.store.record_progress(concept_id, delta)
        score = self.store.get_concept_score(concept_id)
        return float(score) if score is not None else 0.0

    # ------------------------------------------------------------------
    # Progress read (with derived labels)
    # ------------------------------------------------------------------

    def get_progress(self, subject_id: str) -> list[ProgressRow]:
        """Return every concept's progress with its derived label + path rank."""
        rows = self.store.get_progress(subject_id)
        out: list[ProgressRow] = []
        for concept, score in rows:
            out.append(
                ProgressRow(
                    concept=concept,
                    score=score,
                    label=label_for(score),
                    path_rank=concept.path_rank,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Gap detection (FR-022): 3 consecutive incorrect ⇒ flagged
    # ------------------------------------------------------------------

    def get_gaps(self, subject_id: str) -> list[GapRow]:
        """Return concepts that are gaps for the subject.

        A concept is a gap when it carries a stored ``gap_flag`` OR has a run of
        ≥3 consecutive ``incorrect`` attempts (research D7 gap rule). Each row
        reports the trailing failure count for UI prioritization.
        """
        concepts = self.store.list_concepts(subject_id)
        gaps: list[GapRow] = []
        for c in concepts:
            attempts = self.store.list_attempts_by_concept(c.id)
            max_run, trailing = _incorrect_runs(attempts)
            flagged = max_run >= 3 or self.store.get_gap_flag(c.id)
            if flagged:
                gaps.append(
                    GapRow(
                        concept=c,
                        score=self.store.get_concept_score(c.id),
                        recent_failures=trailing,
                    )
                )
        return gaps

    def refresh_gap_flags(self, subject_id: str) -> None:
        """Recompute and persist ``gap_flag`` for every concept (idempotent)."""
        for c in self.store.list_concepts(subject_id):
            attempts = self.store.list_attempts_by_concept(c.id)
            max_run, _ = _incorrect_runs(attempts)
            self.store.set_gap_flag(c.id, max_run >= 3)

    # ------------------------------------------------------------------
    # Learning-path reorder (FR-021/022/023)
    # ------------------------------------------------------------------

    def reorder_path(self, subject_id: str, order: list[str]) -> list[Concept]:
        """Reorder the learning path: ``order`` is a list of concept ids.

        Each concept in ``order`` (that belongs to the subject) receives
        ``path_rank`` equal to its index. Concepts not listed keep their
        existing rank. Returns the concepts ordered by the new path.
        """
        for idx, cid in enumerate(order):
            concept = self.store.get_concept(cid)
            if concept is not None and concept.subject_id == subject_id:
                self.store.set_concept_path_rank(cid, idx)
        return self.store.list_concepts(subject_id)
