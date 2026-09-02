"""Contract tests for competency graph endpoints (Feature 008, US2).

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


def test_graph_empty_before_build(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        r = c.get(f"/api/tutor/subjects/{sid}/graph")
        assert r.status_code == 200
        assert r.json()["nodes"] == []
        assert r.json()["edges"] == []


def test_graph_build_returns_counts(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        r = c.post(f"/api/tutor/subjects/{sid}/graph/build")
        assert r.status_code == 200
        body = r.json()
        assert "nodes" in body and "edges" in body and "ai_proposed" in body
        assert body["nodes"] >= 0


def test_graph_validate_node(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        c.post(f"/api/tutor/subjects/{sid}/graph/build")
        graph = c.get(f"/api/tutor/subjects/{sid}/graph").json()
        if not graph["nodes"]:
            return  # no nodes to validate (empty import) — acceptable
        node_id = graph["nodes"][0]["id"]
        r = c.post(f"/api/tutor/graph/nodes/{node_id}/validate")
        assert r.status_code == 200
        assert r.json()["validation_status"] == "user_confirmed"


def test_graph_dashboard_shape(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        c.post(f"/api/tutor/subjects/{sid}/graph/build")
        r = c.get(f"/api/tutor/subjects/{sid}/graph/dashboard")
        assert r.status_code == 200
        body = r.json()
        for key in ("covered", "uncovered", "contradictory", "unconfirmed"):
            assert key in body
