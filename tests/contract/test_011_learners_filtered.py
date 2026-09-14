"""T020 Contract: GET /api/tutor/learners?subject_id= filtered by couple"""

from pathlib import Path
from fastapi.testclient import TestClient
from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path):
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_learners_filtered_by_subject(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")
    alice = store.create_learner("Alice")
    bob = store.create_learner("Bob")
    # Alice has a path in Python, Bob in java
    p1 = store.create_learning_path(s_python.id, "P Python", learner_id=alice.id)
    store.add_path_step(p1.id, "concept", "notion-1", "Etape")
    p2 = store.create_learning_path(s_java.id, "P Java", learner_id=bob.id)
    store.add_path_step(p2.id, "concept", "notion-2", "Etape Java")
    client = _client(tmp_path)
    with client:
        # Python filter should return only Alice
        r = client.get("/api/tutor/learners", params={"subject_id": s_python.id})
        assert r.status_code == 200, r.text
        ids = {l["id"] for l in r.json()["learners"]}
        assert alice.id in ids
        assert bob.id not in ids
        # Java filter only Bob
        r2 = client.get("/api/tutor/learners", params={"subject_id": s_java.id})
        assert r2.status_code == 200
        ids2 = {l["id"] for l in r2.json()["learners"]}
        assert bob.id in ids2
        assert alice.id not in ids2
        # No filter returns both
        r3 = client.get("/api/tutor/learners")
        assert r3.status_code == 200
        assert len(r3.json()["learners"]) == 2


def test_learners_filtered_via_discussion(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Python")
    learner = store.create_learner("Charlie")
    # create a path step and discussion to test second UNION branch
    path = store.create_learning_path(subj.id, "P", learner_id=learner.id)
    step = store.add_path_step(path.id, "concept", "notion-x", "Titre")
    store.get_or_create_lesson_discussion(step.id, learner.id)
    client = _client(tmp_path)
    with client:
        r = client.get("/api/tutor/learners", params={"subject_id": subj.id})
        assert r.status_code == 200
        assert any(l["id"] == learner.id for l in r.json()["learners"])


def test_learner_create_validation(tmp_path: Path):
    client = _client(tmp_path)
    with client:
        r = client.post("/api/tutor/learners", json={"name": "   "})
        assert r.status_code == 400
        r2 = client.post("/api/tutor/learners", json={"name": "Alice"})
        assert r2.status_code == 200
        r3 = client.post("/api/tutor/learners", json={"name": "alice"})
        assert r3.status_code == 400
        assert "Nom déjà utilisé" in r3.json()["detail"]
