"""US3 P1-A (T020) — ingestion à jobs async + polling (FR-006, E-006).

Couvre (100 % offline, MockTransport, aucun démon) :
- cycle de vie ``uploaded → … → completed`` monotone (statut + progression) ;
- ``job_id`` retourné aussitôt (import synchrone côté appelant) ;
- dedup sha256 : doublon exact ⇒ job ``completed`` immédiat, ``nodes_created=0`` ;
- échec simulé ⇒ ``failed`` + ``error_message`` + entrée ``errors.log`` ;
- migration ``ingestion_jobs`` idempotente (appliquée 2x sans erreur) ;
- ``get/list_ingestion_job`` + routes ``GET /api/ingestion/jobs[/{id}]``.

Adapté de ``autreprojet/OpenTutor-main`` (``services/ingestion/pipeline.py``
phases ``_PHASE_LABELS``, ``routers/upload_processing.py`` background persistant,
``models/ingestion.py`` table jobs) vers ``LibraryStore`` SQLite + ``asyncio.Task``.
"""

from __future__ import annotations

import asyncio
import hashlib
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

_STATUS_ORDER = [
    "uploaded",
    "extracting",
    "classifying",
    "dispatching",
    "embedding",
    "completed",
]

_EXPECTED_JOB_COLUMNS = {
    "id",
    "source_type",
    "original_filename",
    "url",
    "content_hash",
    "status",
    "progress_percent",
    "phase_label",
    "embedding_status",
    "nodes_created",
    "error_message",
    "book_id",
    "created_at",
    "updated_at",
}


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
def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.delenv("EMBEDDING_MODE", raising=False)
    store = LibraryStore(tmp_path)
    state = {"calls": 0}
    client = OllamaClient(transport=_make_embed_transport(state))
    config = Config(config_dir=tmp_path)
    service = TutorService(store, client, config)
    return SimpleNamespace(store=store, client=client, config=config, service=service, state=state)


def _book_file(tmp_path: Path, name: str = "cours.txt", text: str | None = None) -> Path:
    path = tmp_path / name
    path.write_text(text if text is not None else "Photosynthèse et respiration cellulaire. " * 60, encoding="utf-8")
    return path


async def _wait_job(service: TutorService, job_id: str, timeout: float = 20.0) -> list[dict]:
    """Poll jusqu'au statut terminal ; retourne l'historique des snapshots."""
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


def _assert_monotone(history: list[dict]) -> None:
    for prev, cur in zip(history, history[1:]):
        assert cur["progress_percent"] >= prev["progress_percent"], (
            f"progression non monotone : {prev['progress_percent']} -> {cur['progress_percent']}"
        )
        if cur["status"] != prev["status"] and cur["status"] != "failed":
            assert _STATUS_ORDER.index(cur["status"]) > _STATUS_ORDER.index(prev["status"]), (
                f"transition arrière interdite : {prev['status']} -> {cur['status']}"
            )


# ---------------------------------------------------------------------------
# T021 — migration idempotente
# ---------------------------------------------------------------------------


def test_migration_ingestion_jobs_is_idempotent(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    store._migrate_ingestion_jobs()
    store._migrate_ingestion_jobs()  # 2e application : sans erreur
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(ingestion_jobs)")}
    assert _EXPECTED_JOB_COLUMNS <= cols


def test_store_guards_against_backward_transitions(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    job = store.create_ingestion_job(source_type="file", original_filename="a.txt", content_hash="h")
    assert job["status"] == "uploaded" and job["progress_percent"] == 0
    store.update_ingestion_job(job["id"], status="dispatching", progress_percent=70)
    # régression interdite : ni statut arrière, ni progression décroissante
    store.update_ingestion_job(job["id"], status="extracting", progress_percent=10)
    guarded = store.get_ingestion_job(job["id"])
    assert guarded is not None
    assert guarded["status"] == "dispatching"
    assert guarded["progress_percent"] == 70
    # failed depuis tout état + message
    store.update_ingestion_job(job["id"], status="failed", error_message="boom")
    failed = store.get_ingestion_job(job["id"])
    assert failed is not None and failed["status"] == "failed"
    assert failed["error_message"] == "boom"


# ---------------------------------------------------------------------------
# T022 — cycle de vie async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_job_lifecycle_completed(svc: SimpleNamespace, tmp_path: Path) -> None:
    path = _book_file(tmp_path)
    job_id = svc.service.import_job("SVT", str(path))
    assert isinstance(job_id, str) and job_id, "job_id retourné aussitôt"
    first = svc.service.get_ingestion_job(job_id)
    assert first["source_type"] == "file"
    assert first["original_filename"] == "cours.txt"
    assert first["status"] in _STATUS_ORDER
    assert first["content_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()

    history = await _wait_job(svc.service, job_id)
    _assert_monotone(history)
    last = history[-1]
    assert last["status"] == "completed"
    assert last["progress_percent"] == 100
    assert last["phase_label"], "phase_label affichable requis"
    assert last["nodes_created"] > 0
    assert last["embedding_status"] in ("done", "skipped")
    assert last["book_id"], "job lié au livre après dispatch"
    assert svc.store.get_book_status(last["book_id"]) == "indexed"


@pytest.mark.asyncio
async def test_import_job_reports_intermediate_phase(
    svc: SimpleNamespace, tmp_path: Path
) -> None:
    """Phase intermédiaire observable pendant l'embedding (polling)."""
    gate = asyncio.Event()
    gated = OllamaClient(transport=_make_embed_transport(svc.state, gate=gate))
    svc.service.client = gated
    path = _book_file(tmp_path, "lent.txt")
    job_id = svc.service.import_job("SVT", str(path))
    deadline = time.time() + 20.0
    seen: dict | None = None
    while time.time() < deadline:
        seen = svc.service.get_ingestion_job(job_id)
        if seen["status"] == "embedding":
            break
        await asyncio.sleep(0.05)
    assert seen is not None and seen["status"] == "embedding"
    assert seen["phase_label"], "phase_label FR affiché pendant le polling"
    assert seen["progress_percent"] == 90
    gate.set()
    history = await _wait_job(svc.service, job_id)
    assert history[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_duplicate_content_hash_completes_without_nodes(
    svc: SimpleNamespace, tmp_path: Path
) -> None:
    path = _book_file(tmp_path)
    first_id = svc.service.import_job("SVT", str(path))
    first_hist = await _wait_job(svc.service, first_id)
    assert first_hist[-1]["status"] == "completed"
    calls_after_first = svc.state["calls"]
    assert calls_after_first > 0

    second_id = svc.service.import_job("SVT", str(path))
    assert second_id != first_id
    second_hist = await _wait_job(svc.service, second_id)
    second = second_hist[-1]
    assert second["status"] == "completed"
    assert second["nodes_created"] == 0, "doublon exact : zéro nœud créé"
    assert second["book_id"] == first_hist[-1]["book_id"], "référence au livre existant"
    assert svc.state["calls"] == calls_after_first, "doublon : zéro nouvel appel embed"


@pytest.mark.asyncio
async def test_failure_marks_failed_and_logs(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(self, *args, **kwargs):
        raise RuntimeError("panne simulée d'extraction")

    monkeypatch.setattr(TutorService, "_ingest_extract", _boom)
    path = _book_file(tmp_path, "panne.txt")
    job_id = svc.service.import_job("SVT", str(path))
    history = await _wait_job(svc.service, job_id)
    last = history[-1]
    assert last["status"] == "failed"
    assert "panne simulée" in (last["error_message"] or "")
    assert svc.store.get_book_status(last["book_id"]) == "error"
    errors_log = svc.config.config_dir / "errors.log"
    assert errors_log.exists(), "errors.log requis (principe VI)"
    assert "panne simulée" in errors_log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_skip_mode_completes_without_embed_calls(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBEDDING_MODE", "skip")
    path = _book_file(tmp_path, "sansvec.txt")
    job_id = svc.service.import_job("SVT", str(path))
    history = await _wait_job(svc.service, job_id)
    last = history[-1]
    assert last["status"] == "completed", "BM25 suffit : completed malgré skip"
    assert last["embedding_status"] == "skipped"
    assert svc.state["calls"] == 0, "skip : zéro appel réseau d'embedding"


@pytest.mark.asyncio
async def test_explicit_provider_failure_surfaces_named_error(
    svc: SimpleNamespace, tmp_path: Path
) -> None:
    """Pas de fallback silencieux : backend explicite HS ⇒ job failed nommé (Phase 5a)."""
    from src.ollama_tutor.tutor.providers.gguf_embedding import GGUFEmbeddingError

    class _ExplodingProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise GGUFEmbeddingError("backend exploded")

    svc.service.embedding_provider = _ExplodingProvider()
    path = _book_file(tmp_path, "gguf-ko.txt")
    job_id = svc.service.import_job("Hist", str(path))
    history = await _wait_job(svc.service, job_id)
    last = history[-1]
    assert last["status"] == "failed"
    assert "[gguf-provider]" in (last["error_message"] or "")
    assert "GGUFEmbeddingError" in (last["error_message"] or "")
    book = svc.store.get_book(last["book_id"])
    assert book is not None and book.status == "error"
    assert "[gguf-provider]" in (book.error or "")


def test_get_unknown_job_raises_and_list(svc: SimpleNamespace, tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        svc.service.get_ingestion_job("inexistant")
    path = _book_file(tmp_path, "liste.txt")
    job_id = svc.service.import_job("SVT", str(path))
    jobs = svc.service.list_ingestion_jobs(limit=50)
    assert any(j["id"] == job_id for j in jobs)
    assert all("phase_label" in j and "progress_percent" in j for j in jobs)


def test_import_and_index_keeps_book_contract(
    svc: SimpleNamespace, tmp_path: Path
) -> None:
    """Compat : import_and_index retourne toujours un Book indexé (appelants actuels)."""
    path = _book_file(tmp_path, "compat.txt")
    book = svc.service.import_and_index("SVT", str(path), background=False)
    assert book.id
    assert svc.store.get_book_status(book.id) == "indexed"
    assert book.chunks_done > 0


# ---------------------------------------------------------------------------
# T023 — routes de polling (transport fin)
# ---------------------------------------------------------------------------


def _scripted_transport(state: dict):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        inputs = body.get("input", [])
        state["calls"] += 1
        vecs = [[0.1, 0.2, 0.3, 0.4] for _ in inputs]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


class _ScriptedClient(web_server.OllamaClient):
    def __init__(self, *a, **k):
        super().__init__(transport=_scripted_transport({"calls": 0}))


def test_ingestion_routes_list_and_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_server, "OllamaClient", _ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        r = c.get("/api/ingestion/jobs")
        assert r.status_code == 200
        assert r.json()["jobs"] == []
        r = c.get("/api/ingestion/jobs/inexistant")
        assert r.status_code == 404
