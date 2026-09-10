"""Reprise auto de la file au démarrage (worker in-memory perdu au kill).

Après redémarrage, les livres `pending` restaient bloqués (bouton manuel
requis). Au lifespan startup, les pending sont repris (worker relancé),
sans dupliquer les lignes ni toucher terminés/échoués ; sans pending,
démarrage inchangé et rapide. 100 % offline (TestClient déclenche le
lifespan, embeddings mockés).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


def _make_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        if "api/embed" in str(request.url):
            body = json.loads(request.content) if request.content else {}
            inputs = body.get("input", [])
            vecs = [
                [float((i * 3 + j) % 5) / 5 for j in range(dim)]
                for i in range(len(inputs))
            ]
            return httpx.Response(200, json={"embeddings": vecs}, request=request)
        ndjson = (
            json.dumps(
                {"message": {"content": '{"domaine": "generique"}'}, "done": False}
            )
            + "\n"
            + json.dumps({"done": True})
            + "\n"
        )
        return httpx.Response(
            200,
            content=ndjson.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def scripted(monkeypatch):
    dim = 4

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)


def _seed_pending(config_dir: Path, name: str = "cours.txt") -> str:
    """Livre pending brut (comme après un kill) : sujet + ligne, sans indexation."""
    store = LibraryStore(config_dir)
    existing = next((s for s in store.list_subjects() if s.name == "SVT"), None)
    subj = existing if existing is not None else store.create_subject("SVT")
    p = config_dir / name
    p.write_text(f"Contenu specifique {name} sur la photosynthese. " * 60, encoding="utf-8")
    book = store.import_document(subj.id, str(p))
    assert store.get_book_status(book.id) == "pending"
    return book.id


def _wait_status(
    client: TestClient, book_id: str, status: str, timeout: float = 30.0
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = client.get("/api/tutor/index-status").json()["books"]
        row = next((b for b in rows if b["id"] == book_id), None)
        if row is not None and row["status"] == status:
            return
        time.sleep(0.05)
    raise AssertionError(f"book {book_id} jamais {status}")


def test_startup_resumes_pending(tmp_path: Path, scripted) -> None:
    config_dir = tmp_path / "config"
    book_id = _seed_pending(config_dir)
    with TestClient(web_server.create_app(config_dir=config_dir)) as client:
        _wait_status(client, book_id, "ready")
    # Une seule ligne jobs pour ce livre (pas de doublon).
    store = LibraryStore(config_dir)
    rows = store._conn.execute(
        "SELECT COUNT(*) AS c FROM ingestion_jobs WHERE book_id = ?", (book_id,)
    ).fetchone()["c"]
    assert rows == 1
    assert store.get_book_status(book_id) == "indexed"


def test_startup_without_pending_starts_nothing(tmp_path: Path, scripted, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    LibraryStore(config_dir)  # base vide, aucun pending
    calls: list = []
    real_start = web_server.TutorService.start_index_queue

    async def spy(self, **kwargs):
        calls.append(kwargs)
        return await real_start(self, **kwargs)

    monkeypatch.setattr(web_server.TutorService, "start_index_queue", spy)
    with TestClient(web_server.create_app(config_dir=config_dir)):
        pass
    assert calls == [], "aucun démarrage worker sans pending"


def test_startup_preserves_done_and_error(tmp_path: Path, scripted) -> None:
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subj = store.create_subject("SVT")
    ok_path = config_dir / "ok.txt"
    ok_path.write_text("contenu ok " * 40, encoding="utf-8")
    ok_book = store.import_document(subj.id, str(ok_path))
    store.mark_indexed(ok_book.id, 0)
    ko_path = config_dir / "ko.txt"
    ko_path.write_text("contenu ko " * 40, encoding="utf-8")
    ko_book = store.import_document(subj.id, str(ko_path))
    store.set_book_error(ko_book.id, "panne antérieure")
    pending_id = _seed_pending(config_dir, name="cours.txt")

    with TestClient(web_server.create_app(config_dir=config_dir)) as client:
        _wait_status(client, pending_id, "ready")

    assert store.get_book_status(ok_book.id) == "indexed"
    assert store.get_book_status(ko_book.id) == "error"
    rows = store._conn.execute(
        "SELECT COUNT(*) AS c FROM ingestion_jobs WHERE book_id IN (?, ?)",
        (ok_book.id, ko_book.id),
    ).fetchone()["c"]
    assert rows == 0, "aucune ligne pour terminés/échoués"


def test_resume_is_idempotent(tmp_path: Path, scripted, monkeypatch) -> None:
    """Deux reprises successives : la 2e est no-op (worker déjà en cours)."""
    import asyncio

    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService

    config_dir = tmp_path / "config"
    pending_id = _seed_pending(config_dir)
    store = LibraryStore(config_dir)
    config = Config(config_dir=config_dir)
    svc = TutorService(store, None, config)
    started = asyncio.Event()
    release = asyncio.Event()

    async def gated_run(self):
        started.set()
        await release.wait()

    monkeypatch.setattr(TutorService, "_run_index_queue", gated_run)

    async def _twice():
        first = await svc.resume_pending_queue()
        await asyncio.wait_for(started.wait(), timeout=10)
        second = await svc.resume_pending_queue()
        release.set()
        task = svc._index_queue_task
        if task is not None:
            await task
        return first, second

    first, second = asyncio.run(_twice())
    assert first["resumed"] is True
    assert second["resumed"] is False
    assert store.get_book_status(pending_id) == "pending"


def test_resume_without_pending_is_noop(tmp_path: Path) -> None:
    import asyncio

    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService

    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    config = Config(config_dir=config_dir)
    svc = TutorService(store, None, config)
    report = asyncio.run(svc.resume_pending_queue())
    assert report["resumed"] is False
    assert report["pending"] == 0
    assert svc._index_queue_task is None
