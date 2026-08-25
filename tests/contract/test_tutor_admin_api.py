"""Contract tests for the Phase 5a tutor admin REST surface.

Covers /api/tutor/engine, /api/tutor/models (GET/PUT), categories/corpora
CRUD + membership routes, exam scoping, /api/log-error, the global
exception handler and shutdown provider teardown. Offline via TestClient
with a scripted embed transport (same pattern as test_tutor_rest_api.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


def _make_embed_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inputs = body.get("input", [])
        vecs = [
            [float((i * 3 + j) % 5) / 5 for j in range(dim)]
            for i in range(len(inputs))
        ]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


class ScriptedClient(web_server.OllamaClient):
    def __init__(self, *a, **k):
        super().__init__(transport=_make_embed_transport(4))


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _write_gguf_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({
        "tutor": {
            "llama_bin": "/nonexistent/llama-server",
            "embed_gguf": "/models/granite-embedding.gguf",
            "docling_gguf": "/models/granite-docling.gguf",
        }
    }))


@pytest.fixture
def gguf_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    _write_gguf_config(tmp_path / "config")
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _post_book(c: TestClient, tmp_path: Path, subject: str, name: str, word: str) -> str:
    p = tmp_path / name
    p.write_text(f"The {word} roams the plains. " * 50, encoding="utf-8")
    r = c.post("/api/tutor/import", json={"subject": subject, "path": str(p)})
    assert r.status_code == 200
    return r.json()["book_id"]


def _wait_indexed(c: TestClient, count: int, timeout: float = 4.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        books = c.get("/api/tutor/index-status").json()["books"]
        if len(books) >= count and all(b["status"] == "ready" for b in books):
            return
        time.sleep(0.02)
    raise AssertionError(f"indexing did not complete: {books}")


# ---------------------------------------------------------------------------
# 1. Engine endpoint — both states.
# ---------------------------------------------------------------------------


def test_engine_unconfigured_reports_ollama(client: TestClient) -> None:
    r = client.get("/api/tutor/engine")
    assert r.status_code == 200
    assert r.json() == {"embedding": "ollama", "ocr": False}


def test_engine_configured_reports_gguf_and_ocr(gguf_client: TestClient) -> None:
    r = gguf_client.get("/api/tutor/engine")
    assert r.status_code == 200
    assert r.json() == {"embedding": "gguf-local", "ocr": True}


# ---------------------------------------------------------------------------
# 2. Models GET/PUT.
# ---------------------------------------------------------------------------


def test_models_get_offline_empty_lists(client: TestClient) -> None:
    r = client.get("/api/tutor/models")
    assert r.status_code == 200
    body = r.json()
    assert body["embedding"] == []
    assert body["llm"] == []
    assert body["current"]["embedding"] == "embeddinggemma"
    assert body["current"]["llm"]  # config default present


def test_models_put_round_trip_persists(client: TestClient) -> None:
    r = client.put(
        "/api/tutor/models",
        json={"embedding": "granite-emb", "llm": "granite-llm"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == {"embedding": "granite-emb", "llm": "granite-llm"}
    # Persisted: a fresh GET reflects the same values.
    assert client.get("/api/tutor/models").json()["current"] == {
        "embedding": "granite-emb",
        "llm": "granite-llm",
    }


def test_models_put_rejects_empty_strings(client: TestClient) -> None:
    assert client.put("/api/tutor/models", json={"embedding": ""}).status_code == 400
    assert client.put("/api/tutor/models", json={"embedding": "   "}).status_code == 400
    assert client.put("/api/tutor/models", json={"llm": ""}).status_code == 400
    # Partial update keeps the other field untouched.
    r = client.put("/api/tutor/models", json={"llm": "only-llm"})
    assert r.json()["current"]["embedding"] == "embeddinggemma"


# ---------------------------------------------------------------------------
# 3. Categories CRUD + membership (+ corpora mirror).
# ---------------------------------------------------------------------------


def test_categories_crud_with_conflicts(client: TestClient) -> None:
    r = client.post("/api/tutor/categories", json={"name": "Reference"})
    assert r.status_code == 200
    cat = r.json()["category"]
    assert set(cat) == {"id", "name"} and cat["name"] == "Reference"

    # Duplicate (case-insensitive) → 409.
    assert (
        client.post("/api/tutor/categories", json={"name": "reference"}).status_code
        == 409
    )

    # Rename OK; rename clash with another category → 409; unknown → 404.
    other = client.post("/api/tutor/categories", json={"name": "Other"}).json()["category"]
    assert (
        client.post(
            f"/api/tutor/categories/{cat['id']}/rename", json={"name": "Docs"}
        ).json()["category"]["name"]
        == "Docs"
    )
    assert (
        client.post(
            f"/api/tutor/categories/{other['id']}/rename", json={"name": "DOCS"}
        ).status_code
        == 409
    )
    assert (
        client.post("/api/tutor/categories/9999/rename", json={"name": "X"}).status_code
        == 404
    )

    assert client.delete(f"/api/tutor/categories/{cat['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/tutor/categories/{cat['id']}").json() == {"deleted": False}

    listed = client.get("/api/tutor/categories").json()["categories"]
    assert [c["name"] for c in listed] == ["Other"]
    assert set(listed[0]) == {"id", "name", "book_count"}


def test_corpora_crud_mirror(client: TestClient) -> None:
    corpus = client.post("/api/tutor/corpora", json={"name": "Exam Prep"}).json()["corpus"]
    assert (
        client.post("/api/tutor/corpora", json={"name": "exam prep"}).status_code == 409
    )
    assert (
        client.post(
            f"/api/tutor/corpora/{corpus['id']}/rename", json={"name": "Final Prep"}
        ).json()["corpus"]["name"]
        == "Final Prep"
    )
    assert client.delete(f"/api/tutor/corpora/{corpus['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/tutor/corpora/{corpus['id']}").json() == {"deleted": False}
    assert client.get("/api/tutor/corpora").json()["corpora"] == []


def test_membership_routes_categories_and_corpora(
    client: TestClient, tmp_path: Path
) -> None:
    book_id = _post_book(client, tmp_path, "MemberSubj", "member.txt", "otter")
    cat = client.post("/api/tutor/categories", json={"name": "Wetlands"}).json()["category"]
    corpus = client.post("/api/tutor/corpora", json={"name": "Bio Set"}).json()["corpus"]

    # Add (true), idempotent repeat (false), unknown book/category → 404.
    assert client.put(
        f"/api/tutor/books/{book_id}/categories", json={"category_id": cat["id"]}
    ).json() == {"added": True}
    assert client.put(
        f"/api/tutor/books/{book_id}/categories", json={"category_id": cat["id"]}
    ).json() == {"added": False}
    assert (
        client.put("/api/tutor/books/nope/categories", json={"category_id": cat["id"]}).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/tutor/books/{book_id}/categories", json={"category_id": 9999}
        ).status_code
        == 404
    )

    assert client.put(
        f"/api/tutor/books/{book_id}/corpora", json={"corpus_id": corpus["id"]}
    ).json() == {"added": True}

    # List back.
    cats = client.get(f"/api/tutor/books/{book_id}/categories")
    assert cats.status_code == 200
    assert [c["name"] for c in cats.json()["categories"]] == ["Wetlands"]
    assert (
        client.get("/api/tutor/books/nope/categories").status_code == 404
    )
    corps = client.get(f"/api/tutor/books/{book_id}/corpora").json()["corpora"]
    assert [c["name"] for c in corps] == ["Bio Set"]

    # book_count reflected in the listing.
    listed = client.get("/api/tutor/categories").json()["categories"]
    assert listed[0]["book_count"] == 1

    # Remove (true), repeat (false), unknown book → 404.
    assert client.delete(
        f"/api/tutor/books/{book_id}/categories/{cat['id']}"
    ).json() == {"removed": True}
    assert client.delete(
        f"/api/tutor/books/{book_id}/categories/{cat['id']}"
    ).json() == {"removed": False}
    assert (
        client.delete(f"/api/tutor/books/nope/categories/{cat['id']}").status_code == 404
    )
    assert client.delete(
        f"/api/tutor/books/{book_id}/corpora/{corpus['id']}"
    ).json() == {"removed": True}


# ---------------------------------------------------------------------------
# 4. Exam scoping.
# ---------------------------------------------------------------------------


def test_exam_scope_echo_and_restriction(client: TestClient, tmp_path: Path) -> None:
    _post_book(client, tmp_path, "ScopeSubj", "fox.txt", "fox")
    _post_book(client, tmp_path, "ScopeSubj", "zebra.txt", "zebra")
    _wait_indexed(client, 2)

    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    sid = next(s["id"] for s in subjects if s["name"] == "ScopeSubj")

    fox_c = client.post(
        f"/api/tutor/subjects/{sid}/concepts", json={"name": "fox"}
    ).json()
    zebra_c = client.post(
        f"/api/tutor/subjects/{sid}/concepts", json={"name": "zebra"}
    ).json()

    books = client.get("/api/tutor/books").json()["books"]
    fox_book = next(b for b in books if b["title"] == "fox")
    cat = client.post("/api/tutor/categories", json={"name": "Foxes"}).json()["category"]
    assert client.put(
        f"/api/tutor/books/{fox_book['id']}/categories",
        json={"category_id": cat["id"]},
    ).json() == {"added": True}

    # Scoped exam: only concepts grounded in scoped books' chunk text.
    r = client.post(
        f"/api/tutor/subjects/{sid}/exams",
        json={"size": 6, "time_limit_s": 600, "category_ids": [cat["id"]]},
    )
    assert r.status_code == 200
    exam = r.json()
    assert exam["scope"] == {"category_ids": [cat["id"]], "corpus_ids": []}
    concept_ids = {q["concept_id"] for q in exam["questions"]}
    assert concept_ids == {fox_c["id"]}  # zebra excluded by scope

    # Unscoped exam draws from ALL concepts (legacy behavior intact).
    unscoped = client.post(
        f"/api/tutor/subjects/{sid}/exams", json={"size": 6}
    ).json()
    assert unscoped["scope"] == {"category_ids": [], "corpus_ids": []}
    unscoped_ids = {q["concept_id"] for q in unscoped["questions"]}
    assert unscoped_ids == {fox_c["id"], zebra_c["id"]}

    # Scope selecting nothing known → 404.
    assert (
        client.post(
            f"/api/tutor/subjects/{sid}/exams",
            json={"size": 4, "category_ids": [9999]},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# 5. Error logging: endpoint + helper + global exception handler.
# ---------------------------------------------------------------------------


def test_log_error_endpoint_writes_tmp_errors_log(client: TestClient, tmp_path: Path) -> None:
    log_file = tmp_path / "config" / "errors.log"
    r = client.post(
        "/api/log-error",
        json={"message": "TypeError: x is undefined", "context": "tutor-view"},
    )
    assert r.status_code == 200
    assert r.json() == {"logged": True}
    content = log_file.read_text(encoding="utf-8")
    assert "[tutor-view] TypeError: x is undefined" in content
    # ISO-8601-ish timestamp prefix.
    assert content.startswith("[20")


def test_log_error_helper_direct(tmp_path: Path) -> None:
    from src.ollama_tutor.config import Config

    config = Config(config_dir=tmp_path / "cfg")
    log_file = tmp_path / "cfg" / "errors.log"
    assert not log_file.exists()  # lazy creation

    web_server._log_error(config, "unit", "boom happened", "Traceback...\n line 2")
    web_server._log_error(config, "unit", "no detail this time")

    content = log_file.read_text(encoding="utf-8")
    assert "[unit] boom happened\nTraceback...\n line 2\n" in content
    assert "[unit] no detail this time\n" in content
    assert content.count("[unit]") == 2


def test_global_exception_handler_logs_and_masks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)

    from src.ollama_tutor.tutor.store import LibraryStore

    def explode(self):
        raise RuntimeError("kaboom for science")

    monkeypatch.setattr(LibraryStore, "list_all_books", explode)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/tutor/books")
    assert r.status_code == 500
    assert r.json() == {"detail": "Erreur interne (journalisée dans errors.log)"}
    content = (tmp_path / "config" / "errors.log").read_text(encoding="utf-8")
    assert "[http]" in content
    assert "kaboom for science" in content
    assert "Traceback" in content


# ---------------------------------------------------------------------------
# 6. Provider wiring + shutdown teardown.
# ---------------------------------------------------------------------------


def test_shutdown_closes_providers_and_passes_parser(
    tmp_path: Path, monkeypatch
) -> None:
    closed: list[str] = []

    class FakeEmbeddingProvider:
        async def aclose(self) -> None:
            closed.append("emb")

    class FakeOCRProvider:
        async def aclose(self) -> None:
            closed.append("ocr")

    fake_emb = FakeEmbeddingProvider()
    fake_ocr = FakeOCRProvider()
    monkeypatch.setattr(
        web_server, "create_embedding_provider", lambda config: fake_emb
    )
    monkeypatch.setattr(web_server, "create_ocr_provider", lambda config: fake_ocr)

    captured: dict = {}
    real_service = web_server.TutorService

    class SpyService(real_service):
        def __init__(self, store, client, config, **kwargs):
            captured.update(kwargs)
            super().__init__(store, client, config, **kwargs)

    monkeypatch.setattr(web_server, "TutorService", SpyService)

    _write_gguf_config(tmp_path / "config")  # providers are only built when configured
    app = web_server.create_app(config_dir=tmp_path / "config")
    assert captured["embedding_provider"] is fake_emb
    parser = captured["document_parser"]
    assert isinstance(parser, web_server.HybridDocumentParser)

    with TestClient(app):
        pass  # context exit triggers the shutdown handler

    assert closed == ["emb", "ocr"]
