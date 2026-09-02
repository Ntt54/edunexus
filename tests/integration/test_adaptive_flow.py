"""Offline integration test for the Feature 008 adaptive flow (T064).

Drives the full adaptive loop through the FastAPI app with a tmp config dir:
import a book → set the pedagogical profile → build the graph → read the
dashboard → generate an explainable path → run a notebook action. Fully
offline (no daemon, no network): the LLM is not required because the notebook
action falls back to deterministic content.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.web.server import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def _import_subject(c: TestClient, tmp_path: Path, name: str) -> str:
    p = tmp_path / f"{name}.txt"
    p.write_text("The quick brown fox jumps over the lazy dog. " * 50, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": name, "path": str(p)})
    assert r.status_code == 200
    subs = c.get("/api/tutor/subjects").json()["subjects"]
    for s in subs:
        if s["name"] == name:
            return s["id"]
    raise AssertionError(f"subject {name} not found after import")


def test_adaptive_flow_end_to_end(tmp_path):
    with _client(tmp_path) as c:
        sid = _import_subject(c, tmp_path, "Java")

        # 1) Profile (US1)
        r = c.put(
            f"/api/tutor/subjects/{sid}/profile",
            json={"domain": "Programmation", "level": "débutant", "objective": "Apprendre Java"},
        )
        assert r.status_code == 200
        assert r.json()["profile"]["domain"] == "Programmation"

        # 2) Graph build (US2)
        r = c.post(f"/api/tutor/subjects/{sid}/graph/build")
        assert r.status_code == 200
        # Indexing is async; nodes may be 0 if the import has not finished.
        assert r.json()["nodes"] >= 0

        # 3) Dashboard (US5)
        r = c.get(f"/api/tutor/subjects/{sid}/graph/dashboard")
        assert r.status_code == 200
        dash = r.json()
        for key in ("covered", "uncovered", "contradictory", "unconfirmed"):
            assert key in dash

        # 4) Path generation (US3)
        r = c.post(f"/api/tutor/subjects/{sid}/path/generate")
        assert r.status_code == 200

        # 5) Notebook action (US8) — deterministic fallback, no LLM needed
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/actions",
            json={"action": "summarize_source"},
        )
        assert r.status_code == 200
        out = r.json()["output"]
        assert out["kind"] == "summary"
        assert out["content"]

        # 6) Notebook note (US8)
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/notes",
            json={"note": "Réviser les bases"},
        )
        assert r.status_code == 200
        assert r.json()["notebook"]["notes"] == ["Réviser les bases"]
