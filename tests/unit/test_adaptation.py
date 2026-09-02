"""Unit tests for local adaptation (Feature 008, US4).

Covers window recomputation (FR-016), multi-proof mastery validation
(FR-017), attempt recording with answer/time/hints/source (FR-018) and the
stability portion (FR-019).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.adaptation import (
    MIN_DISTINCT_PROOFS,
    AdaptationService,
)
from src.ollama_tutor.tutor.models import Exercise, SubjectProfile
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def _seed_concept_with_exercises(store: LibraryStore, tmp_path: Path) -> tuple[str, str]:
    """Create a subject, a concept and two exercises on that concept."""
    subject = store.create_subject("Informatique")
    concept = store.upsert_concept(subject.id, "Variables")
    ex1 = store.add_exercise(Exercise(
        id="ex-1", subject_id=subject.id, concept_id=concept.id, difficulty="easy", statement="Q1"))
    store.add_exercise(Exercise(
        id="ex-2", subject_id=subject.id, concept_id=concept.id, difficulty="medium", statement="Q2"))
    return concept.id, ex1.id


def test_record_attempt_stores_fr018_fields(store: LibraryStore, tmp_path: Path):
    _, ex_id = _seed_concept_with_exercises(store, tmp_path)
    svc = AdaptationService(store)
    attempt = svc.record_attempt(
        ex_id, verdict="correct", answer="42", time_ms=1500, hints_used=1, source="rappel",
    )
    assert attempt.time_ms == 1500
    assert attempt.hints_used == 1
    assert attempt.source == "rappel"
    ex = store.get_exercise(ex_id)
    attempts = store.list_attempts_by_concept(ex.concept_id)
    assert any(a.source == "rappel" and a.time_ms == 1500 for a in attempts)


def test_mastery_requires_distinct_proofs(store: LibraryStore, tmp_path: Path):
    concept_id, ex_id = _seed_concept_with_exercises(store, tmp_path)
    svc = AdaptationService(store)
    # One correct proof is not enough.
    svc.record_attempt(ex_id, verdict="correct", source="rappel")
    assert svc.is_mastered(concept_id) is False
    # Same source repeated does not count as distinct.
    svc.record_attempt(ex_id, verdict="correct", source="rappel")
    assert svc.is_mastered(concept_id) is False
    # Different sources accumulate toward mastery.
    svc.record_attempt(ex_id, verdict="correct", source="exercice_guide")
    svc.record_attempt(ex_id, verdict="correct", source="probleme_transfert")
    assert svc.is_mastered(concept_id) is True
    assert svc.mastery_progress(concept_id)["distinct_proofs"] == MIN_DISTINCT_PROOFS


def test_recompute_window_only_touches_window(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    path = store.create_learning_path(subject.id, "Parcours")
    for i in range(5):
        store.add_path_step(path.id, "concept", f"concept-{i}", f"Notion {i}", ordinal=i)
    svc = AdaptationService(store)
    result = svc.recompute_window(subject.id, anchor_step_id=None)
    assert "window" in result
    assert len(result["window"]) <= 3  # WINDOW_SIZE
    assert result["path"]["steps"]


def test_stability_portion_preserved(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    store.set_subject_profile(SubjectProfile(
        subject_id=subject.id, objective="Réussir l'examen",
        mastery_criteria=["répondre sans aide"],
    ))
    path = store.create_learning_path(subject.id, "Parcours")
    store.add_path_step(path.id, "concept", "c1", "Variables", ordinal=0)
    svc = AdaptationService(store)
    portion = svc.stability_portion(subject.id)
    assert portion["objective"] == "Réussir l'examen"
    assert portion["main_notion"] == "Variables"
    assert portion["success_criterion"] == "répondre sans aide"
