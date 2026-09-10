"""Remplacement des catégories d'un livre en un appel.

`PUT /api/tutor/books/{id}/categories` n'acceptait que
`{"category_id": int}` (ajout unitaire) alors que le client Vue envoie
`{"category_ids": [...]}` (remplacement) ⇒ 422. Désormais les deux corps
sont acceptés : `category_ids` présent ⇒ REMPLACE (une transaction),
sinon ajout unitaire inchangé. 100 % offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def client(tmp_path: Path):
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def book(tmp_path: Path, client: TestClient) -> str:
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("SVT")
    p = tmp_path / "cours.txt"
    p.write_text("contenu categories " * 20, encoding="utf-8")
    return store.import_document(subj.id, str(p)).id


@pytest.fixture
def cats(tmp_path: Path, client: TestClient) -> dict[str, int]:
    store = LibraryStore(tmp_path / "config")
    return {
        name: store.create_category(name)["id"] for name in ("Alpha", "Beta", "Gamma")
    }


def _cat_names(client: TestClient, book_id: str) -> list[str]:
    r = client.get(f"/api/tutor/books/{book_id}/categories")
    assert r.status_code == 200, r.text
    return sorted(c["name"] for c in r.json()["categories"])


def test_replace_two_with_one(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    assert client.put(
        f"/api/tutor/books/{book}/categories", json={"category_id": cats["Alpha"]}
    ).json() == {"added": True}
    assert client.put(
        f"/api/tutor/books/{book}/categories", json={"category_id": cats["Beta"]}
    ).json() == {"added": True}
    assert _cat_names(client, book) == ["Alpha", "Beta"]

    r = client.put(
        f"/api/tutor/books/{book}/categories", json={"category_ids": [cats["Gamma"]]}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"replaced": True}
    assert _cat_names(client, book) == ["Gamma"]


def test_replace_empty_removes_all(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    client.put(f"/api/tutor/books/{book}/categories", json={"category_id": cats["Alpha"]})
    r = client.put(f"/api/tutor/books/{book}/categories", json={"category_ids": []})
    assert r.status_code == 200, r.text
    assert r.json() == {"replaced": True}
    assert _cat_names(client, book) == []


def test_replace_idempotent(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    client.put(
        f"/api/tutor/books/{book}/categories", json={"category_ids": [cats["Alpha"]]}
    )
    r = client.put(
        f"/api/tutor/books/{book}/categories", json={"category_ids": [cats["Alpha"]]}
    )
    assert r.json() == {"replaced": False}
    assert _cat_names(client, book) == ["Alpha"]


def test_single_add_unchanged(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    r = client.put(
        f"/api/tutor/books/{book}/categories", json={"category_id": cats["Alpha"]}
    )
    assert r.status_code == 200
    assert r.json() == {"added": True}
    r = client.put(
        f"/api/tutor/books/{book}/categories", json={"category_id": cats["Alpha"]}
    )
    assert r.json() == {"added": False}
    assert _cat_names(client, book) == ["Alpha"]


def test_replace_unknown_404(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    assert client.put(
        "/api/tutor/books/inconnu/categories", json={"category_ids": [cats["Alpha"]]}
    ).status_code == 404
    assert client.put(
        f"/api/tutor/books/{book}/categories", json={"category_ids": [999999]}
    ).status_code == 404
    assert client.put(
        "/api/tutor/books/inconnu/categories", json={"category_id": cats["Alpha"]}
    ).status_code == 404
    # Rien d'écrit sur le 404.
    assert _cat_names(client, book) == []


def test_empty_body_400(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    r = client.put(f"/api/tutor/books/{book}/categories", json={})
    assert r.status_code == 400, r.text


def test_replace_keeps_book_and_categories(
    tmp_path: Path, client: TestClient, book: str, cats: dict[str, int]
) -> None:
    client.put(
        f"/api/tutor/books/{book}/categories", json={"category_ids": [cats["Alpha"], cats["Beta"]]}
    )
    client.put(
        f"/api/tutor/books/{book}/categories", json={"category_ids": [cats["Gamma"]]}
    )
    store = LibraryStore(tmp_path / "config")
    assert store.get_book(book) is not None
    names = sorted(c["name"] for c in store.list_categories())
    assert names == ["Alpha", "Beta", "Gamma"]
