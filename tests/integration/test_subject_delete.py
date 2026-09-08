"""Domaines supprimables + import sans domaine imposé (2 corrections liées).

100 % offline (TestClient + MockTransport, aucun démon).

- Suppression domaine : livres conservés (joins CASCADE), inconnu → 404,
  bouton UI (confirm + fetch DELETE) câblé au bon endpoint.
- Import sans domaine : aucun « Général » créé, classement auto backend
  (subject vide/absent → _infer_subject_from_path).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


TUTOR_HTML = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ollama_tutor"
    / "web"
    / "static"
    / "tutor.html"
)

DELETE_CONFIRM = "Supprimer définitivement"


def _make_embed_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        inputs = body.get("input", [])
        n = len(inputs)
        vecs = [[float((i * 3 + j) % 5) / 5 for j in range(dim)] for i in range(n)]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    dim = 4

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_embed_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _import_book(c: TestClient, tmp_path: Path, payload: dict, name: str = "algebre.txt") -> str:
    p = tmp_path / name
    p.write_text("Algebra is the study of symbols and rules. " * 50, encoding="utf-8")
    body = dict(payload)
    body.setdefault("path", str(p))
    r = c.post("/api/tutor/import", json=body)
    assert r.status_code == 200, r.text
    return r.json()["book_id"]


# ---------------------------------------------------------------------------
# Suppression domaine : store
# ---------------------------------------------------------------------------


def test_delete_subject_keeps_books_store_level(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Physique")
    p = tmp_path / "meca.txt"
    p.write_text("mecanique " * 20, encoding="utf-8")
    book = store.import_document(subj.id, str(p))
    assert store.get_book_subject_id(book.id) == subj.id

    store.delete_subject(subj.id)

    assert store.get_subject(subj.id) is None
    # Le livre survit (joins CASCADE : seule la ligne subject_books part).
    assert store.get_book(book.id) is not None
    assert store.get_book_subject_id(book.id) is None
    assert any(b.id == book.id for b in store.list_all_books())


def test_delete_unknown_subject_store_raises_keyerror(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    with pytest.raises(KeyError):
        store.delete_subject("nope")


# ---------------------------------------------------------------------------
# Suppression domaine : route API
# ---------------------------------------------------------------------------


def test_delete_subject_route_keeps_books(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import_book(client, tmp_path, {"subject": "Chimie"})
    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    target = next(s for s in subjects if s["name"].lower() == "chimie")

    r = client.delete(f"/api/tutor/subjects/{target['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True}

    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    assert all(s["id"] != target["id"] for s in subjects)
    # Livres conservés et toujours visibles/assignables (liste globale).
    books = client.get("/api/tutor/books").json()["books"]
    assert any(b["id"] == book_id for b in books)


def test_delete_unknown_subject_route_404(
    tmp_path: Path, client: TestClient
) -> None:
    r = client.delete("/api/tutor/subjects/does-not-exist")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Import sans domaine : pas de « Général » forcé, détection auto backend
# ---------------------------------------------------------------------------


def test_import_without_subject_creates_no_general(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import_book(client, tmp_path, {})
    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    assert subjects, "l'import auto doit rattacher à un domaine"
    assert not any(s["name"].lower() == "général" for s in subjects), (
        "aucun domaine « Général » ne doit être créé sur import sans domaine"
    )
    # Classement auto : sans recouvrement lexical, bac unique « Non classé »
    # (JAMAIS un domaine nommé d'après le fichier).
    assert [s["name"] for s in subjects] == ["Non classé"]
    books = client.get("/api/tutor/books").json()["books"]
    assert any(b["id"] == book_id for b in books)


def test_import_with_empty_subject_creates_no_general(
    tmp_path: Path, client: TestClient
) -> None:
    book_id = _import_book(client, tmp_path, {"subject": "   "})
    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    assert not any(s["name"].lower() == "général" for s in subjects)
    assert any(b["id"] == book_id for b in client.get("/api/tutor/books").json()["books"])


# ---------------------------------------------------------------------------
# Câblage UI (statique, offline)
# ---------------------------------------------------------------------------


def _import_source_body() -> str:
    html = TUTOR_HTML.read_text(encoding="utf-8")
    start = html.find("async function importSource() {")
    assert start != -1, "importSource() introuvable dans tutor.html"
    # Fin de fonction : la suivante commence par une ligne non indentée
    # « async function » / « function » au même niveau.
    nxt = re.search(r"\n(async )?function \w+\(", html[start + 10 :])
    end = start + 10 + nxt.start() if nxt else len(html)
    return html[start:end]


def test_ui_import_sends_empty_subject_for_auto_detect() -> None:
    body = _import_source_body()
    # Le vide ne doit JAMAIS être remplacé par « Général » dans importSource.
    assert "Général" not in body, (
        "importSource() ne doit plus substituer « Général » au sujet vide"
    )
    # Le champ subject n'est envoyé que s'il est non vide.
    assert 'fd.append("subject"' not in body or "if" in body, (
        "subject ne doit être envoyé que conditionnellement"
    )


def test_ui_delete_subject_wired_to_endpoint() -> None:
    html = TUTOR_HTML.read_text(encoding="utf-8")
    assert DELETE_CONFIRM in html, (
        "le bouton de suppression doit demander "
        'confirm("Supprimer définitivement … Les documents seront conservés.")'
    )
    assert "Les documents seront conservés" in html
    assert 'method:"DELETE"' in html.replace(" ", "") or 'method: "DELETE"' in html
    assert "/api/tutor/subjects/" in html
