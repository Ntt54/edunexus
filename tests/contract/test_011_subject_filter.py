"""T007 Contract: subject filtering for GET /api/tutor/learning-paths?subject_id=&learner_id= and /api/tutor/dashboard"""

from pathlib import Path
from fastapi.testclient import TestClient
from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path):
    app = create_app(config_dir=tmp_path / "config")
    return TestClient(app)


def test_learning_paths_filtered_by_couple(tmp_path: Path):
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    # two subjects
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")
    # learner
    learner = store.create_learner("Alice")
    # paths: python has one, java empty
    p = store.create_learning_path(s_python.id, "Parcours Python", learner_id=learner.id)
    store.add_path_step(p.id, "concept", "notion-1", "Introduction à Python")
    # Need also a path for java with different learner? keep java empty for this test
    client = _client(tmp_path)
    with client:
        # filter python should return 1
        r = client.get("/api/tutor/learning-paths", params={"subject_id": s_python.id, "learner_id": learner.id})
        assert r.status_code == 200, r.text
        assert len(r.json()["paths"]) == 1
        # filter java should return 0 (isolated)
        r2 = client.get("/api/tutor/learning-paths", params={"subject_id": s_java.id, "learner_id": learner.id})
        assert r2.status_code == 200
        assert len(r2.json()["paths"]) == 0
        # also via /api/tutor/paths alias
        r3 = client.get("/api/tutor/paths", params={"subject_id": s_java.id, "learner_id": learner.id})
        assert r3.status_code == 200
        assert r3.json()["paths"] == []


def test_learning_paths_validation_400_on_empty_subject_id(tmp_path: Path):
    client = _client(tmp_path)
    with client:
        r = client.get("/api/tutor/learning-paths", params={"subject_id": ""})
        assert r.status_code == 400


def test_dashboard_nextStep_null_when_empty(tmp_path: Path):
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    s_java = store.create_subject("java")
    learner = store.create_learner("Bob")
    client = _client(tmp_path)
    with client:
        r = client.get("/api/tutor/dashboard", params={"subject_id": s_java.id, "learner_id": learner.id})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["nextStep"] is None
        assert "counts" in data


def test_dashboard_returns_nextStep_when_path_exists(tmp_path: Path):
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subj = store.create_subject("Python")
    learner = store.create_learner("Alice")
    p = store.create_learning_path(subj.id, "Parcours", learner_id=learner.id)
    store.add_path_step(p.id, "concept", "notion-1", "Etape 1")
    client = _client(tmp_path)
    with client:
        r = client.get("/api/tutor/dashboard", params={"subject_id": subj.id, "learner_id": learner.id})
        assert r.status_code == 200
        data = r.json()
        assert data["nextStep"] is not None
        assert data["nextStep"]["title"] == "Etape 1"
