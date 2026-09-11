"""Routage de la génération par modèle (plus de client global unique).

Bug prod : fournisseur cloud actif + modèle Ollama sélectionné
(`gemma4:e2b-t3`) ⇒ 400 passerelle « Unable to determine provider ».
Le choix du MODÈLE tranche à l'appel : nom dans la liste Ollama ⇒
client Ollama (préfixe `openai/` toléré/strippé), sinon ⇒ cloud
configuré, avec repli sur l'autre en cas d'insalubre. Le switch de
fournisseur reste le défaut (listes inconnues).

100 % offline (transports mockés : faux backends `chat_stream`).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.providers.routing import (
    LLMRoutingError,
    RoutingLLMClient,
)
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


class FakeBackend:
    """Faux backend `chat_stream` + `list_models` (offline)."""

    def __init__(
        self,
        tag: str,
        models: list[str] | None = None,
        fail_chat: bool = False,
        fail_list: bool = False,
    ) -> None:
        self.tag = tag
        self._models = list(models or [])
        self.fail_chat = fail_chat
        self.fail_list = fail_list
        self.calls: list = []
        self.closed = False

    async def list_models(self):
        if self.fail_list:
            raise RuntimeError(f"{self.tag} list down")
        return [SimpleNamespace(name=m) for m in self._models]

    async def chat_stream(self, messages, model=None, **kwargs):
        self.calls.append(model)
        if self.fail_chat:
            raise RuntimeError(f"{self.tag} down")
        yield SimpleNamespace(kind="content", text=f"[{self.tag}:{model}]")

    async def close(self) -> None:
        self.closed = True


def _router(**kwargs) -> RoutingLLMClient:
    ollama = FakeBackend("ollama", models=["gemma4:e2b-t3", "llama3.2"])
    cloud = FakeBackend("cloud", models=["gpt-4o-mini"])
    kwargs.setdefault("ollama_client", ollama)
    kwargs.setdefault("cloud_client", cloud)
    return RoutingLLMClient(**kwargs)


async def _collect(gen):
    return "".join([ev.text async for ev in gen])


@pytest.mark.asyncio
async def test_ollama_model_hits_ollama() -> None:
    router = _router()
    await router.refresh_models()
    text = await _collect(router.chat_stream([], "gemma4:e2b-t3"))
    assert text == "[ollama:gemma4:e2b-t3]"
    assert router.cloud_client.calls == []


@pytest.mark.asyncio
async def test_cloud_model_hits_gateway() -> None:
    router = _router()
    await router.refresh_models()
    text = await _collect(router.chat_stream([], "gpt-4o-mini"))
    assert text == "[cloud:gpt-4o-mini]"
    assert router.ollama_client.calls == []


@pytest.mark.asyncio
async def test_openai_prefix_tolerated_for_ollama() -> None:
    router = _router()
    await router.refresh_models()
    text = await _collect(router.chat_stream([], "openai/llama3.2"))
    # Strippé avant envoi Ollama (jamais inventé : que du strip).
    assert text == "[ollama:llama3.2]"
    assert router.ollama_client.calls == ["llama3.2"]


@pytest.mark.asyncio
async def test_cloud_failure_falls_back_to_ollama() -> None:
    ollama = FakeBackend("ollama", models=["gemma4:e2b-t3"])
    cloud = FakeBackend("cloud", models=["gpt-4o-mini"], fail_chat=True)
    router = RoutingLLMClient(ollama_client=ollama, cloud_client=cloud)
    await router.refresh_models()
    text = await _collect(router.chat_stream([], "gpt-4o-mini"))
    assert text.startswith("[ollama:")
    assert cloud.calls == ["gpt-4o-mini"]  # primaire tenté d'abord


@pytest.mark.asyncio
async def test_ollama_failure_falls_back_to_cloud() -> None:
    ollama = FakeBackend("ollama", models=["gemma4:e2b-t3"], fail_chat=True)
    cloud = FakeBackend("cloud", models=["gpt-4o-mini"])
    router = RoutingLLMClient(ollama_client=ollama, cloud_client=cloud)
    await router.refresh_models()
    text = await _collect(router.chat_stream([], "gemma4:e2b-t3"))
    assert text.startswith("[cloud:")
    assert ollama.calls == ["gemma4:e2b-t3"]


@pytest.mark.asyncio
async def test_unknown_model_explicit_error() -> None:
    router = _router()
    await router.refresh_models()
    with pytest.raises(LLMRoutingError, match="modele-inexistant-xyz"):
        await _collect(router.chat_stream([], "modele-inexistant-xyz"))
    assert router.ollama_client.calls == []
    assert router.cloud_client.calls == []


@pytest.mark.asyncio
async def test_no_catalog_no_cloud_legacy_ollama() -> None:
    ollama = FakeBackend("ollama", models=["gemma4:e2b-t3"], fail_list=True)
    router = RoutingLLMClient(ollama_client=ollama, cloud_client=None)
    await router.refresh_models()  # échec silencieux, cache vide
    text = await _collect(router.chat_stream([], "nimporte-quoi"))
    assert text.startswith("[ollama:")


def test_client_for_contract() -> None:
    import asyncio

    router = _router()
    asyncio.run(router.refresh_models())
    cli, eff, backend = router.client_for("GEMMA4:E2B-T3")
    assert backend == "ollama"
    assert eff == "GEMMA4:E2B-T3"
    assert cli is router.ollama_client
    cli, eff, backend = router.client_for("gpt-4o-mini")
    assert backend == "cloud" and cli is router.cloud_client


@pytest.mark.asyncio
async def test_close_only_cloud() -> None:
    router = _router()
    await router.close()
    assert router.cloud_client.closed is True
    assert router.ollama_client.closed is False


# ---------------------------------------------------------------------------
# Câblage service : quiz + leçons suivent le même routage
# ---------------------------------------------------------------------------


def _service(tmp_path: Path, ollama: FakeBackend, cloud=None) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    svc = TutorService(store, ollama, config)
    if cloud is not None:
        # Cloud branché après coup (comme le PUT switch) pour ce test.
        from src.ollama_tutor.tutor.providers.routing import RoutingLLMClient as R

        assert isinstance(svc._llm_client, R)
        svc._llm_client._cloud = cloud
        svc._llm_client.default = "cloud"
    return svc


def test_service_wires_router_for_quiz(tmp_path: Path) -> None:
    ollama = FakeBackend("ollama", models=["gemma4:e2b-t3"])
    svc = _service(tmp_path, ollama)
    assert isinstance(svc._llm_client, RoutingLLMClient)
    assert svc.quiz_engine.client is svc._llm_client
    assert svc._llm_client._ollama is ollama
    # Embeddings intacts : client Ollama direct, pas le routeur.
    assert svc.client is ollama


@pytest.mark.asyncio
async def test_quiz_and_lessons_follow_routing(tmp_path: Path) -> None:
    ollama = FakeBackend("ollama", models=["gemma4:e2b-t3"])
    cloud = FakeBackend("cloud", models=["gpt-4o-mini"])
    svc = _service(tmp_path, ollama, cloud)
    router = svc._llm_client
    await router.refresh_models()

    # Quiz (assessment) via le routeur : modèle Ollama ⇒ Ollama.
    assert svc.quiz_engine.client is router
    out = await _collect(
        svc.quiz_engine.client.chat_stream([], "gemma4:e2b-t3")
    )
    assert out == "[ollama:gemma4:e2b-t3]"

    # Leçon streaming via le routeur : modèle cloud ⇒ passerelle.
    svc.config.tutor_model = "gpt-4o-mini"
    text = "".join(
        [ev async for ev in svc.stream_lesson_text("lesson_summary", "notion", ["extrait"])]
    )
    assert "[cloud:gpt-4o-mini]" in text
