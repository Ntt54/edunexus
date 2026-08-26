from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


class ScriptedClient(web_server.OllamaClient):
    def __init__(self, *args, **kwargs):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content or b"{}")
            inputs = body.get("input", [])
            return httpx.Response(
                200,
                json={"embeddings": [[0.1, 0.2] for _ in inputs]},
                request=request,
            )

        super().__init__(transport=httpx.MockTransport(handler))


def test_nightly_settings_and_status_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as client:
        response = client.put(
            "/api/tutor/settings",
            json={
                "nightly_enabled": True,
                "nightly_start_at": "22:30",
                "nightly_stop_at": "06:45",
                "nightly_only_on_ac": False,
                "nightly_max_runtime_minutes": 60,
                "nightly_prepare_enabled": True,
            },
        )
        assert response.status_code == 200
        status = client.get("/api/tutor/nightly")
        assert status.status_code == 200
        data = status.json()
        assert data["enabled"] is True
        assert data["start_at"] == "22:30"
        assert data["stop_at"] == "06:45"
        assert data["prepare_enabled"] is True


def test_maintenance_api_creates_backup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as client:
        response = client.post("/api/tutor/maintenance", json={"backup": True, "vacuum": False})
        assert response.status_code == 200
        body = response.json()
        assert body["after"]["ok"] is True
        assert body["backup_path"]
        assert Path(body["backup_path"]).exists()
