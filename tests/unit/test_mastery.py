"""Unit tests for mastery tracking (T030, US4).

D7 weight application per event type, clamping 0–100, label thresholds
(non étudié<20 / faible<50 / moyen<80 / maîtrisé≥80), gap detection rule
(3 consecutive incorrect ⇒ flagged), and path_rank reordering.

Offline: pure logic over ``LibraryStore`` with a ``tmp_path`` config dir. No
LLM, no network, no daemon.
"""

from __future__ import annotations

from pathlib import Path

from src.ollama_tutor.tutor.models import Exercise, ExerciseAttempt
from src.ollama_tutor.tutor.progress import D7_WEIGHTS, ProgressTracker, label_for
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# Label thresholds (task T030)
# ---------------------------------------------------------------------------

def test_label_none() -> None:
    assert label_for(None) == "non étudié"


def test_label_thresholds() -> None:
    assert label_for(0) == "non étudié"
    assert label_for(19) == "non étudié"
    assert label_for(20) == "faible"
    assert label_for(49) == "faible"
    assert label_for(50) == "moyen"
    assert label_for(79) == "moyen"
    assert label_for(80) == "maîtrisé"
    assert label_for(100) == "maîtrisé"


# ---------------------------------------------------------------------------
# D7 weights
# ---------------------------------------------------------------------------

def test_d7_weights() -> None:
    assert D7_WEIGHTS == {
        "correct": 12.0,
        "incorrect": -12.0,
        "partial": 6.0,
        "hint_used": -4.0,
    }


# ---------------------------------------------------------------------------
# Event recording + clamping
# ---------------------------------------------------------------------------

def test_record_event_applies_weight(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "Fractions")
    tracker = ProgressTracker(store)
    score = tracker.record_event(concept.id, "correct")
    assert score == 12.0
    score = tracker.record_event(concept.id, "incorrect")
    assert score == 0.0  # 12 - 12


def test_record_event_partial_and_hint(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "X")
    tracker = ProgressTracker(store)
    tracker.record_event(concept.id, "partial")
    assert store.get_concept_score(concept.id) == 6.0
    tracker.record_event(concept.id, "hint_used")
    assert store.get_concept_score(concept.id) == 2.0  # 6 - 4


def test_clamping_low_and_high(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "X")
    tracker = ProgressTracker(store)
    for _ in range(20):
        tracker.record_event(concept.id, "incorrect")
    assert store.get_concept_score(concept.id) == 0.0  # clamped
    for _ in range(20):
        tracker.record_event(concept.id, "correct")
    assert store.get_concept_score(concept.id) == 100.0  # clamped


def test_record_event_unknown_type(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "X")
    tracker = ProgressTracker(store)
    try:
        tracker.record_event(concept.id, "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Gap detection (3 consecutive incorrect ⇒ flagged)
# ---------------------------------------------------------------------------

def _make_exercise(store: LibraryStore, subj, concept) -> Exercise:
    ex = Exercise(
        id=f"e_{concept.id}",
        subject_id=subj.id,
        concept_id=concept.id,
        difficulty="easy",
        statement="s",
        solution="sol",
        hints=["h1", "h2", "h3"],
    )
    store.add_exercise(ex)
    return ex


def test_gap_detection_three_consecutive(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "X")
    tracker = ProgressTracker(store)
    ex = _make_exercise(store, subj, concept)
    for i in range(3):
        store.add_attempt(
            ExerciseAttempt(id=f"a{i}", exercise_id=ex.id, verdict="incorrect", answer="x")
        )
    gaps = tracker.get_gaps(subj.id)
    assert len(gaps) == 1
    assert gaps[0].concept.id == concept.id
    assert gaps[0].recent_failures == 3


def test_gap_not_flagged_when_correct_breaks_run(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "X")
    tracker = ProgressTracker(store)
    ex = _make_exercise(store, subj, concept)
    store.add_attempt(ExerciseAttempt(id="a1", exercise_id=ex.id, verdict="incorrect", answer="x"))
    store.add_attempt(ExerciseAttempt(id="a2", exercise_id=ex.id, verdict="correct", answer="x"))
    store.add_attempt(ExerciseAttempt(id="a3", exercise_id=ex.id, verdict="incorrect", answer="x"))
    gaps = tracker.get_gaps(subj.id)
    assert gaps == []  # no run of 3 consecutive incorrect


def test_gap_flag_stored_and_detected(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    concept = store.upsert_concept(subj.id, "X")
    tracker = ProgressTracker(store)
    # No attempts, but a stored gap_flag (on the progress row) ⇒ still a gap.
    store.record_progress(concept.id, 0.0)  # ensure a progress row exists
    store.set_gap_flag(concept.id, True)
    gaps = tracker.get_gaps(subj.id)
    assert len(gaps) == 1
    assert gaps[0].concept.id == concept.id


# ---------------------------------------------------------------------------
# Learning-path reorder (FR-021/022/023)
# ---------------------------------------------------------------------------

def test_path_reorder(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    c1 = store.upsert_concept(subj.id, "A")
    c2 = store.upsert_concept(subj.id, "B")
    c3 = store.upsert_concept(subj.id, "C")
    tracker = ProgressTracker(store)
    tracker.reorder_path(subj.id, [c3.id, c1.id, c2.id])
    concepts = store.list_concepts(subj.id)
    order = [(c.id, c.path_rank) for c in concepts]
    assert order == [(c3.id, 0), (c1.id, 1), (c2.id, 2)]


def test_path_reorder_ignores_other_subjects(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj1 = store.create_subject("Math")
    subj2 = store.create_subject("Phys")
    c1 = store.upsert_concept(subj1.id, "A")
    c2 = store.upsert_concept(subj2.id, "B")
    tracker = ProgressTracker(store)
    # Reordering subj1 with a concept from subj2 must not move subj2's concept.
    tracker.reorder_path(subj1.id, [c1.id, c2.id])
    assert store.get_concept(c1.id).path_rank == 0
    assert store.get_concept(c2.id).path_rank is None


def test_get_progress_returns_labels(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "cfg")
    subj = store.create_subject("Math")
    c1 = store.upsert_concept(subj.id, "A")
    c2 = store.upsert_concept(subj.id, "B")
    tracker = ProgressTracker(store)
    tracker.record_event(c1.id, "correct")   # 12 → non étudié (<20)
    tracker.record_event(c1.id, "correct")   # 24 → faible (<50)
    rows = tracker.get_progress(subj.id)
    by_name = {r.concept.name: r for r in rows}
    assert by_name["A"].score == 24.0
    assert by_name["A"].label == "faible"
    assert by_name["B"].score is None
    assert by_name["B"].label == "non étudié"
