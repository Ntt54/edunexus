"""Unit tests for pgvector migration (SQLite fallback + mocked PG)."""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch


def _make_vec(dim=4):
    return np.random.rand(dim).astype(np.float32).tolist()


def test_config_pgvector_defaults():
    from ollama_tutor.config import Config

    cfg = Config(config_dir=__import__("pathlib").Path(__import__("tempfile").mkdtemp()))
    assert cfg.pgvector_enabled is False
    assert "postgres" in cfg.pgvector_dsn


def test_pgvector_schema_mock():
    from ollama_tutor.tutor.store import LibraryStore
    import tempfile, pathlib

    tmp = pathlib.Path(tempfile.mkdtemp())
    store = LibraryStore(tmp, pgvector_enabled=True, pgvector_dsn="postgresql://x")
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(store, "get_pg_connection", return_value=mock_conn):
        store.init_pgvector_schema(dim=384)
    # should have called CREATE EXTENSION and CREATE TABLE
    calls = " ".join(str(c) for c in mock_cur.execute.call_args_list)
    assert "vector" in calls
    assert "hnsw" in calls
    assert "vector_cosine_ops" in calls
    mock_conn.commit.assert_called()
    mock_conn.close.assert_called()


def test_add_and_search_pg_mock():
    from ollama_tutor.tutor.store import LibraryStore
    import tempfile, pathlib

    tmp = pathlib.Path(tempfile.mkdtemp())
    store = LibraryStore(tmp, pgvector_enabled=True)
    # mock add
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(store, "get_pg_connection", return_value=mock_conn):
        store.add_chunks_pg("subj1", "book1", ["hello world"], [[0.1, 0.2, 0.3, 0.4]], model="m")
    assert mock_cur.execute.called
    # mock search
    mock_conn2 = MagicMock()
    mock_cur2 = MagicMock()
    mock_cur2.fetchall.return_value = [("id1", 0.9), ("id2", 0.5)]
    mock_conn2.cursor.return_value.__enter__.return_value = mock_cur2
    with patch.object(store, "get_pg_connection", return_value=mock_conn2):
        res = store.search_similar_pg("subj1", [0.1, 0.2, 0.3, 0.4], k=5)
    assert res == [("id1", 0.9), ("id2", 0.5)]


def test_search_fallback_when_disabled():
    from ollama_tutor.tutor.store import LibraryStore
    import tempfile, pathlib

    tmp = pathlib.Path(tempfile.mkdtemp())
    store = LibraryStore(tmp, pgvector_enabled=False)
    # inserting via sqlite directly then searching fallback
    store._conn.execute("INSERT INTO subjects (id,name,created_at,last_used_at) VALUES (?,?,?,?)", ("s1","Subj","2024-01-01","2024-01-01"))
    store._conn.execute("INSERT INTO books (id,title,source_path,format,fingerprint,status,created_at) VALUES (?,?,?,?,?,?,?)", ("b1","Book","/tmp/a.txt","txt","fp1","indexed","2024-01-01"))
    store._conn.execute("INSERT INTO subject_books (subject_id,book_id) VALUES (?,?)", ("s1","b1"))
    store._conn.commit()
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    store.add_chunks("s1", "b1", ["hello"], [vec.tolist()], model="m")
    res = store.search_similar_pg("s1", [1.0, 0.0, 0.0], k=5)
    # brute-force fallback returns at least one
    assert len(res) >= 1


@pytest.mark.asyncio
async def test_retrieval_hybrid_with_pgvector_mock():
    from ollama_tutor.tutor.store import LibraryStore
    from ollama_tutor.tutor.retrieval import Retriever
    import tempfile, pathlib

    tmp = pathlib.Path(tempfile.mkdtemp())
    store = LibraryStore(tmp, pgvector_enabled=True)
    # mock pg methods
    store.get_indexed_chunks_pg = MagicMock(return_value=[
        {"id": "c1", "book_id": "b1", "text": "hello world", "chapter": None, "page": None, "embedding": [0.1, 0.2]},
        {"id": "c2", "book_id": "b1", "text": "other", "chapter": None, "page": None, "embedding": [0.3, 0.4]},
    ])
    store.list_books = MagicMock(return_value=[MagicMock(id="b1", title="Book")])
    store.search_similar_pg = MagicMock(return_value=[("c1", 0.95), ("c2", 0.2)])
    client = MagicMock()
    async def fake_embed(model, texts):
        return [[0.1, 0.2]]
    client.embed = fake_embed
    retr = Retriever(store=store, client=client, model="m", floor=0.0)
    res = await retr.retrieve("s1", "hello", k=5)
    assert len(res) >= 1
    assert res[0].chunk_id == "c1"


def test_pgvector_index_protocol():
    from ollama_tutor.tutor.pgvector_index import PgVectorIndex

    idx = PgVectorIndex(dsn="postgresql://x", subject_id="s1")
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [("id1", 0.8)]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(idx, "_conn", return_value=mock_conn):
        res = idx.search([0.1, 0.2, 0.3], k=5)
    assert res == [("id1", 0.8)]
    idx.invalidate()


def test_migration_report_mock():
    import sys
    import tempfile, pathlib, hashlib
    from unittest.mock import MagicMock, patch
    # psycopg is optional (C2). Skip gracefully if not installed unless we can mock via sys.modules.
    try:
        import psycopg  # noqa: F401
    except ImportError:
        # Provide a mock psycopg module so patch target resolves and test still validates logic
        if "psycopg" not in sys.modules:
            mock_psycopg = MagicMock()
            sys.modules["psycopg"] = mock_psycopg
        else:
            mock_psycopg = sys.modules["psycopg"]
        # ensure connect exists
        if not hasattr(mock_psycopg, "connect"):
            mock_psycopg.connect = MagicMock()
    # now proceed — if psycopg still not importable, skip instead of fail
    try:
        import psycopg  # noqa: F811
    except ImportError:
        pytest.skip("psycopg not installed")
    from ollama_tutor.tutor.store import LibraryStore
    import numpy as np

    tmp = pathlib.Path(tempfile.mkdtemp())
    store = LibraryStore(tmp)
    store._conn.execute("INSERT INTO subjects (id,name,created_at,last_used_at) VALUES (?,?,?,?)", ("s1","S","2024-01-01","2024-01-01"))
    store._conn.execute("INSERT INTO books (id,title,source_path,format,fingerprint,status,created_at) VALUES (?,?,?,?,?,?,?)", ("b1","B","/tmp/a.txt","txt","fp","indexed","2024-01-01"))
    store._conn.execute("INSERT INTO subject_books (subject_id,book_id) VALUES (?,?)", ("s1","b1"))
    store._conn.commit()
    vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    store.add_chunks("s1", "b1", ["hello"], [vec.tolist()], model="m")

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    # Patch both the global psycopg.connect and the store's get_pg_connection for pgvector schema init
    # Use sys.modules mock plus patch on store to cover both code paths
    with patch("psycopg.connect", return_value=mock_conn):
        with patch.object(LibraryStore, "get_pg_connection", return_value=mock_conn):
            from ollama_tutor.tutor.migrate_to_pgvector import migrate

            report = migrate(config_dir=tmp, dsn="postgresql://x")
            assert report["total"] >= 1
            assert report["inserted"] >= 1
            assert "dim" in report


def test_web_pgvector_status_endpoint():
    from fastapi.testclient import TestClient
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    from ollama_tutor.web.server import create_app

    app = create_app(config_dir=tmp)
    client = TestClient(app)
    r = client.get("/api/tutor/pgvector/status")
    assert r.status_code == 200
    data = r.json()
    assert "enabled" in data
    assert "ok" in data
    # default disabled
    assert data["enabled"] is False
