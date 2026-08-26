"""Unit tests for learning paths CRUD via LibraryStore (Feature 006).

Tests all learning-path and path-step operations, subject domain, and
cascade-delete behaviour — offline against tmp_path.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path):
    s = LibraryStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def subject(store):
    return store.create_subject("Test")


# ------------------------------------------------------------------
# Learning Path CRUD
# ------------------------------------------------------------------


class TestLearningPaths:
    def test_create_path(self, store, subject):
        path = store.create_learning_path(subject.id, "Mon Parcours")
        assert path.title == "Mon Parcours"
        assert path.subject_id == subject.id
        assert path.status == "draft"

    def test_create_path_with_description(self, store, subject):
        path = store.create_learning_path(subject.id, "P", description="A description")
        assert path.description == "A description"

    def test_get_path(self, store, subject):
        path = store.create_learning_path(subject.id, "Mon Parcours")
        got = store.get_learning_path(path.id)
        assert got is not None
        assert got.title == "Mon Parcours"

    def test_get_path_unknown_returns_none(self, store):
        assert store.get_learning_path("nonexistent") is None

    def test_list_paths(self, store, subject):
        store.create_learning_path(subject.id, "P1")
        store.create_learning_path(subject.id, "P2")
        paths = store.list_learning_paths(subject.id)
        assert len(paths) == 2

    def test_list_paths_scoped_to_subject(self, store):
        s1 = store.create_subject("S1")
        s2 = store.create_subject("S2")
        store.create_learning_path(s1.id, "Path A")
        store.create_learning_path(s2.id, "Path B")
        assert len(store.list_learning_paths(s1.id)) == 1
        assert len(store.list_learning_paths(s2.id)) == 1

    def test_update_path(self, store, subject):
        path = store.create_learning_path(subject.id, "Old")
        store.update_learning_path(path.id, title="New", status="active")
        got = store.get_learning_path(path.id)
        assert got.title == "New"
        assert got.status == "active"

    def test_update_path_description(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        store.update_learning_path(path.id, description="Updated desc")
        got = store.get_learning_path(path.id)
        assert got.description == "Updated desc"

    def test_update_path_noop_when_no_args(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        store.update_learning_path(path.id)
        got = store.get_learning_path(path.id)
        assert got.title == "P"  # unchanged

    def test_delete_path(self, store, subject):
        path = store.create_learning_path(subject.id, "Temp")
        store.delete_learning_path(path.id)
        assert store.get_learning_path(path.id) is None

    def test_delete_path_updates_list(self, store, subject):
        p1 = store.create_learning_path(subject.id, "P1")
        p2 = store.create_learning_path(subject.id, "P2")
        store.delete_learning_path(p1.id)
        paths = store.list_learning_paths(subject.id)
        assert len(paths) == 1
        assert paths[0].id == p2.id


# ------------------------------------------------------------------
# Path Steps
# ------------------------------------------------------------------


class TestPathSteps:
    def test_add_step(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1", "My concept")
        assert step.activity_type == "concept"
        assert step.activity_id == "c1"
        assert step.title == "My concept"
        assert step.ordinal == 0

    def test_add_step_auto_ordinal(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        s1 = store.add_path_step(path.id, "concept", "c1")
        s2 = store.add_path_step(path.id, "quiz", "q1")
        s3 = store.add_path_step(path.id, "exercise", "e1")
        assert s1.ordinal == 0
        assert s2.ordinal == 1
        assert s3.ordinal == 2

    def test_add_step_explicit_ordinal(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1", ordinal=5)
        assert step.ordinal == 5

    def test_get_step(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1")
        got = store.get_path_step(step.id)
        assert got is not None
        assert got.activity_type == "concept"

    def test_get_step_unknown_returns_none(self, store):
        assert store.get_path_step("nonexistent") is None

    def test_list_steps_ordered(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        store.add_path_step(path.id, "quiz", "q1")
        store.add_path_step(path.id, "concept", "c1")
        steps = store.list_path_steps(path.id)
        # List is ordered by ordinal
        assert steps[0].activity_type == "quiz"
        assert steps[1].activity_type == "concept"

    def test_update_step_status(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1")
        store.update_path_step(step.id, status="completed")
        got = store.get_path_step(step.id)
        assert got.status == "completed"
        assert got.completed_at is not None

    def test_update_step_status_in_progress(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1")
        store.update_path_step(step.id, status="in_progress")
        got = store.get_path_step(step.id)
        assert got.status == "in_progress"
        assert got.completed_at is None  # only set on "completed"

    def test_update_step_ordinal(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1")
        store.update_path_step(step.id, ordinal=10)
        got = store.get_path_step(step.id)
        assert got.ordinal == 10

    def test_reorder_steps(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        s1 = store.add_path_step(path.id, "concept", "c1")
        s2 = store.add_path_step(path.id, "quiz", "q1")
        store.reorder_path_steps(path.id, [s2.id, s1.id])
        steps = store.list_path_steps(path.id)
        assert steps[0].activity_id == "q1"
        assert steps[1].activity_id == "c1"

    def test_delete_step(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        step = store.add_path_step(path.id, "concept", "c1")
        store.delete_path_step(step.id)
        assert store.get_path_step(step.id) is None

    def test_cascade_delete_path(self, store, subject):
        path = store.create_learning_path(subject.id, "P")
        store.add_path_step(path.id, "concept", "c1")
        store.add_path_step(path.id, "quiz", "q1")
        store.delete_learning_path(path.id)
        assert store.list_path_steps(path.id) == []


# ------------------------------------------------------------------
# Subject Domain
# ------------------------------------------------------------------


class TestSubjectDomain:
    def test_default_domain(self, store, subject):
        assert store.get_subject_domain(subject.id) == "generique"

    def test_set_domain(self, store, subject):
        store.set_subject_domain(subject.id, "programmation")
        assert store.get_subject_domain(subject.id) == "programmation"

    def test_set_domain_science(self, store, subject):
        store.set_subject_domain(subject.id, "sciences")
        assert store.get_subject_domain(subject.id) == "sciences"

    def test_set_domain_overwrite(self, store, subject):
        store.set_subject_domain(subject.id, "programmation")
        store.set_subject_domain(subject.id, "mathematiques")
        assert store.get_subject_domain(subject.id) == "mathematiques"

    def test_unknown_subject_domain_returns_generique(self, store):
        assert store.get_subject_domain("nonexistent") == "generique"
