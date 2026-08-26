"""Contract tests for the learning-path REST routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client_with_subject(tmp_path: Path) -> tuple[TestClient, str]:
    config_dir = tmp_path / "config"
    store = LibraryStore(config_dir)
    subject = store.create_subject("Mathématiques")
    app = create_app(config_dir=config_dir)
    return TestClient(app), subject.id


def test_create_path_requires_subject_when_none_is_active(tmp_path: Path) -> None:
    app = create_app(config_dir=tmp_path / "config")
    with TestClient(app) as client:
        response = client.post(
            "/api/tutor/paths",
            json={"title": "Parcours sans sujet"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "subject_id requis"}


def test_create_and_list_path_with_explicit_subject(tmp_path: Path) -> None:
    client, subject_id = _client_with_subject(tmp_path)
    with client:
        response = client.post(
            "/api/tutor/paths",
            json={
                "subject_id": subject_id,
                "title": "Fonctions",
                "description": "Réviser les fonctions usuelles",
            },
        )
        assert response.status_code == 200
        path = response.json()
        assert path["subject_id"] == subject_id
        assert path["title"] == "Fonctions"

        listed = client.get(
            "/api/tutor/paths", params={"subject_id": subject_id}
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["paths"]] == [path["id"]]


def test_path_routes_return_404_for_unknown_subject(tmp_path: Path) -> None:
    app = create_app(config_dir=tmp_path / "config")
    with TestClient(app) as client:
        created = client.post(
            "/api/tutor/paths",
            json={"subject_id": "unknown-subject", "title": "Test"},
        )
        listed = client.get(
            "/api/tutor/paths", params={"subject_id": "unknown-subject"}
        )

    assert created.status_code == 404
    assert created.json() == {"detail": "Sujet inconnu"}
    assert listed.status_code == 404
    assert listed.json() == {"detail": "Sujet inconnu"}
