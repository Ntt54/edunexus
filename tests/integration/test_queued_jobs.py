"""Imports queue=true suivis dans ingestion_jobs (bug prod : table vide).

Tout import ``queue=true`` (chemin des deux UIs) doit créer une ligne
``ingestion_jobs`` (statut initial ``queued``/5 %) que le worker séquentiel
fait progresser (phases 5/20/45/70/90/100, ``failed`` + errors.log en
échec) ; la réponse HTTP garde ``book_id/status/queued`` et AJOUTE
``job_id``. Aucun doublon de ligne (dédup fingerprint ⇒ completed
immédiat existant). 100 % offline.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _make_embed_transport(state: dict, gate: asyncio.Event | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/embed" in url or "api/embed" in url:
            if gate is not None:
                await gate.wait()
            body = json.loads(request.content) if request.content else {}
            inputs = body.get("input", [])
            state["calls"] += 1
            vecs = [[float((i * 3 + j) % 5) / 5 for j in range(4)] for i in range(len(inputs))]
            return httpx.Response(200, json={"embeddings": vecs}, request=request)
        ndjson = json.dumps({"message": {"content": '{"domaine": "mathematiques"}'}, "done": False}) + "\n"
        ndjson += json.dumps({"done": True}) + "\n"
        return httpx.Response(
            200,
            content=ndjson.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def svc(tmp_path: Path) -> SimpleNamespace:
    store = LibraryStore(tmp_path)
    state = {"calls": 0}
    client = OllamaClient(transport=_make_embed_transport(state))
    config = Config(config_dir=tmp_path)
    service = TutorService(store, client, config)
    return SimpleNamespace(store=store, client=client, config=config, service=service, state=state)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state = {"calls": 0}

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_embed_transport(state))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _book_file(tmp_path: Path, name: str = "cours.txt") -> Path:
    path = tmp_path / name
    path.write_text(f"Contenu specifique {name} sur la photosynthese. " * 60, encoding="utf-8")
    return path


async def _wait_job(service: TutorService, job_id: str, timeout: float = 20.0) -> list[dict]:
    deadline = time.time() + timeout
    history: list[dict] = []
    last: dict | None = None
    while time.time() < deadline:
        last = service.get_ingestion_job(job_id)
        assert last is not None, f"job {job_id} introuvable"
        if not history or history[-1]["status"] != last["status"] or history[-1]["progress_percent"] != last["progress_percent"]:
            history.append(dict(last))
        if last["status"] in ("completed", "failed"):
            return history
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} jamais terminal : {last}")


# ---------------------------------------------------------------------------
# Service : ensure_book_job
# ---------------------------------------------------------------------------


def test_ensure_book_job_creates_queued_row(svc: SimpleNamespace, tmp_path: Path) -> None:
    path = _book_file(tmp_path)
    _sid, book = svc.service.register_import("SVT", str(path))
    job = svc.service.ensure_book_job(book.id, _sid, str(path))
    assert job["status"] == "queued"
    assert job["progress_percent"] == 5
    assert job["book_id"] == book.id
    assert job["original_filename"] == "cours.txt"
    # Sans worker : la ligne reste en attente (pre-fix : aucune ligne).
    rows = svc.store.list_ingestion_jobs()
    assert any(j["id"] == job["id"] for j in rows)


def test_ensure_book_job_reuses_open_job(svc: SimpleNamespace, tmp_path: Path) -> None:
    path = _book_file(tmp_path)
    _sid, book = svc.service.register_import("SVT", str(path))
    first = svc.service.ensure_book_job(book.id, _sid, str(path))
    second = svc.service.ensure_book_job(book.id, _sid, str(path))
    assert second["id"] == first["id"], "double POST rapide ⇒ même ligne, pas de doublon"
    mine = [j for j in svc.store.list_ingestion_jobs() if j.get("book_id") == book.id]
    assert len(mine) == 1


def test_queued_is_first_status(svc: SimpleNamespace) -> None:
    order = list(svc.store.INGESTION_STATUS_ORDER)
    assert order[0] == "queued"
    assert "uploaded" in order  # statut initial historique conservé
    # Le défaut de création reste uploaded/0 (import_job inchangé).
    job = svc.store.create_ingestion_job(source_type="file", original_filename="a.txt")
    assert job["status"] == "uploaded" and job["progress_percent"] == 0


# ---------------------------------------------------------------------------
# Service : le worker fait progresser LA ligne (pas de chemin aveugle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_worker_drives_job_to_completed(
    tmp_path: Path,
) -> None:
    store = LibraryStore(tmp_path)
    state = {"calls": 0}
    gate = asyncio.Event()
    client = OllamaClient(transport=_make_embed_transport(state, gate=gate))
    config = Config(config_dir=tmp_path)
    service = TutorService(store, client, config)
    path = _book_file(tmp_path)
    sid, book = service.register_import("SVT", str(path))
    job = service.ensure_book_job(book.id, sid, str(path))

    await service.start_index_queue()
    # Preuve live : la ligne est visible et avance pendant l'import réel.
    deadline = time.time() + 20.0
    seen_embedding = False
    while time.time() < deadline:
        cur = service.get_ingestion_job(job["id"])
        assert cur is not None
        if cur["status"] == "embedding":
            seen_embedding = True
            assert cur["progress_percent"] == 90
            break
        await asyncio.sleep(0.05)
    assert seen_embedding, "phase embedding observable pendant le polling"
    gate.set()

    history = await _wait_job(service, job["id"])
    last = history[-1]
    assert last["status"] == "completed"
    assert last["progress_percent"] == 100
    assert last["nodes_created"] > 0
    assert store.get_book_status(book.id) == "indexed"
    # Monotone sur l'ordre incluant queued.
    order = list(store.INGESTION_STATUS_ORDER)
    for prev, cur in zip(history, history[1:]):
        assert cur["progress_percent"] >= prev["progress_percent"]
        if cur["status"] != prev["status"]:
            assert order.index(cur["status"]) > order.index(prev["status"])


# ---------------------------------------------------------------------------
# Route : queue=true ⇒ job_id + suivi
# ---------------------------------------------------------------------------


def _wait_route_job(c: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = c.get(f"/api/ingestion/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} jamais terminal : {last}")


def test_queue_route_returns_job_id_and_tracks(
    tmp_path: Path, client: TestClient
) -> None:
    path = _book_file(tmp_path)
    r = client.post(
        "/api/tutor/import",
        json={"subject": "SVT", "path": str(path), "queue": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Réponse compatible : champs historiques + job_id ajouté.
    assert body["status"] == "pending"
    assert body["queued"] is True
    assert body["book_id"]
    assert body.get("job_id"), "job_id requis pour le polling temps réel"

    # Ligne visible via list_ingestion_jobs pendant/après l'import réel.
    jobs = client.get("/api/ingestion/jobs").json()["jobs"]
    assert any(j["id"] == body["job_id"] for j in jobs)

    final = _wait_route_job(client, body["job_id"])
    assert final["status"] == "completed"
    assert final["progress_percent"] == 100
    books = client.get("/api/tutor/books").json()["books"]
    assert any(b["id"] == body["book_id"] and b["status"] == "indexed" for b in books)


def test_queue_route_duplicate_reuses_job(
    tmp_path: Path, client: TestClient
) -> None:
    path = _book_file(tmp_path)
    first = client.post(
        "/api/tutor/import", json={"subject": "SVT", "path": str(path), "queue": True}
    ).json()
    second = client.post(
        "/api/tutor/import", json={"subject": "SVT", "path": str(path), "queue": True}
    ).json()
    assert second["book_id"] == first["book_id"]
    if second.get("status") != "ready":
        assert second.get("job_id") == first["job_id"], "pas de 2e ligne"
    open_rows = [
        j
        for j in client.get("/api/ingestion/jobs").json()["jobs"]
        if j.get("book_id") == first["book_id"] and j["status"] not in ("completed", "failed")
    ]
    assert len(open_rows) <= 1


def test_queue_route_failure_marks_failed_and_logs(
    tmp_path: Path, client: TestClient, monkeypatch
) -> None:
    async def _boom(self, *args, **kwargs):
        raise RuntimeError("panne simulee file")

    monkeypatch.setattr(web_server.TutorService, "_ingest_extract", _boom)
    path = _book_file(tmp_path, "panne.txt")
    r = client.post(
        "/api/tutor/import", json={"subject": "SVT", "path": str(path), "queue": True}
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    final = _wait_route_job(client, job_id)
    assert final["status"] == "failed"
    assert "panne simulee" in (final["error_message"] or "")
    errors_log = tmp_path / "config" / "errors.log"
    assert errors_log.exists(), "errors.log requis (principe VI)"
