"""T021 Integration: learners filtered per matière (Q3=B) + CRUD + activation.

Scenario 3 of quickstart: en 'java' créer Alice ne pollue pas Python,
GET /learners?subject_id= isolation via couple, activate header coherence,
delete cascade. Uses tmp SQLite store, fully offline, no daemon.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_learners_filtered_per_subject_q3b(tmp_path: Path):
    """Q3=B: learners visible only in matière where they have couple data."""
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")
    alice = store.create_learner("Alice")
    bob = store.create_learner("Bob")

    # Create couple data: Alice <-> Python, Bob <-> java
    p1 = store.create_learning_path(s_python.id, "P Python", learner_id=alice.id)
    store.add_path_step(p1.id, "concept", "notion-1", "Etape Python")
    p2 = store.create_learning_path(s_java.id, "P Java", learner_id=bob.id)
    store.add_path_step(p2.id, "concept", "notion-2", "Etape Java")

    client = _client(tmp_path)
    with client:
        # Python filter -> only Alice
        r_py = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
        assert r_py.status_code == 200, r_py.text
        ids_py = {l["id"] for l in r_py.json()["learners"]}
        assert alice.id in ids_py
        assert bob.id not in ids_py

        # Java filter -> only Bob
        r_java = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert r_java.status_code == 200
        ids_java = {l["id"] for l in r_java.json()["learners"]}
        assert bob.id in ids_java
        assert alice.id not in ids_java

        # No filter -> both (compat)
        r_all = client.get("/api/tutor/learners")
        assert len(r_all.json()["learners"]) == 2

        # Second call same subject_id still isolated (reload persistence)
        r_java2 = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert {l["id"] for l in r_java2.json()["learners"]} == ids_java


def test_learners_filtered_via_lesson_discussion_branch(tmp_path: Path):
    """Second UNION branch: discussion couple also makes learner visible."""
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    charlie = store.create_learner("Charlie")
    path = store.create_learning_path(s_python.id, "P", learner_id=charlie.id)
    step = store.add_path_step(path.id, "concept", "notion-x", "Titre")
    store.get_or_create_lesson_discussion(step.id, charlie.id)

    client = _client(tmp_path)
    with client:
        r = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
        assert r.status_code == 200
        assert any(l["id"] == charlie.id for l in r.json()["learners"])


def test_learner_create_validate_activate_delete_cascade(tmp_path: Path):
    """Create validation, activate header, delete cascade filtered."""
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")

    client = _client(tmp_path)
    with client:
        # Validation: empty -> 400
        r_empty = client.post("/api/tutor/learners", json={"name": "   "})
        assert r_empty.status_code == 400

        # Create Alice via API (global creation, no subject binding yet)
        r_alice = client.post("/api/tutor/learners", json={"name": "Alice"})
        assert r_alice.status_code == 200, r_alice.text
        alice_id = r_alice.json()["id"]
        assert r_alice.json()["name"] == "Alice"

        # Duplicate case-insensitive -> 400 Nom déjà utilisé
        r_dup = client.post("/api/tutor/learners", json={"name": "alice"})
        assert r_dup.status_code == 400
        assert "Nom déjà utilisé" in r_dup.json()["detail"]

        # Create Bob via store so we can immediately bind couple data
        alice = store.get_learner(alice_id)
        assert alice is not None
        bob = store.create_learner("Bob")

        # Bind couples: Alice->Python, Bob->java
        p1 = store.create_learning_path(s_python.id, "P Python", learner_id=alice.id)
        store.add_path_step(p1.id, "concept", "n1", "Etape")
        p2 = store.create_learning_path(s_java.id, "P Java", learner_id=bob.id)
        store.add_path_step(p2.id, "concept", "n2", "Etape Java")

        # Verify isolation after creation
        r_py = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
        assert alice.id in {l["id"] for l in r_py.json()["learners"]}
        assert bob.id not in {l["id"] for l in r_py.json()["learners"]}
        r_java = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert bob.id in {l["id"] for l in r_java.json()["learners"]}

        # Activate Alice -> header coherence (returns learner + subjects scoped)
        r_act = client.post(f"/api/tutor/learners/{alice.id}/activate")
        assert r_act.status_code == 200, r_act.text
        act_body = r_act.json()
        assert act_body["learner"]["id"] == alice.id
        # subjects scoped to learner (may be empty if no subjects.learner_id binding, still 200)
        assert "subjects" in act_body

        # Activate unknown -> 404
        r_bad_act = client.post("/api/tutor/learners/nope/activate")
        assert r_bad_act.status_code == 404

        # Delete Bob -> cascade, then filtered lists update
        r_del = client.delete(f"/api/tutor/learners/{bob.id}")
        assert r_del.status_code == 200, r_del.text
        assert r_del.json()["deleted"] == bob.id
        assert store.get_learner(bob.id) is None

        # java filter now empty (Bob was the only one with couple data there)
        r_java_after = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert r_java_after.status_code == 200
        assert all(l["id"] != bob.id for l in r_java_after.json()["learners"])
        assert bob.id not in {l["id"] for l in r_java_after.json()["learners"]}

        # Python filter still contains Alice
        r_py_after = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
        assert alice.id in {l["id"] for l in r_py_after.json()["learners"]}

        # Delete unknown learner -> 404
        r_del_bad = client.delete("/api/tutor/learners/nope")
        assert r_del_bad.status_code == 404


def test_learners_filtered_validation_and_switch_coherence(tmp_path: Path):
    """Badge/header coherence: switching subject changes filtered list deterministically."""
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")
    alice = store.create_learner("Alice")
    bob = store.create_learner("Bob")
    # Alice only in Python, Bob only in java
    p1 = store.create_learning_path(s_python.id, "P", learner_id=alice.id)
    store.add_path_step(p1.id, "concept", "n", "Etape")
    p2 = store.create_learning_path(s_java.id, "P2", learner_id=bob.id)
    store.add_path_step(p2.id, "concept", "n2", "Etape2")

    client = _client(tmp_path)
    with client:
        # Empty subject_id -> 400 (validation)
        r_empty = client.get("/api/tutor/learners", params={"subject_id": ""})
        assert r_empty.status_code == 400

        # Unknown subject_id -> 404 (with Q3=B validation)
        r_unknown = client.get("/api/tutor/learners", params={"subject_id": "unknown-id"})
        assert r_unknown.status_code == 404

        # Switch header simulation: 20 navigations must keep coherence (SC-003)
        for _ in range(20):
            r_py = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
            assert {l["id"] for l in r_py.json()["learners"]} == {alice.id}
            r_java = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
            assert {l["id"] for l in r_java.json()["learners"]} == {bob.id}


def test_learner_create_bound_to_subject_visible_immediately(tmp_path: Path):
    """T031/FR-005/US3-AC1: POST ?subject_id= binds the new learner so it is
    immediately visible in the filtered list (before any path/discussion)."""
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")

    client = _client(tmp_path)
    with client:
        # Create Alice bound to java: visible in java at once, invisible in Python
        r_alice = client.post(
            "/api/tutor/learners",
            params={"subject_id": s_java.id},
            json={"name": "Alice"},
        )
        assert r_alice.status_code == 200, r_alice.text
        alice_id = r_alice.json()["id"]

        r_java = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert alice_id in {l["id"] for l in r_java.json()["learners"]}
        r_py = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
        assert alice_id not in {l["id"] for l in r_py.json()["learners"]}

        # Reload-equivalent: a fresh GET still shows her (membership persisted)
        r_java2 = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert alice_id in {l["id"] for l in r_java2.json()["learners"]}

        # Global creation (compat): not visible in filtered lists until activity
        r_bob = client.post("/api/tutor/learners", json={"name": "Bob"})
        assert r_bob.status_code == 200, r_bob.text
        bob_id = r_bob.json()["id"]
        assert bob_id not in {
            l["id"]
            for l in client.get(
                "/api/tutor/learners", params={"subject_id": s_java.id}
            ).json()["learners"]
        }

        # Validation: empty subject_id -> 400, unknown -> 404
        r_empty = client.post(
            "/api/tutor/learners", params={"subject_id": ""}, json={"name": "Zoe"}
        )
        assert r_empty.status_code == 400
        r_unknown = client.post(
            "/api/tutor/learners", params={"subject_id": "nope"}, json={"name": "Zoe"}
        )
        assert r_unknown.status_code == 404
