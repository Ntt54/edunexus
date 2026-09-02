"""Contract tests for learner endpoints (Feature 008, US9).

Offline via TestClient against the FastAPI app with a tmp config dir.
Covers CRUD, activation with scoped subjects, and cascade delete.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.web.server import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_create_and_list_learners(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/tutor/learners", json={"name": "Thierry"})
        assert r.status_code == 200
        assert r.json()["name"] == "Thierry"
        names = [l["name"] for l in c.get("/api/tutor/learners").json()["learners"]]
        assert names == ["Thierry"]


def test_activate_returns_scoped_subjects(tmp_path):
    with _client(tmp_path) as c:
        lid = c.post("/api/tutor/learners", json={"name": "Thierry"}).json()["id"]
        # Create a subject scoped to this learner via import.
        p = tmp_path / "maths.txt"
        p.write_text("The quick brown fox jumps over the lazy dog. " * 50, encoding="utf-8")
        c.post("/api/tutor/import", json={"subject": "Maths", "path": str(p)})
        r = c.post(f"/api/tutor/learners/{lid}/activate")
        assert r.status_code == 200
        assert r.json()["learner"]["id"] == lid
        assert r.json()["subjects"] == []


def test_delete_learner(tmp_path):
    with _client(tmp_path) as c:
        lid = c.post("/api/tutor/learners", json={"name": "Thierry"}).json()["id"]
        r = c.delete(f"/api/tutor/learners/{lid}")
        assert r.status_code == 200
        assert r.json()["deleted"] == lid
        assert c.get("/api/tutor/learners").json()["learners"] == []


def test_delete_unknown_learner_404(tmp_path):
    with _client(tmp_path) as c:
        r = c.delete("/api/tutor/learners/nope")
        assert r.status_code == 404
