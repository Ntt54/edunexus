"""T008 Integration: subject switch isolation (Mon parcours + Accueil + reload).

Covers Scenario 1 of quickstart: 2 subjects (Non classé with path, java empty),
switch via ?subject_id=&learner_id= params, verify filtered paths/dashboard
isolation, reload persistence simulated via second call, ensure no cross-contamination.

100 % offline: tmp SQLite + TestClient, no daemon, httpx.MockTransport not needed
for these reads but stack stays offline. Uses `from src.ollama_tutor...` imports
(repo root pythonpath=".").
"""

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def test_subject_isolation_paths_dashboard_and_reload(tmp_path: Path):
    """Mon parcours + Accueil isolated per couple, reload persists, no leak."""
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    s_nonclasse = store.create_subject("Non classé")
    s_java = store.create_subject("java")
    learner = store.create_learner("Alice")

    # Non classé has a path with one step -> dashboard nextStep present
    p = store.create_learning_path(s_nonclasse.id, "Parcours Python", learner_id=learner.id)
    store.add_path_step(p.id, "concept", "notion-1", "Introduction à Python")

    # java intentionally empty for this learner (isolation target)
    client = _client(tmp_path)
    with client:
        # --- Non classé filtered view: should contain the Python path ---
        r = client.get(
            "/api/tutor/learning-paths",
            params={"subject_id": s_nonclasse.id, "learner_id": learner.id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["paths"]) == 1
        assert body["paths"][0]["title"] == "Parcours Python"

        # Dashboard for Non classé: nextStep must be present, title matches step
        r_dash = client.get(
            "/api/tutor/dashboard",
            params={"subject_id": s_nonclasse.id, "learner_id": learner.id},
        )
        assert r_dash.status_code == 200, r_dash.text
        data = r_dash.json()
        assert data["nextStep"] is not None
        assert data["nextStep"]["title"] == "Introduction à Python"
        # Alias via /api/tutor/paths should agree
        r_alias = client.get(
            "/api/tutor/paths",
            params={"subject_id": s_nonclasse.id, "learner_id": learner.id},
        )
        assert r_alias.status_code == 200
        assert len(r_alias.json()["paths"]) == 1

        # --- Switch to java: both Mon parcours and Accueil must be empty ---
        r2 = client.get(
            "/api/tutor/learning-paths",
            params={"subject_id": s_java.id, "learner_id": learner.id},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["paths"] == []

        r_dash_java = client.get(
            "/api/tutor/dashboard",
            params={"subject_id": s_java.id, "learner_id": learner.id},
        )
        assert r_dash_java.status_code == 200, r_dash_java.text
        dash_java = r_dash_java.json()
        assert dash_java["nextStep"] is None
        assert "counts" in dash_java
        # Java alias also empty
        r_alias_java = client.get(
            "/api/tutor/paths",
            params={"subject_id": s_java.id, "learner_id": learner.id},
        )
        assert r_alias_java.status_code == 200
        assert r_alias_java.json()["paths"] == []

        # --- Reload persistence: second identical call must stay isolated ---
        r_reload = client.get(
            "/api/tutor/learning-paths",
            params={"subject_id": s_java.id, "learner_id": learner.id},
        )
        assert r_reload.status_code == 200
        assert r_reload.json()["paths"] == []
        r_dash_reload = client.get(
            "/api/tutor/dashboard",
            params={"subject_id": s_java.id, "learner_id": learner.id},
        )
        assert r_dash_reload.json()["nextStep"] is None

        # Back to Non classé must still show data (no leak in opposite direction)
        r_back = client.get(
            "/api/tutor/learning-paths",
            params={"subject_id": s_nonclasse.id, "learner_id": learner.id},
        )
        assert len(r_back.json()["paths"]) == 1


def test_subject_isolation_no_cross_contamination_10_switches(tmp_path: Path):
    """SC-005: 10 successive switches must never leak data between matières."""
    store = LibraryStore(tmp_path / "config")
    s_nc = store.create_subject("Non classé")
    s_java = store.create_subject("java")
    learner = store.create_learner("Bob")
    p = store.create_learning_path(s_nc.id, "Parcours Python", learner_id=learner.id)
    store.add_path_step(p.id, "concept", "c1", "Intro Python")

    client = _client(tmp_path)
    with client:
        for _ in range(10):
            r_nc = client.get("/api/tutor/learning-paths", params={"subject_id": s_nc.id, "learner_id": learner.id})
            assert len(r_nc.json()["paths"]) == 1, "Non classé must stay 1"
            r_java = client.get("/api/tutor/learning-paths", params={"subject_id": s_java.id, "learner_id": learner.id})
            assert r_java.json()["paths"] == [], "java must stay empty"
            d_nc = client.get("/api/tutor/dashboard", params={"subject_id": s_nc.id, "learner_id": learner.id}).json()
            d_java = client.get("/api/tutor/dashboard", params={"subject_id": s_java.id, "learner_id": learner.id}).json()
            assert d_nc["nextStep"] is not None
            assert d_java["nextStep"] is None


def test_subject_isolation_books_also_isolated(tmp_path: Path):
    """Library filtering must also be isolated per subject (Q4 baseline)."""
    import hashlib
    import uuid

    store = LibraryStore(tmp_path / "config")
    s_nc = store.create_subject("Non classé")
    s_java = store.create_subject("java")

    def _book(subject_id: str, title: str) -> str:
        bid = uuid.uuid4().hex[:8]
        fp = hashlib.sha256(title.encode()).hexdigest()
        store._conn.execute(
            "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bid, title, f"/tmp/{title}.txt", "txt", fp, "indexed", "2026-01-01T00:00:00+00:00"),
        )
        store._conn.execute("INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subject_id, bid))
        store._conn.commit()
        return bid

    b_python = _book(s_nc.id, "Intro Python")
    b_java = _book(s_java.id, "Intro Java")

    client = _client(tmp_path)
    with client:
        r_nc = client.get("/api/tutor/books", params={"subject_id": s_nc.id})
        assert r_nc.status_code == 200
        titles_nc = {b["title"] for b in r_nc.json()["books"]}
        assert "Intro Python" in titles_nc
        assert "Intro Java" not in titles_nc

        r_java = client.get("/api/tutor/books", params={"subject_id": s_java.id})
        assert len(r_java.json()["books"]) == 1
        assert r_java.json()["books"][0]["title"] == "Intro Java"

        # all=true must return both regardless of filter (bibliothèque toggle)
        r_all = client.get("/api/tutor/books", params={"subject_id": s_java.id, "all": "true"})
        assert len(r_all.json()["books"]) == 2
