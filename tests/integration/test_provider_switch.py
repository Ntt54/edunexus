"""Reconstruction à chaud du client LLM au changement de fournisseur.

Cause : `tutor_client` construit uniquement à `create_app` — le PUT
settings persistait sans reconstruire, la génération continuait sur
l'ancien backend jusqu'au redémarrage.

100 % offline (helper mocké, génération prouvée via un faux client
rebranché ; aucun appel réseau).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.tutor.store import LibraryStore


class FakeLLMClient:
    """Faux client LLM enregistreur (chat_stream protocol)."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.calls: list = []
        self.closed = False

    async def chat_stream(self, messages, model, options=None):
        self.calls.append({"model": model, "n_messages": len(messages)})
        yield SimpleNamespace(kind="content", text=f"[{self.tag}] génération")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def app_client(tmp_path: Path):
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield app, c


def _service(app):
    return app.state.tutor_service


def _mock_builder(monkeypatch, calls: list, tag: str = "openai-mock"):
    def fake_build(config):
        calls.append(
            (config.llm_provider, config.llm_base_url, config.llm_api_key)
        )
        return FakeLLMClient(tag)

    monkeypatch.setattr(web_server, "_build_tutor_client", fake_build)


def test_switch_ollama_to_openai_rewires_generation(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    app, c = app_client
    svc = _service(app)
    old_llm = svc._llm_client
    old_embed = svc.client
    calls: list = []
    _mock_builder(monkeypatch, calls)

    r = c.put(
        "/api/tutor/settings",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
        },
    )
    assert r.status_code == 200, r.text
    assert len(calls) == 1  # une seule construction
    assert svc._llm_client is not old_llm
    # Routage par modèle : le rebranché est un routeur dont la jambe cloud
    # est le faux construit (scénario inchangé, enveloppe routeur).
    from src.ollama_tutor.tutor.providers.routing import RoutingLLMClient

    assert isinstance(svc._llm_client, RoutingLLMClient)
    assert isinstance(svc._llm_client.cloud_client, FakeLLMClient)
    assert svc.quiz_engine.client is svc._llm_client
    # Embeddings intacts : même client Ollama conservé.
    assert svc.client is old_embed
    # L'ancien routeur n'est pas fermé (son cloud était None/partagé).
    assert getattr(old_llm, "closed", False) is False
    # Réponse du PUT inchangée (mêmes clés settings).
    assert "tutor" in r.json()

    # Preuve : la génération suivante passe par le nouveau client.
    fake = svc._llm_client.cloud_client
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("SVT")
    p = tmp_path / "cours.txt"
    p.write_text("La photosynthèse convertit la lumière. " * 40, encoding="utf-8")
    book = store.import_document(subj.id, str(p))
    store.add_chunks(
        subj.id, book.id, ["La photosynthèse convertit la lumière."],
        [[0.1, 0.2, 0.3, 0.4]], model="test-model",
    )
    summary = asyncio.run(svc.summarize_book(book.id))
    assert "[openai-mock] génération" in summary["summary"]
    assert len(fake.calls) == 1


def test_switch_back_openai_to_ollama_closes_old(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    from src.ollama_tutor.client import OllamaClient as RealOllama

    app, c = app_client
    svc = _service(app)
    calls: list = []
    _mock_builder(monkeypatch, calls, tag="cloud")
    c.put(
        "/api/tutor/settings",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
        },
    )
    from src.ollama_tutor.tutor.providers.routing import RoutingLLMClient

    fake = svc._llm_client.cloud_client
    assert isinstance(fake, FakeLLMClient)
    assert fake.closed is False
    calls.clear()
    monkeypatch.undo()  # helper réel pour le retour
    r = c.put("/api/tutor/settings", json={"llm_provider": "ollama"})
    assert r.status_code == 200, r.text
    # Retour vers Ollama : nouveau routeur, défaut ollama, sans jambe cloud.
    assert isinstance(svc._llm_client, RoutingLLMClient)
    assert svc._llm_client.cloud_client is None
    assert svc._llm_client.default == "ollama"
    assert isinstance(svc._llm_client.ollama_client, RealOllama)
    assert svc.quiz_engine.client is svc._llm_client
    assert fake.closed is True, "ancien provider dédié fermé best-effort"
    assert svc.client is not None  # embeddings intacts


def test_invalid_url_400_before_any_change(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    app, c = app_client
    svc = _service(app)
    old_llm = svc._llm_client
    calls: list = []
    _mock_builder(monkeypatch, calls)
    for bad in ("pas-une-url", "ftp://hote/v1", "http://"):
        r = c.put(
            "/api/tutor/settings",
            json={"llm_provider": "openai", "llm_base_url": bad},
        )
        assert r.status_code == 400, (bad, r.text)
    assert calls == [], "aucune construction sur URL invalide"
    assert svc._llm_client is old_llm, "ancien client gardé"
    assert c.get("/api/tutor/settings").json()["tutor"]["llm_provider"] == "ollama"


def test_masked_or_empty_key_kept_no_rebuild(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    app, c = app_client
    svc = _service(app)
    calls: list = []
    _mock_builder(monkeypatch, calls, tag="cloud")
    c.put(
        "/api/tutor/settings",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
        },
    )
    assert len(calls) == 1
    fake = svc._llm_client.cloud_client
    masked = c.get("/api/tutor/settings").json()["tutor"]["llm_api_key_masked"]

    r = c.put("/api/tutor/settings", json={"llm_api_key": ""})
    assert r.status_code == 200, r.text
    r = c.put("/api/tutor/settings", json={"llm_api_key": masked})
    assert r.status_code == 200, r.text
    data = c.get("/api/tutor/settings").json()["tutor"]
    assert data["has_llm_api_key"] is True
    assert data["llm_api_key_masked"] == masked, "clé conservée"
    assert len(calls) == 1, "aucune reconstruction sans changement"
    assert svc._llm_client.cloud_client is fake


def test_no_change_no_rebuild(tmp_path: Path, app_client, monkeypatch) -> None:
    app, c = app_client
    svc = _service(app)
    old_llm = svc._llm_client
    calls: list = []
    _mock_builder(monkeypatch, calls)
    r = c.put("/api/tutor/settings", json={"think": True})
    assert r.status_code == 200, r.text
    r = c.put("/api/tutor/settings", json={"llm_provider": "ollama"})
    assert r.status_code == 200, r.text
    assert calls == []
    assert svc._llm_client is old_llm


def test_key_change_rebuilds(tmp_path: Path, app_client, monkeypatch) -> None:
    app, c = app_client
    svc = _service(app)
    calls: list = []
    _mock_builder(monkeypatch, calls, tag="cloud")
    c.put(
        "/api/tutor/settings",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-one",
        },
    )
    first_cloud = svc._llm_client.cloud_client
    r = c.put("/api/tutor/settings", json={"llm_api_key": "sk-two"})
    assert r.status_code == 200, r.text
    assert len(calls) == 2
    assert svc._llm_client.cloud_client is not first_cloud
    assert isinstance(svc._llm_client.cloud_client, FakeLLMClient)
    assert first_cloud.closed is True


# ---------------------------------------------------------------------------
# PUT /api/tutor/models : la sélection d'un modèle auto-bascule le provider
# (« avoir les deux fournisseurs »). 100 % offline : listages mockés via
# les seams existants (OllamaClient de module + sonde OpenAICompatProvider),
# rebuild observé via le seam _build_tutor_client.
# ---------------------------------------------------------------------------

LOCAL_MODELS = ["hf.co/local-model", "gemma3:1b"]
CLOUD_MODELS = ["cloud-model-x"]


class _ListingOllama(web_server.OllamaClient):
    """OllamaClient mocké : listage scripté, jamais de réseau."""

    NAMES: list = []

    def __init__(self, *a, **k):
        pass

    async def list_models(self) -> list:
        from types import SimpleNamespace as _NS

        return [_NS(name=n) for n in self.NAMES]

    async def close(self):
        pass


class _ListingProbe:
    """Sonde OpenAICompatProvider mockée : listage scripté, jamais de réseau."""

    NAMES: list = []

    def __init__(self, base_url=None, api_key=None):
        self.base_url = base_url
        self.api_key = api_key

    async def list_models(self):
        from types import SimpleNamespace as _NS

        return [_NS(name=n) for n in self.NAMES]

    async def close(self):
        pass


def _mock_listings(monkeypatch, ollama_names, cloud_names) -> None:
    _ListingOllama.NAMES = list(ollama_names)
    _ListingProbe.NAMES = list(cloud_names)
    monkeypatch.setattr(web_server, "OllamaClient", _ListingOllama)
    import src.ollama_tutor.tutor.providers.openai_compat as compat_mod

    monkeypatch.setattr(compat_mod, "OpenAICompatProvider", _ListingProbe)


def _provider_of(c) -> str:
    return c.get("/api/tutor/settings").json()["tutor"]["llm_provider"]


def _switch_to_openai(c) -> None:
    r = c.put(
        "/api/tutor/settings",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
        },
    )
    assert r.status_code == 200, r.text
    assert _provider_of(c) == "openai"


def test_models_put_local_model_switches_openai_to_ollama(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    """Le bug : PUT {"llm": <modèle local>} sur provider openai ⇒ ollama."""
    app, c = app_client
    svc = _service(app)
    _mock_listings(monkeypatch, LOCAL_MODELS, CLOUD_MODELS)
    calls: list = []
    _mock_builder(monkeypatch, calls, tag="openai-mock")
    _switch_to_openai(c)
    old_llm = svc._llm_client
    calls.clear()

    r = c.put("/api/tutor/models", json={"llm": "hf.co/local-model"})
    assert r.status_code == 200, r.text
    assert r.json()["current"]["llm"] == "hf.co/local-model"
    assert _provider_of(c) == "ollama", "auto-bascule vers la source du modèle"
    assert len(calls) == 1 and calls[0][0] == "ollama", "rebuild à chaud"
    assert svc._llm_client is not old_llm, "service rebranché"


def test_models_put_cloud_model_resolves_openai(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    """Modèle cloud (listé quand provider==openai) ⇒ provider openai confirmé.

    Triplet inchangé ⇒ aucune reconstruction (helper partagé).
    """
    app, c = app_client
    _mock_listings(monkeypatch, LOCAL_MODELS, CLOUD_MODELS)
    calls: list = []
    _mock_builder(monkeypatch, calls, tag="openai-mock")
    _switch_to_openai(c)  # rend le cloud visible, comme la dropdown UI
    calls.clear()

    r = c.put("/api/tutor/models", json={"llm": "cloud-model-x"})
    assert r.status_code == 200, r.text
    assert r.json()["current"]["llm"] == "cloud-model-x"
    assert _provider_of(c) == "openai"
    assert calls == [], "triplet inchangé ⇒ pas de rebuild"


def test_models_put_duplicate_name_prefers_ollama(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    """Doublon dans les deux listages ⇒ priorité Ollama (comme le GET)."""
    app, c = app_client
    svc = _service(app)
    _mock_listings(monkeypatch, ["dup-model"], ["dup-model"])
    calls: list = []
    _mock_builder(monkeypatch, calls, tag="openai-mock")
    _switch_to_openai(c)
    old_llm = svc._llm_client
    calls.clear()

    r = c.put("/api/tutor/models", json={"llm": "dup-model"})
    assert r.status_code == 200, r.text
    assert _provider_of(c) == "ollama"
    assert len(calls) == 1 and calls[0][0] == "ollama"
    assert svc._llm_client is not old_llm


def test_models_put_unknown_model_keeps_provider_persists_name(
    tmp_path: Path, app_client, monkeypatch
) -> None:
    """Listages vides/offline : provider inchangé, nom persisté, pas de 400."""
    app, c = app_client
    svc = _service(app)
    _mock_listings(monkeypatch, [], [])
    calls: list = []
    _mock_builder(monkeypatch, calls)
    old_llm = svc._llm_client
    assert _provider_of(c) == "ollama"  # défaut frais

    r = c.put("/api/tutor/models", json={"llm": "mystery-model"})
    assert r.status_code == 200, r.text
    assert r.json()["current"]["llm"] == "mystery-model"
    assert _provider_of(c) == "ollama", "modèle inconnu ⇒ provider inchangé"
    assert calls == [], "aucune reconstruction"
    assert svc._llm_client is old_llm
