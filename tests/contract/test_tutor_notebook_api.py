"""Contract tests for notebook endpoints (Feature 008, US8).

Offline via TestClient against the FastAPI app with a tmp config dir.
Covers GET notebook, POST notes, POST actions, DELETE output.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.web.server import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def _seed_subject(c, tmp_path) -> str:
    """Import a book to create a subject with indexed chunks."""
    p = tmp_path / "maths.txt"
    p.write_text("The quick brown fox jumps over the lazy dog. " * 50, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": "Maths", "path": str(p)})
    assert r.status_code == 200
    subs = c.get("/api/tutor/subjects").json()["subjects"]
    for s in subs:
        if s["name"] == "Maths":
            return s["id"]
    raise AssertionError("subject Maths not found after import")


def test_get_notebook(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        r = c.get(f"/api/tutor/subjects/{sid}/notebook")
        assert r.status_code == 200
        nb = r.json()["notebook"]
        assert nb["subject_id"] == sid
        assert nb["notes"] == []
        assert nb["outputs"] == []


def test_add_note(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/notes",
            json={"note": "Revoir les boucles"},
        )
        assert r.status_code == 200
        assert r.json()["notebook"]["notes"] == ["Revoir les boucles"]


def test_add_empty_note_400(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/notes", json={"note": "   "}
        )
        assert r.status_code == 400


def test_run_action(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/actions",
            json={"action": "summarize_source"},
        )
        assert r.status_code == 200
        out = r.json()["output"]
        assert out["kind"] == "summary"
        assert out["content"]


def test_run_quiz_without_answer(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/actions",
            json={"action": "quiz_without_answer"},
        )
        assert r.status_code == 200
        assert r.json()["output"]["kind"] == "quiz"


def test_unknown_action_400(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        r = c.post(
            f"/api/tutor/subjects/{sid}/notebook/actions", json={"action": "nope"}
        )
        assert r.status_code == 400


def test_delete_output(tmp_path):
    with _client(tmp_path) as c:
        sid = _seed_subject(c, tmp_path)
        out = c.post(
            f"/api/tutor/subjects/{sid}/notebook/actions",
            json={"action": "summarize_source"},
        ).json()["output"]
        r = c.delete(f"/api/tutor/notebook-outputs/{out['id']}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        outputs = c.get(f"/api/tutor/subjects/{sid}/notebook").json()["notebook"]["outputs"]
        assert all(o["id"] != out["id"] for o in outputs)
