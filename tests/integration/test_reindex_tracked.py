"""Réindexation suivie via job (reindex HTTP bloqué en prod).

`POST /api/tutor/books/{id}/reindex` répondait après des minutes
d'embeddings CPU sans aucun progrès visible. Désormais : 202 immédiat
`{"book_id","job_id","status"}` + ligne `ingestion_jobs` (embedding par
batch, monotone) pilotée en fond ; la bannière Vue suit déjà les jobs.

100 % offline (embeddings mockés, aucun démon).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


def _make_transport(state: dict, gate: threading.Event | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        import asyncio as _asyncio

        url = str(request.url)
        if "/api/embed" in url or "api/embed" in url:
            if gate is not None:
                while not gate.is_set():
                    await _asyncio.sleep(0.01)
            body = json.loads(request.content) if request.content else {}
            inputs = body.get("input", [])
            state["calls"] += 1
            vecs = [[float((i * 3 + j) % 5) / 5 for j in range(4)] for i in range(len(inputs))]
            return httpx.Response(200, json={"embeddings": vecs}, request=request)
        ndjson = json.dumps({"message": {"content": '{"domaine": "generique"}'}, "done": False}) + "\n"
        ndjson += json.dumps({"done": True}) + "\n"
        return httpx.Response(
            200,
            content=ndjson.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state = {"calls": 0}

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_transport(state))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def gated_client(tmp_path: Path, monkeypatch):
    state = {"calls": 0}
    gate = threading.Event()
    gate.set()

    class GatedEmbedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_transport(state, gate=gate))

    monkeypatch.setattr(web_server, "OllamaClient", GatedEmbedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield SimpleNamespaceClient(c, gate)


class SimpleNamespaceClient:
    def __init__(self, client: TestClient, gate: threading.Event) -> None:
        self.client = client
        self.gate = gate

    def __getattr__(self, name: str):
        return getattr(self.client, name)


def _seed_book(tmp_path: Path, name: str = "cours.txt", chunks: int = 3) -> Path:
    p = tmp_path / name
    p.write_text(f"Contenu reindex {name} " * 40, encoding="utf-8")
    return p


def _import(client: TestClient, tmp_path: Path, subject: str, name: str) -> str:
    p = _seed_book(tmp_path, name)
    r = client.post("/api/tutor/import", json={"subject": subject, "path": str(p)})
    assert r.status_code == 200, r.text
    return r.json()["book_id"]


def _wait_indexed(client: TestClient, book_id: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    rows: list = []
    while time.time() < deadline:
        rows = client.get("/api/tutor/index-status").json()["books"]
        row = next((r for r in rows if r["id"] == book_id), None)
        if row is not None and row["status"] == "ready":
            return
        time.sleep(0.05)
    raise AssertionError(f"book {book_id} jamais ready : {rows}")


def _wait_job(client: TestClient, job_id: str, timeout: float = 30.0) -> list[dict]:
    deadline = time.time() + timeout
    history: list[dict] = []
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/ingestion/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if not history or history[-1]["status"] != last["status"] or history[-1]["progress_percent"] != last["progress_percent"]:
            history.append(dict(last))
        if last["status"] in ("completed", "failed"):
            return history
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} jamais terminal : {last}")


def test_reindex_returns_202_with_job_id(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_indexed(client, book_id)
    r = client.post(f"/api/tutor/books/{book_id}/reindex")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["book_id"] == book_id
    assert body.get("job_id"), "job_id requis pour le suivi"
    assert body["status"] == "pending"

    history = _wait_job(client, body["job_id"])
    last = history[-1]
    assert last["status"] == "completed"
    assert last["progress_percent"] == 100
    assert last["nodes_created"] > 0
    # Progression monotone sur l'ordre des statuts.
    order = ["queued", "uploaded", "extracting", "classifying", "dispatching", "embedding", "completed"]
    for prev, cur in zip(history, history[1:]):
        assert cur["progress_percent"] >= prev["progress_percent"]
        if cur["status"] != prev["status"]:
            assert order.index(cur["status"]) > order.index(prev["status"])


def test_reindex_progress_visible_via_list(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_indexed(client, book_id)
    job_id = client.post(f"/api/tutor/books/{book_id}/reindex").json()["job_id"]
    jobs = client.get("/api/ingestion/jobs").json()["jobs"]
    assert any(j["id"] == job_id for j in jobs)
    final = _wait_job(client, job_id)
    assert final[-1]["status"] == "completed"


def test_concurrent_reindex_reuses_open_job(tmp_path: Path, gated_client) -> None:
    c = gated_client
    book_id = _import(c, tmp_path, "SVT", "cours.txt")
    _wait_indexed(c, book_id)
    c.gate.clear()
    first = c.post(f"/api/tutor/books/{book_id}/reindex")
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]
    # Le worker est bloqué en embedding (gate) : 2e appel ⇒ même job.
    deadline = time.time() + 15.0
    while time.time() < deadline:
        cur = c.get(f"/api/ingestion/jobs/{job_id}").json()
        if cur["status"] == "embedding":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("job jamais en embedding")
    second = c.post(f"/api/tutor/books/{book_id}/reindex")
    assert second.status_code == 202, second.text
    assert second.json()["job_id"] == job_id, "pas de doublon de ligne"
    c.gate.set()
    history = _wait_job(c, job_id)
    assert history[-1]["status"] == "completed"
    rows = [
        j for j in c.get("/api/ingestion/jobs").json()["jobs"] if j.get("book_id") == book_id
    ]
    assert len(rows) == 1


def test_reindex_unknown_book_404(tmp_path: Path, client: TestClient) -> None:
    r = client.post("/api/tutor/books/inconnu/reindex")
    assert r.status_code == 404, r.text


def test_reindex_without_chunks_completes_immediately(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_indexed(client, book_id)
    store = LibraryStore(tmp_path / "config")
    store._conn.execute("DELETE FROM chunks WHERE book_id = ?", (book_id,))
    store._conn.commit()
    r = client.post(f"/api/tutor/books/{book_id}/reindex")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    final = _wait_job(client, job_id)
    assert final[-1]["status"] == "completed"
    assert final[-1]["nodes_created"] == 0


def test_reindex_keeps_book_indexed_cancel_safe(
    tmp_path: Path, client: TestClient
) -> None:
    """Le livre reste indexed (jamais pending) : file/ordre/annulation intacts."""
    book_id = _import(client, tmp_path, "SVT", "cours.txt")
    _wait_indexed(client, book_id)
    job_id = client.post(f"/api/tutor/books/{book_id}/reindex").json()["job_id"]
    # Annuler un reindex en cours est refusé proprement (pas de purge).
    cancel = client.post(f"/api/tutor/books/{book_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json().get("cancelled") is False
    final = _wait_job(client, job_id)
    assert final[-1]["status"] == "completed"
    books = client.get("/api/tutor/books").json()["books"]
    assert any(b["id"] == book_id and b["status"] == "indexed" for b in books)
