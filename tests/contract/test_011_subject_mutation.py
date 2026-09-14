"""T014 Contract: rename/delete Non classé via PATCH/DELETE"""

from pathlib import Path
from fastapi.testclient import TestClient
from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path):
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_rename_non_classe_via_patch(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Non classé")
    client = _client(tmp_path)
    with client:
        r = client.patch(f"/api/tutor/subjects/{subj.id}", json={"name": "Python"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Python"
        # verify persisted
        assert store.get_subject(subj.id).name == "Python"
        # propagation via GET subjects
        r2 = client.get("/api/tutor/subjects")
        assert "Python" in [s["name"] for s in r2.json()["subjects"]]


def test_rename_duplicate_case_insensitive_400(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    s1 = store.create_subject("Non classé")
    s2 = store.create_subject("java")
    client = _client(tmp_path)
    with client:
        r = client.patch(f"/api/tutor/subjects/{s1.id}", json={"name": "JAVA"})
        assert r.status_code == 400, r.text
        assert "Nom déjà utilisé" in r.json()["detail"]


def test_rename_empty_400(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    s = store.create_subject("Non classé")
    client = _client(tmp_path)
    with client:
        r = client.patch(f"/api/tutor/subjects/{s.id}", json={"name": "   "})
        assert r.status_code == 400


def test_delete_non_classe_with_fallback(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    s1 = store.create_subject("Non classé")
    s2 = store.create_subject("java")
    # create a book linked to s1 to test cascade via subject_books
    import hashlib, uuid
    book_id = uuid.uuid4().hex[:8]
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Livre", "/tmp/x.txt", "txt", hashlib.sha256(b"x").hexdigest(), "indexed", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.execute("INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (s1.id, book_id))
    store._conn.commit()
    client = _client(tmp_path)
    with client:
        r = client.delete(f"/api/tutor/subjects/{s1.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["deleted"] == s1.id
        assert "fallbackSubjectId" in data
        assert data["fallbackSubjectId"] == s2.id
        # cascade: subject_books removed but book survives as orphan
        assert store.get_subject(s1.id) is None
        assert store.get_book(book_id) is not None
