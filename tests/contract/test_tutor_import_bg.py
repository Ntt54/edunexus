"""Contract tests for the Phase 6 UX import flow (background indexing).

Covers:
- multipart/form-data import regression (python-multipart present): a PDF
  upload creates the book row and indexes it end-to-end;
- immediate ``{"book_id", "status": "indexing"}`` response while heavy
  indexing continues in an asyncio background task (strong refs on
  app.state);
- post-response embedding failure ⇒ book status "error" + errors.log entry;
- GET /api/tutor/index-status exposes light "books" rows with the wire
  status vocabulary "indexing" | "ready" | "error";
- pre-flight failures still answer 4xx immediately.

Offline: TestClient + scripted httpx.MockTransport (no daemon).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


def _make_embed_transport(
    dim: int = 4,
    fail_embed: bool = False,
    gate: threading.Event | None = None,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            if gate is not None:
                while not gate.is_set():
                    await asyncio.sleep(0.01)
            if fail_embed:
                return httpx.Response(
                    500, json={"error": "embed down"}, request=request
                )
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
        super().__init__(transport=_make_embed_transport())


class GatedClient(web_server.OllamaClient):
    """Embed calls block until ``gate`` is set (deterministic mid-indexing)."""

    gate: threading.Event = threading.Event()

    def __init__(self, *a, **k):
        super().__init__(transport=_make_embed_transport(gate=type(self).gate))


class FailingEmbedClient(web_server.OllamaClient):
    def __init__(self, *a, **k):
        super().__init__(transport=_make_embed_transport(fail_embed=True))


def _minimal_pdf(path: Path, text: str = "Algebra basics for beginners") -> None:
    """Write a minimal but valid single-page PDF with extractable text."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length "
        + str(len(text) + 26).encode()
        + b">>stream\nBT /F1 12 Tf 50 50 Td ("
        + text.encode()
        + b") Tj ET\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<</Size " + str(len(objs) + 1).encode() + b"/Root 1 0 R>>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    path.write_bytes(out)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _wait_status(
    c: TestClient, book_id: str, status: str, timeout: float = 4.0
) -> list[dict]:
    deadline = time.time() + timeout
    rows: list[dict] = []
    while time.time() < deadline:
        rows = c.get("/api/tutor/index-status").json()["books"]
        row = next((r for r in rows if r["id"] == book_id), None)
        if row is not None and row["status"] == status:
            return rows
        time.sleep(0.02)
    raise AssertionError(f"book {book_id} never reached {status!r}: {rows}")


# ---------------------------------------------------------------------------
# TASK 1 — multipart import regression (the original python-multipart bug)
# ---------------------------------------------------------------------------


def test_multipart_pdf_import_creates_book(tmp_path: Path, client: TestClient) -> None:
    pdf = tmp_path / "test.pdf"
    _minimal_pdf(pdf)
    r = client.post(
        "/api/tutor/import",
        files={"file": ("test.pdf", pdf.read_bytes(), "application/pdf")},
        data={"subject": "Math"},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"book_id", "status"}
    assert body["status"] == "indexing"

    # The book row exists immediately.
    books = client.get("/api/tutor/books").json()["books"]
    assert len(books) == 1
    assert books[0]["id"] == body["book_id"]
    assert books[0]["title"] == "test"
    assert books[0]["format"] == "pdf"

    # And the PDF indexes end-to-end through the multipart path.
    rows = _wait_status(client, body["book_id"], "ready")
    assert rows[0]["title"] == "test"


def test_multipart_without_file_is_400(client: TestClient) -> None:
    body = (
        b"--boundary\r\n"
        b'Content-Disposition: form-data; name="subject"\r\n'
        b"\r\n"
        b"Math\r\n"
        b"--boundary--\r\n"
    )
    r = client.post(
        "/api/tutor/import",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# TASK 2 — immediate response + background task + failure reporting
# ---------------------------------------------------------------------------


def test_import_responds_immediately_with_indexing_shape(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(web_server, "OllamaClient", GatedClient)
    GatedClient.gate = threading.Event()  # fresh gate per test
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        src = tmp_path / "lesson.txt"
        src.write_text("La photosynthese des plantes vertes. " * 60, encoding="utf-8")
        r = c.post("/api/tutor/import", json={"subject": "SVT", "path": str(src)})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"book_id", "status"}
        assert body["status"] == "indexing"

        # The embed call is gated ⇒ the book is still indexing NOW: the row
        # was created synchronously and the response did not wait for it.
        rows = c.get("/api/tutor/index-status").json()["books"]
        assert [row["status"] for row in rows] == ["indexing"]

        GatedClient.gate.set()  # release the background run
        _wait_status(c, body["book_id"], "ready")


def test_failing_embed_marks_error_and_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_server, "OllamaClient", FailingEmbedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        src = tmp_path / "broken.txt"
        src.write_text("Texte a indexer pour echouer. " * 80, encoding="utf-8")
        r = c.post("/api/tutor/import", json={"subject": "Hist", "path": str(src)})
        assert r.status_code == 200
        book_id = r.json()["book_id"]
        # Post-response failure ⇒ status flips to "error", never raises.
        rows = _wait_status(c, book_id, "error")
        assert rows[0]["status"] == "error"
    content = (tmp_path / "config" / "errors.log").read_text(encoding="utf-8")
    assert "[tutor-index]" in content
    assert "Indexation impossible" in content


def test_index_status_books_wire_shape(tmp_path: Path, client: TestClient) -> None:
    src = tmp_path / "geo.txt"
    src.write_text("Les volcans et les plaques tectoniques. " * 60, encoding="utf-8")
    r = client.post("/api/tutor/import", json={"subject": "Geo", "path": str(src)})
    book_id = r.json()["book_id"]
    rows = _wait_status(client, book_id, "ready")

    body = client.get("/api/tutor/index-status").json()
    # Existing keys preserved.
    assert "books" in body and "indexing" in body
    assert body["indexing"] is False
    # Light wire rows with the normalized vocabulary.
    assert set(rows[0]) == {"id", "title", "status"}
    assert rows[0]["status"] == "ready"


# ---------------------------------------------------------------------------
# Pre-flight failures stay synchronous 4xx.
# ---------------------------------------------------------------------------


def test_json_missing_subject_or_path_is_400(client: TestClient) -> None:
    assert client.post("/api/tutor/import", json={"subject": "X"}).status_code == 400
    assert client.post("/api/tutor/import", json={"path": "/tmp/a.txt"}).status_code == 400


def test_missing_file_on_disk_is_400(client: TestClient) -> None:
    r = client.post(
        "/api/tutor/import",
        json={"subject": "X", "path": "/nonexistent/really.txt"},
    )
    assert r.status_code == 400


def test_unsupported_format_is_400(tmp_path: Path, client: TestClient) -> None:
    f = tmp_path / "prog.exe"
    f.write_bytes(b"MZfake")
    r = client.post(
        "/api/tutor/import", json={"subject": "X", "path": str(f)}
    )
    assert r.status_code == 400
