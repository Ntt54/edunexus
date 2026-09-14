"""T029 Contract: GET /api/tutor/books?subject_id=&all= filtering"""

import hashlib, uuid
from pathlib import Path
from fastapi.testclient import TestClient
from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path):
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_books_filtered_by_subject_id(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    s_python = store.create_subject("Python")
    s_java = store.create_subject("java")
    # create books
    def _book(subject_id, title):
        bid = uuid.uuid4().hex[:8]
        fp = hashlib.sha256(title.encode()).hexdigest()
        store._conn.execute(
            "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bid, title, f"/tmp/{title}.txt", "txt", fp, "indexed", "2026-01-01T00:00:00+00:00"),
        )
        store._conn.execute("INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subject_id, bid))
        store._conn.commit()
        return bid
    b1 = _book(s_python.id, "Intro Python")
    b2 = _book(s_java.id, "Intro Java")
    b3 = _book(s_python.id, "Advanced Python")
    client = _client(tmp_path)
    with client:
        # filtered by python -> 2
        r = client.get("/api/tutor/books", params={"subject_id": s_python.id})
        assert r.status_code == 200, r.text
        titles = {b["title"] for b in r.json()["books"]}
        assert "Intro Python" in titles and "Advanced Python" in titles
        assert "Intro Java" not in titles
        # filtered by java -> 1
        r2 = client.get("/api/tutor/books", params={"subject_id": s_java.id})
        assert r2.status_code == 200
        assert len(r2.json()["books"]) == 1
        # all=true returns all 3 regardless of subject_id
        r3 = client.get("/api/tutor/books", params={"subject_id": s_python.id, "all": "true"})
        assert r3.status_code == 200
        assert len(r3.json()["books"]) == 3
        # no params returns all (compat)
        r4 = client.get("/api/tutor/books")
        assert len(r4.json()["books"]) == 3


def test_books_filter_validation(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    store.create_subject("Python")
    client = _client(tmp_path)
    with client:
        r = client.get("/api/tutor/books", params={"subject_id": ""})
        assert r.status_code == 400
        r2 = client.get("/api/tutor/books", params={"subject_id": "unknown-id"})
        assert r2.status_code == 404
