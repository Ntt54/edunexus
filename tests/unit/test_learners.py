"""Unit tests for learner profiles (Feature 008, US9).

Covers CRUD + cascade delete (T046) and data isolation across learners
(T047). All data stays local (FR-037/FR-038/FR-039).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.learners import LearnerService
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def test_create_and_list_learners(store: LibraryStore):
    svc = LearnerService(store)
    a = svc.create("Thierry")
    b = svc.create("Awa", avatar="🐱")
    assert a["name"] == "Thierry"
    assert b["avatar"] == "🐱"
    names = [l["name"] for l in svc.list()["learners"]]
    assert names == ["Thierry", "Awa"]


def test_get_learner(store: LibraryStore):
    svc = LearnerService(store)
    created = svc.create("Thierry")
    fetched = svc.get(created["id"])
    assert fetched["id"] == created["id"]
    assert fetched["name"] == "Thierry"


def test_delete_learner_cascades_subjects(store: LibraryStore):
    svc = LearnerService(store)
    learner = svc.create("Thierry")
    # Create a subject scoped to this learner.
    store.create_subject("Maths", learner_id=learner["id"])
    assert len(store.list_subjects(learner_id=learner["id"])) == 1
    svc.delete(learner["id"])
    assert store.get_learner(learner["id"]) is None
    assert len(store.list_subjects(learner_id=learner["id"])) == 0


def test_delete_unknown_learner_raises(store: LibraryStore):
    svc = LearnerService(store)
    with pytest.raises(KeyError):
        svc.delete("nope")


def test_data_isolation_across_learners(store: LibraryStore):
    svc = LearnerService(store)
    t = svc.create("Thierry")
    a = svc.create("Awa")
    store.create_subject("Maths", learner_id=t["id"])
    store.create_subject("Français", learner_id=a["id"])
    # Each learner only sees their own subjects.
    t_subjects = [s["name"] for s in svc.activate(t["id"])["subjects"]]
    a_subjects = [s["name"] for s in svc.activate(a["id"])["subjects"]]
    assert t_subjects == ["Maths"]
    assert a_subjects == ["Français"]


def test_activate_returns_scoped_subjects(store: LibraryStore):
    svc = LearnerService(store)
    t = svc.create("Thierry")
    store.create_subject("Maths", learner_id=t["id"])
    result = svc.activate(t["id"])
    assert result["learner"]["id"] == t["id"]
    assert [s["name"] for s in result["subjects"]] == ["Maths"]
