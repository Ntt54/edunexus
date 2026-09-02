"""Contract tests for explainable path endpoints (Feature 008, US3).

Offline via TestClient against the FastAPI app with a tmp config dir.
Subjects are created implicitly via ``POST /api/tutor/import``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.web.server import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def _create_subject(c: TestClient, tmp_path: Path, name: str) -> str:
    p = tmp_path / f"{name}.txt"
    p.write_text("The quick brown fox jumps over the lazy dog. " * 50, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": name, "path": str(p)})
    assert r.status_code == 200
    subs = c.get("/api/tutor/subjects").json()["subjects"]
    for s in subs:
        if s["name"] == name:
            return s["id"]
    raise AssertionError(f"subject {name} not found after import")


def test_path_generate_empty_graph(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        r = c.post(f"/api/tutor/subjects/{sid}/path/generate")
        assert r.status_code == 200
        assert "path" in r.json()


def test_path_generate_after_graph_build(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        c.post(f"/api/tutor/subjects/{sid}/graph/build")
        r = c.post(f"/api/tutor/subjects/{sid}/path/generate")
        assert r.status_code == 200
        steps = r.json()["path"]["steps"]
        for step in steps:
            assert step["why_now"]
            assert step["planned_activity"]
            assert step["expected_proof"]


def test_path_reorder_excludes_step(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        c.post(f"/api/tutor/subjects/{sid}/graph/build")
        gen = c.post(f"/api/tutor/subjects/{sid}/path/generate").json()
        steps = gen["path"]["steps"]
        if not steps:
            return
        payload = [{"id": s["id"], "ordinal": i, "excluded": (i == 0)} for i, s in enumerate(steps)]
        r = c.put(f"/api/tutor/subjects/{sid}/path", json={"steps": payload})
        assert r.status_code == 200
        remaining = r.json()["path"]["steps"]
        assert len(remaining) == len(steps) - 1
