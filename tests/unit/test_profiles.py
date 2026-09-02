"""Unit tests for pedagogical subject profiles (Feature 008, US1).

Offline: pure logic over ``LibraryStore`` with a ``tmp_path`` config dir.
No LLM, no network, no daemon.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.models import SubjectProfile
from src.ollama_tutor.tutor.profiles import ProfileService
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path):
    s = LibraryStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def svc(store):
    return ProfileService(store)


def test_list_templates_seeds_defaults(svc):
    templates = svc.list_templates()
    names = {t.name for t in templates}
    assert "Programmation" in names
    assert "Mathématiques" in names
    assert "Profil libre" in names
    # Idempotent seeding.
    assert len(svc.list_templates()) == len(templates)


def test_create_from_template_prefills(svc, store):
    subject = store.create_subject("Java")
    profile = svc.create_from_template(subject.id, "programmation")
    assert profile.activities  # prefilled
    assert profile.mastery_criteria  # prefilled proof types
    assert profile.template_id == "programmation"
    # Persisted and restored.
    restored = svc.get_profile(subject.id)
    assert restored is not None
    assert restored.activities == profile.activities


def test_save_profile_requires_domain_and_objective(svc, store):
    subject = store.create_subject("Maths")
    with pytest.raises(ValueError):
        svc.save_profile(SubjectProfile(subject_id=subject.id, domain=""))
    with pytest.raises(ValueError):
        svc.save_profile(SubjectProfile(subject_id=subject.id, domain="Maths", objective=""))


def test_save_profile_round_trip(svc, store):
    subject = store.create_subject("Java")
    profile = SubjectProfile(
        subject_id=subject.id,
        domain="Programmation",
        level="supérieur",
        objective="apprendre Java pour créer des projets",
        activities=["exemples résolus", "mini-projets"],
        mastery_criteria=["écrire", "tester"],
    )
    svc.save_profile(profile)
    restored = svc.get_profile(subject.id)
    assert restored is not None
    assert restored.domain == "Programmation"
    assert restored.activities == ["exemples résolus", "mini-projets"]
    assert restored.mastery_criteria == ["écrire", "tester"]


def test_interpret_goal_project(svc):
    params = svc.interpret_goal("apprendre Java pour créer des projets")
    assert params["approach"] == "project"
    assert params["practice"] == "high"


def test_interpret_goal_exam(svc):
    params = svc.interpret_goal("préparer l'examen de maths")
    assert params["approach"] == "exam"


def test_interpret_goal_remedial(svc):
    params = svc.interpret_goal("remise à niveau en SVT")
    assert params["approach"] == "remedial"
    assert params["progression"] == "foundations_first"


def test_interpret_goal_general(svc):
    params = svc.interpret_goal("comprendre la physique")
    assert params["approach"] == "general"
