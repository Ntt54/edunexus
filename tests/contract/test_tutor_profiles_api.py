"""Contract tests for pedagogical profile endpoints (Feature 008, US1).

Offline via TestClient against the FastAPI app with a tmp config dir.
Subjects are created implicitly via ``POST /api/tutor/import`` (the existing
subject-creation path), matching the pattern in test_tutor_rest_api.py.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.web.server import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def _create_subject(c: TestClient, tmp_path: Path, name: str) -> str:
    """Create a subject by importing a small text file (returns subject id)."""
    p = tmp_path / f"{name}.txt"
    p.write_text("The quick brown fox jumps over the lazy dog. " * 50, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": name, "path": str(p)})
    assert r.status_code == 200
    # The import creates/selects the subject; fetch its id from the subjects list.
    subs = c.get("/api/tutor/subjects").json()["subjects"]
    for s in subs:
        if s["name"] == name:
            return s["id"]
    raise AssertionError(f"subject {name} not found after import")


def test_templates_list(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/tutor/pedagogical-templates")
        assert r.status_code == 200
        names = {t["name"] for t in r.json()["templates"]}
        assert "Programmation" in names
        assert "Profil libre" in names


def test_profile_get_404_when_unset(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        r = c.get(f"/api/tutor/subjects/{sid}/profile")
        assert r.status_code == 404


def test_profile_put_and_get_round_trip(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        body = {
            "domain": "Programmation",
            "level": "supérieur",
            "objective": "apprendre Java pour créer des projets",
            "activities": ["exemples résolus", "mini-projets"],
            "mastery_criteria": ["écrire", "tester"],
        }
        r = c.put(f"/api/tutor/subjects/{sid}/profile", json=body)
        assert r.status_code == 200
        assert r.json()["profile"]["domain"] == "Programmation"
        r = c.get(f"/api/tutor/subjects/{sid}/profile")
        assert r.status_code == 200
        assert r.json()["profile"]["activities"] == ["exemples résolus", "mini-projets"]


def test_profile_put_requires_domain(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Maths")
        r = c.put(f"/api/tutor/subjects/{sid}/profile", json={"objective": "x"})
        assert r.status_code == 400


def test_interpret_goal(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/tutor/subjects/x/profile/interpret-goal",
                   json={"goal": "apprendre Java pour créer des projets"})
        assert r.status_code == 200
        assert r.json()["parameters"]["approach"] == "project"
