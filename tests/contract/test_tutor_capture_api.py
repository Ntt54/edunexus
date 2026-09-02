"""Contract tests for program capture endpoints (Feature 008, US6).

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


def test_capture_program_ready(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        src = tmp_path / "program.txt"
        src.write_text("1 Introduction\n1.1 Variables\n2 Fonctions\n", encoding="utf-8")
        r = c.post(f"/api/tutor/subjects/{sid}/program/capture",
                   json={"path": str(src), "source_type": "pdf"})
        assert r.status_code == 200
        program = r.json()["program"]
        assert program["status"] == "ready"
        assert program["nodes"]


def test_get_program_returns_tree(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        src = tmp_path / "program.txt"
        src.write_text("1 Introduction\n", encoding="utf-8")
        pid = c.post(f"/api/tutor/subjects/{sid}/program/capture",
                     json={"path": str(src)}).json()["program"]["id"]
        r = c.get(f"/api/tutor/subjects/{sid}/program/{pid}")
        assert r.status_code == 200
        assert r.json()["program"]["id"] == pid


def test_correct_program_node(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        src = tmp_path / "program.txt"
        src.write_text("1 Introduction\n", encoding="utf-8")
        pid = c.post(f"/api/tutor/subjects/{sid}/program/capture",
                     json={"path": str(src)}).json()["program"]["id"]
        node_id = c.get(f"/api/tutor/subjects/{sid}/program/{pid}").json()["program"]["nodes"][0]["id"]
        r = c.put(f"/api/tutor/subjects/{sid}/program/{pid}/nodes/{node_id}",
                  json={"title": "Introduction à Java"})
        assert r.status_code == 200
        assert r.json()["title"] == "Introduction à Java"
        assert r.json()["validation_status"] == "corrected"


def test_confirm_program(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Java")
        src = tmp_path / "program.txt"
        src.write_text("1 Introduction\n", encoding="utf-8")
        pid = c.post(f"/api/tutor/subjects/{sid}/program/capture",
                     json={"path": str(src)}).json()["program"]["id"]
        r = c.post(f"/api/tutor/subjects/{sid}/program/{pid}/confirm")
        assert r.status_code == 200
        assert r.json()["program"]["validation_status"] == "confirmed"
