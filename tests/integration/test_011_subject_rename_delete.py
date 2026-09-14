"""T015 Integration: rename Non classé + delete with fallback.

Scenario 2 of quickstart: PATCH "Non classé" -> "Python" (200) everywhere,
duplicate rename -> 400 Nom déjà utilisé, DELETE with fallbackSubjectId.
Uses real tmp SQLite store (LibraryStore) via TestClient, fully offline.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_rename_non_classe_to_python_propagates(tmp_path: Path):
    """PATCH Non classé -> Python visible partout after refresh."""
    store = LibraryStore(tmp_path / "config")
    s_nc = store.create_subject("Non classé")
    # seed a path so dashboard/badge propagation can be observed elsewhere
    learner = store.create_learner("Alice")
    p = store.create_learning_path(s_nc.id, "Parcours", learner_id=learner.id)
    store.add_path_step(p.id, "concept", "notion-1", "Etape")

    client = _client(tmp_path)
    with client:
        r = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": "Python"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Python"
        # persisted in store
        assert store.get_subject(s_nc.id).name == "Python"
        # propagated via GET /api/tutor/subjects
        subjects = client.get("/api/tutor/subjects").json()["subjects"]
        assert "Python" in [s["name"] for s in subjects]
        assert "Non classé" not in [s["name"] for s in subjects]
        # PATCH to same name (idempotent) stays 200
        r2 = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": "Python"})
        assert r2.status_code == 200


def test_rename_duplicate_case_insensitive_400_and_empty_400(tmp_path: Path):
    """Duplicate (case-insensitive) -> 400 Nom déjà utilisé, empty -> 400."""
    store = LibraryStore(tmp_path / "config")
    s_nc = store.create_subject("Non classé")
    s_java = store.create_subject("java")

    client = _client(tmp_path)
    with client:
        # duplicate to existing java, case-insensitive
        r = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": "JAVA"})
        assert r.status_code == 400, r.text
        assert "Nom déjà utilisé" in r.json()["detail"]

        r2 = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": "java"})
        assert r2.status_code == 400

        # empty / whitespace -> 400
        r3 = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": "   "})
        assert r3.status_code == 400
        r4 = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": ""})
        assert r4.status_code == 400

        # ensure java unchanged and still present
        assert store.get_subject(s_java.id).name == "java"


def test_rename_via_put_still_enforces_409_for_duplicate(tmp_path: Path):
    """PUT legacy path must still reject duplicate with 409 (contract compat)."""
    store = LibraryStore(tmp_path / "config")
    s_a = store.create_subject("Python")
    s_b = store.create_subject("java")
    client = _client(tmp_path)
    with client:
        r = client.put(f"/api/tutor/subjects/{s_b.id}", json={"name": "Python"})
        assert r.status_code == 409, r.text


def test_delete_non_classe_renamed_with_fallback(tmp_path: Path):
    """DELETE renommée 'Python' (ex Non classé) -> fallback vers java."""
    store = LibraryStore(tmp_path / "config")
    s_nc = store.create_subject("Non classé")
    s_java = store.create_subject("java")

    # rename first so we test the renamed value path
    client = _client(tmp_path)
    with client:
        r_patch = client.patch(f"/api/tutor/subjects/{s_nc.id}", json={"name": "Python"})
        assert r_patch.status_code == 200

        # create a subject_books link to verify cascade does not kill book
        import hashlib
        import uuid

        book_id = uuid.uuid4().hex[:8]
        store._conn.execute(
            "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, "Livre", "/tmp/x.txt", "txt", hashlib.sha256(b"x").hexdigest(), "indexed", "2026-01-01T00:00:00+00:00"),
        )
        store._conn.execute("INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (s_nc.id, book_id))
        store._conn.commit()

        r_del = client.delete(f"/api/tutor/subjects/{s_nc.id}")
        assert r_del.status_code == 200, r_del.text
        data = r_del.json()
        # With multiple subjects remaining, API returns deleted=id + fallbackSubjectId
        assert data["deleted"] == s_nc.id
        assert data["fallbackSubjectId"] == s_java.id

        # cascade check
        assert store.get_subject(s_nc.id) is None
        assert store.get_book(book_id) is not None  # book survives as orphan
        assert store.get_book(book_id).title == "Livre"

        # remaining subjects must be visible, fallback indeed selectable
        remaining = client.get("/api/tutor/subjects").json()["subjects"]
        assert len(remaining) == 1
        assert remaining[0]["id"] == s_java.id
        assert remaining[0]["name"] == "java"

        # trying to GET paths for deleted subject now 404
        r_404 = client.get("/api/tutor/learning-paths", params={"subject_id": s_nc.id})
        # learning-paths validates existence? via store.get_subject check -> 404 ? spec says 404 when unknown
        # Our server returns 404 for unknown subject via list_paths
        assert r_404.status_code == 404


def test_delete_single_subject_returns_deleted_true(tmp_path: Path):
    """Single remaining subject delete returns legacy {deleted: true}."""
    store = LibraryStore(tmp_path / "config")
    s = store.create_subject("Non classé")
    client = _client(tmp_path)
    with client:
        r = client.delete(f"/api/tutor/subjects/{s.id}")
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": True}
        assert store.get_subject(s.id) is None


def test_delete_unknown_404(tmp_path: Path):
    client = _client(tmp_path)
    with client:
        r = client.delete("/api/tutor/subjects/does-not-exist")
        assert r.status_code == 404, r.text
