"""Orphelins + VACUUM auto après suppression (backend uniquement).

100 % offline. Couvre :
- orphelin après delete domaine (livre conservé, jointure partie) ;
- `link_book_to_subject` : OK, doublon False, KeyError si inconnu ;
- VACUUM : freelist élevé ⇒ compacté, petit freelist ⇒ pas de VACUUM ;
- route POST /api/tutor/subjects/{id}/books : 200 {"linked": …} / 404.
"""

from __future__ import annotations

import json
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
def client(tmp_path: Path, monkeypatch):
    dim = 4

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_transport(dim))

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _doc(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_text("contenu pedagogique " * 30, encoding="utf-8")
    return str(p)


def _freelist_ratio(store: LibraryStore) -> float:
    free = store._conn.execute("PRAGMA freelist_count").fetchone()[0]
    total = store._conn.execute("PRAGMA page_count").fetchone()[0]
    return float(free) / float(total) if total else 0.0


# ---------------------------------------------------------------------------
# Orphelins + rattachement (store)
# ---------------------------------------------------------------------------


def test_orphan_survives_subject_delete(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Physique")
    book = store.import_document(subj.id, _doc(tmp_path, "meca.txt"))

    store.delete_subject(subj.id)

    assert store.get_subject(subj.id) is None
    assert store.get_book(book.id) is not None, "le livre doit survivre"
    assert store.get_book_subject_id(book.id) is None, "jointure partie"
    assert any(b.id == book.id for b in store.list_all_books())


def test_link_book_to_subject_ok(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Physique")
    target = store.create_subject("Chimie")
    book = store.import_document(subj.id, _doc(tmp_path, "meca.txt"))
    store.delete_subject(subj.id)
    assert store.get_book_subject_id(book.id) is None

    assert store.link_book_to_subject(book.id, target.id) is True

    assert store.get_book_subject_id(book.id) == target.id
    assert any(b.id == book.id for b in store.list_books(target.id))


def test_link_duplicate_returns_false(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Physique")
    book = store.import_document(subj.id, _doc(tmp_path, "meca.txt"))

    assert store.link_book_to_subject(book.id, subj.id) is False

    rows = store._conn.execute(
        "SELECT COUNT(*) AS c FROM subject_books WHERE book_id = ?",
        (book.id,),
    ).fetchone()["c"]
    assert rows == 1, "aucune jointure dupliquée"


def test_link_unknown_raises_keyerror(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Physique")
    book = store.import_document(subj.id, _doc(tmp_path, "meca.txt"))
    with pytest.raises(KeyError):
        store.link_book_to_subject("no-such-book", subj.id)
    with pytest.raises(KeyError):
        store.link_book_to_subject(book.id, "no-such-subject")


# ---------------------------------------------------------------------------
# VACUUM auto après suppression (store)
# ---------------------------------------------------------------------------


def _build_freelist(store: LibraryStore, n: int = 200) -> None:
    """Crée un freelist élevé : INSERT blobs puis DELETE direct (sans hook)."""
    for i in range(n):
        store._conn.execute(
            "INSERT INTO embeddings (text_hash, model, dim, vector) VALUES (?, ?, ?, ?)",
            (f"vacuum-probe-{i}", "probe-model", 4, "x" * 4000),
        )
    store._conn.commit()
    store._conn.execute("DELETE FROM embeddings WHERE model = ?", ("probe-model",))
    store._conn.commit()


def test_high_freelist_triggers_vacuum_on_delete(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subj = store.create_subject("Physique")
    book = store.import_document(subj.id, _doc(tmp_path, "meca.txt"))
    _build_freelist(store)
    assert _freelist_ratio(store) > 0.20, "le freelist doit dépasser le seuil"

    store.delete_book(book.id)

    assert store._conn.execute("PRAGMA freelist_count").fetchone()[0] == 0, (
        "VACUUM attendu : freelist compacté"
    )
    assert store.get_book(book.id) is None


def test_small_freelist_skips_vacuum(tmp_path: Path, monkeypatch) -> None:
    store = LibraryStore(tmp_path)
    calls: list[bool] = []
    real_optimize = store.optimize

    def spy(*, vacuum: bool = False):
        calls.append(vacuum)
        return real_optimize(vacuum=vacuum)

    monkeypatch.setattr(store, "optimize", spy)
    subj = store.create_subject("Physique")
    book = store.import_document(subj.id, _doc(tmp_path, "meca.txt"))
    assert _freelist_ratio(store) <= 0.20

    store.delete_book(book.id)

    assert store.get_book(book.id) is None
    assert not any(calls), "aucun VACUUM sous le seuil"


def test_maybe_vacuum_never_raises(tmp_path: Path, monkeypatch) -> None:
    store = LibraryStore(tmp_path)
    monkeypatch.setattr(
        store, "optimize", lambda *, vacuum=False: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert store.maybe_vacuum() is False


# ---------------------------------------------------------------------------
# Route thin-transport
# ---------------------------------------------------------------------------


def _import(client: TestClient, tmp_path: Path, subject: str, name: str) -> str:
    p = tmp_path / name
    # Contenu distinct par fichier : à contenu identique, le fingerprint
    # global réutiliserait le même livre (Bug #12, cross-subject reuse).
    p.write_text(f"contenu specifique {name} " * 50, encoding="utf-8")
    r = client.post(
        "/api/tutor/import",
        json={"subject": subject, "path": str(p), "queue": True},
    )
    assert r.status_code == 200, r.text
    return r.json()["book_id"]


def test_link_route_200_then_duplicate_false(
    tmp_path: Path, client: TestClient
) -> None:
    _import(client, tmp_path, "Physique", "phys.txt")
    orphan_id = _import(client, tmp_path, "Chimie", "chim.txt")
    subjects = {s["name"]: s["id"] for s in client.get("/api/tutor/subjects").json()["subjects"]}
    assert client.delete(f"/api/tutor/subjects/{subjects['Chimie']}").status_code == 200

    r = client.post(
        f"/api/tutor/subjects/{subjects['Physique']}/books",
        json={"book_id": orphan_id},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"linked": True}

    # Orphelin visible dans le domaine cible.
    books = client.get("/api/tutor/books?subject=Physique").json()["books"]
    assert any(b["id"] == orphan_id for b in books)

    r2 = client.post(
        f"/api/tutor/subjects/{subjects['Physique']}/books",
        json={"book_id": orphan_id},
    )
    assert r2.status_code == 200
    assert r2.json() == {"linked": False}


def test_link_route_404(tmp_path: Path, client: TestClient) -> None:
    book_id = _import(client, tmp_path, "Physique", "phys.txt")
    subjects = client.get("/api/tutor/subjects").json()["subjects"]
    sid = subjects[0]["id"]

    r = client.post(f"/api/tutor/subjects/{sid}/books", json={"book_id": "nope"})
    assert r.status_code == 404, r.text
    r = client.post("/api/tutor/subjects/nope/books", json={"book_id": book_id})
    assert r.status_code == 404, r.text
