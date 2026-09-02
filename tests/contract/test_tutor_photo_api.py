"""Contract tests for conversation photo endpoints (Feature 008, US7).

Offline via TestClient against the FastAPI app with a tmp config dir.
A subject is created via import, then a conversation, then a photo.
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


def _create_conversation(c: TestClient, subject_id: str) -> str:
    r = c.post("/api/tutor/conversations", json={"subject_id": subject_id, "title": "Conv"})
    assert r.status_code == 200
    return r.json()["conversation"]["id"]


def test_import_photo_pending(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Maths")
        cid = _create_conversation(c, sid)
        src = tmp_path / "photo.txt"
        src.write_text("2x + 3 = 7", encoding="utf-8")
        r = c.post(f"/api/tutor/conversations/{cid}/photo", json={"path": str(src)})
        assert r.status_code == 200
        photo = r.json()
        assert photo["conversation_id"] == cid
        assert photo["confirmation_status"] == "pending"
        assert "2x + 3 = 7" in photo["recognized_text"]


def test_confirm_photo(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Maths")
        cid = _create_conversation(c, sid)
        src = tmp_path / "photo.txt"
        src.write_text("contenu", encoding="utf-8")
        pid = c.post(f"/api/tutor/conversations/{cid}/photo",
                     json={"path": str(src)}).json()["id"]
        r = c.post(f"/api/tutor/conversation-photos/{pid}/confirm")
        assert r.status_code == 200
        assert r.json()["confirmation_status"] == "confirmed"
        assert r.json()["source_linkage"] == str(src)


def test_get_photo(tmp_path):
    with _client(tmp_path) as c:
        sid = _create_subject(c, tmp_path, "Maths")
        cid = _create_conversation(c, sid)
        src = tmp_path / "photo.txt"
        src.write_text("contenu", encoding="utf-8")
        pid = c.post(f"/api/tutor/conversations/{cid}/photo",
                     json={"path": str(src)}).json()["id"]
        r = c.get(f"/api/tutor/conversation-photos/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid
