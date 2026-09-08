"""Import multi-fichiers (tutor.html uniquement, aucune nouvelle route).

100 % offline (TestClient + MockTransport). Couvre :
- câblage statique : `multiple` sur #impFile, boucle SÉQUENTIELLE dans
  importSource (pas de files[0] seul, pas de Promise.all sur les uploads),
  drop multi-fichiers, preview premier fichier + compteur « +N autres » ;
- backend : 3 imports successifs → 3 book_ids distincts (aucune route
  ajoutée : POST /api/tutor/import répété) ; doublon préservé.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


TUTOR_HTML = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ollama_tutor"
    / "web"
    / "static"
    / "tutor.html"
)


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
def client(tmp_path: Path, monkeypatch):
    dim = 4

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _func_body(html: str, start_marker: str) -> str:
    start = html.find(start_marker)
    assert start != -1, f"{start_marker} introuvable dans tutor.html"
    nxt = re.search(r"\n(async )?function \w+\(", html[start + 10 :])
    return html[start : start + 10 + nxt.start()] if nxt else html[start:]


# ---------------------------------------------------------------------------
# Câblage statique UI
# ---------------------------------------------------------------------------


def test_input_accepts_multiple_files() -> None:
    html = TUTOR_HTML.read_text(encoding="utf-8")
    m = re.search(r'<input[^>]*id="impFile"[^>]*>', html)
    assert m, "#impFile introuvable"
    assert "multiple" in m.group(0), "#impFile doit porter l'attribut multiple"


def test_import_source_loops_sequentially() -> None:
    html = TUTOR_HTML.read_text(encoding="utf-8")
    body = _func_body(html, "async function importSource() {")
    # Plus de files[0] seul : chaque fichier sélectionné est posté.
    assert "files[0]" not in body, (
        "importSource() ne doit plus ne prendre que files[0]"
    )
    # Boucle séquentielle (cible ≤ 8 Go : pas d'uploads parallèles).
    assert re.search(r"for\s*\(", body), "importSource() doit boucler sur les fichiers"
    assert 'await sf("/api/tutor/import"' in body
    assert "Promise.all" not in body, "pas de parallélisme d'upload"
    # Progression fichier i/N + résumé final.
    assert re.search(r"i\s*\+\s*1.*files\.length|files\.length", body)
    assert "doublon" in body


def test_drop_handler_takes_all_files() -> None:
    html = TUTOR_HTML.read_text(encoding="utf-8")
    body = _func_body(html, "function setupImportDragDrop() {")
    assert "files[0]" not in body, "le drop doit déposer TOUS les fichiers"
    assert "for" in body, "le drop doit itérer sur tous les fichiers déposés"


def test_preview_shows_first_file_plus_counter() -> None:
    html = TUTOR_HTML.read_text(encoding="utf-8")
    drop_body = _func_body(html, "function updateDropPreview() {")
    assert "autre" in drop_body, "l'aperçu doit afficher « +N autres »"
    auto_body = _func_body(html, "function updateAutoDetectPreview(){")
    assert "autres" in auto_body, "la preview auto doit mentionner les autres fichiers"


# ---------------------------------------------------------------------------
# Backend : imports répétés → jobs/books distincts (aucune route ajoutée)
# ---------------------------------------------------------------------------


def test_three_imports_yield_three_books(
    tmp_path: Path, client: TestClient
) -> None:
    book_ids: list[str] = []
    for name, text in (
        ("alpha.txt", "Alpha lesson on symbols. "),
        ("beta.txt", "Beta lesson on equations. "),
        ("gamma.txt", "Gamma lesson on functions. "),
    ):
        p = tmp_path / name
        p.write_text(text * 50, encoding="utf-8")
        r = client.post(
            "/api/tutor/import",
            json={"subject": "Maths", "path": str(p), "queue": True},
        )
        assert r.status_code == 200, r.text
        book_ids.append(r.json()["book_id"])
    assert len(set(book_ids)) == 3, "3 fichiers ⇒ 3 book_ids distincts"
    books = client.get("/api/tutor/books").json()["books"]
    assert all(any(b["id"] == bid for b in books) for bid in book_ids)


def test_duplicate_reimport_stays_noop(
    tmp_path: Path, client: TestClient
) -> None:
    p = tmp_path / "unique.txt"
    p.write_text("Unique content here. " * 50, encoding="utf-8")
    first = client.post(
        "/api/tutor/import", json={"subject": "Maths", "path": str(p), "queue": True}
    )
    assert first.status_code == 200
    second = client.post(
        "/api/tutor/import", json={"subject": "Maths", "path": str(p), "queue": True}
    )
    assert second.status_code == 200
    assert second.json()["book_id"] == first.json()["book_id"], (
        "le doublon doit rester un no-op (même book_id)"
    )
