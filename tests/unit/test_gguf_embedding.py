"""Unit tests for the GGUF embedding provider (Phase 2).

Fully OFFLINE: the server manager is a fake (records start calls, returns a
handle with a fixed base_url) and HTTP traffic goes through an
``httpx.MockTransport`` injected via the provider's ``transport`` seam.
No subprocess is ever spawned and no real socket is opened.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.providers.gguf_embedding import (
    GGUFEmbeddingError,
    GGUFEmbeddingProvider,
    create_embedding_provider,
)
from src.ollama_tutor.tutor.providers.llama_server import LlamaServerConfig
from src.ollama_tutor.tutor.providers.ollama_adapter import OllamaEmbeddingProvider

BASE_URL = "http://127.0.0.1:9911"


# ---------------------------------------------------------------------------
# Offline doubles
# ---------------------------------------------------------------------------


class FakeManagedServer:
    """Duck-typed ManagedLlamaServer: just a base_url + stop recorder."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class FakeManager:
    """Duck-typed LlamaServerManager that never spawns anything."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.start_calls: list[LlamaServerConfig] = []
        self.server = FakeManagedServer(base_url)

    async def start(self, config: LlamaServerConfig) -> FakeManagedServer:
        self.start_calls.append(config)
        return self.server


def embeddings_payload(vectors: list[list[float]]) -> dict:
    return {"data": [{"embedding": vec} for vec in vectors]}


def recording_handler(calls: list[httpx.Request], vectors: list[list[float]]):
    """Handler appending each request to ``calls`` and serving ``vectors``."""

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=embeddings_payload(vectors))

    return handle


def make_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 1. Construction starts NO server (laziness contract).
# ---------------------------------------------------------------------------


def test_construction_starts_no_server() -> None:
    manager = FakeManager()
    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/local/bin/llama-server",
        model_path="/models/granite-embedding.gguf",
    )
    assert manager.start_calls == []
    assert provider.dims is None
    assert provider.model_name == "granite-embedding"


# ---------------------------------------------------------------------------
# 2. First embed starts server exactly once, posts {"input": [...]} to
#    /v1/embeddings, and returns ordered vectors.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_embed_starts_server_and_posts_input() -> None:
    manager = FakeManager()
    calls: list[httpx.Request] = []
    sent_batches: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        sent_batches.append(json.loads(request.content)["input"])
        return httpx.Response(
            200,
            json=embeddings_payload([[0.5, 0.6], [0.7, 0.8], [0.9, 1.0]]),
        )

    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/emb.gguf",
        extra_args=["-t", "4"],
        timeout_s=30,
        transport=make_transport(handle),
    )

    texts = ["alpha", "beta", "gamma"]
    vectors = await provider.embed(texts)

    # Server started exactly once with the expected launch config.
    assert len(manager.start_calls) == 1
    cfg = manager.start_calls[0]
    assert cfg.binary_path == "/usr/bin/llama-server"
    assert cfg.model_path == "/models/emb.gguf"
    assert cfg.extra_args == ["-t", "4"]
    assert cfg.startup_timeout_s == 30

    # Exactly one POST to /v1/embeddings carrying {"input": texts}.
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/v1/embeddings"
    assert f"{BASE_URL}/v1/embeddings" == str(calls[0].url)
    assert sent_batches == [texts]

    # Order preserved.
    assert vectors == [[0.5, 0.6], [0.7, 0.8], [0.9, 1.0]]

    await provider.aclose()


# ---------------------------------------------------------------------------
# 3. dims auto-detected from first vector length; None before any embed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dims_auto_detected_from_first_vector() -> None:
    manager = FakeManager()
    calls: list[httpx.Request] = []
    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/emb.gguf",
        transport=make_transport(
            recording_handler(calls, [[1.0] * 768])
        ),
    )

    assert provider.dims is None
    await provider.embed(["hello"])
    assert provider.dims == 768

    await provider.aclose()


# ---------------------------------------------------------------------------
# 4. Second embed reuses the server (start called exactly once).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_embed_reuses_server() -> None:
    manager = FakeManager()
    calls: list[httpx.Request] = []
    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/emb.gguf",
        transport=make_transport(
            recording_handler(calls, [[0.1, 0.2]])
        ),
    )

    await provider.embed(["first"])
    await provider.embed(["second"])

    assert len(manager.start_calls) == 1
    assert len(calls) == 2

    await provider.aclose()


# ---------------------------------------------------------------------------
# 5. data-length mismatch raises GGUFEmbeddingError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_length_mismatch_raises() -> None:
    manager = FakeManager()
    calls: list[httpx.Request] = []
    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/emb.gguf",
        transport=make_transport(
            recording_handler(calls, [[0.1, 0.2]])  # 1 vector for 3 inputs
        ),
    )

    with pytest.raises(GGUFEmbeddingError):
        await provider.embed(["a", "b", "c"])

    await provider.aclose()


# ---------------------------------------------------------------------------
# 6. Connection failure triggers exactly ONE restart, then succeeds.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_failure_restarts_once_then_succeeds() -> None:
    manager = FakeManager()
    state = {"requests": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        if state["requests"] == 1:
            raise httpx.ConnectError("server died", request=request)
        return httpx.Response(200, json=embeddings_payload([[0.9, 0.8]]))

    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/emb.gguf",
        transport=make_transport(handle),
    )

    vectors = await provider.embed(["recover"])

    # Exactly one restart: two start() calls total, old handle stopped once.
    assert len(manager.start_calls) == 2
    assert manager.server.stop_calls == 1
    assert state["requests"] == 2
    assert vectors == [[0.9, 0.8]]
    assert provider.dims == 2

    await provider.aclose()


@pytest.mark.asyncio
async def test_persistent_failure_raises_after_single_restart() -> None:
    manager = FakeManager()

    def always_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("still dead", request=request)

    provider = GGUFEmbeddingProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/emb.gguf",
        transport=make_transport(always_down),
    )

    with pytest.raises(httpx.ConnectError):
        await provider.embed(["doomed"])

    # One restart only — no infinite retry loop.
    assert len(manager.start_calls) == 2

    await provider.aclose()


# ---------------------------------------------------------------------------
# 7. Factory selection: Ollama fallback vs GGUF (absolute + relative join),
#    never starting a server at construction time.
# ---------------------------------------------------------------------------


def _fresh_config(tmp_path: Path) -> Config:
    return Config(config_dir=tmp_path / "cfg")


def test_factory_returns_ollama_provider_when_keys_empty(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    provider = create_embedding_provider(config)
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.model_name == "embeddinggemma"  # Config default.


def test_factory_returns_gguf_provider_absolute_model_path(tmp_path: Path) -> None:
    import src.ollama_tutor.tutor.providers.gguf_embedding as gguf_module

    config = _fresh_config(tmp_path)
    gguf_abs = tmp_path / "granite-embedding.gguf"
    config.tutor_llama_bin = "/opt/llama.cpp/llama-server"
    config.tutor_embed_gguf = str(gguf_abs)

    fake_manager = FakeManager()
    original = gguf_module._default_manager
    gguf_module._default_manager = fake_manager
    try:
        provider = create_embedding_provider(config)
    finally:
        gguf_module._default_manager = original

    assert isinstance(provider, GGUFEmbeddingProvider)
    assert provider.model_name == "granite-embedding"
    assert provider._model_path == str(gguf_abs)
    assert provider._binary_path == "/opt/llama.cpp/llama-server"
    # Laziness contract: factory construction must not start any server.
    assert fake_manager.start_calls == []


def test_factory_joins_relative_gguf_with_models_dir(tmp_path: Path) -> None:
    import src.ollama_tutor.tutor.providers.gguf_embedding as gguf_module

    config = _fresh_config(tmp_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    config.tutor_llama_bin = "/opt/llama.cpp/llama-server"
    config.tutor_llama_models_dir = str(models_dir)
    config.tutor_embed_gguf = "granite-embedding.gguf"

    fake_manager = FakeManager()
    original = gguf_module._default_manager
    gguf_module._default_manager = fake_manager
    try:
        provider = create_embedding_provider(config)
    finally:
        gguf_module._default_manager = original

    assert isinstance(provider, GGUFEmbeddingProvider)
    assert provider._model_path == str(models_dir / "granite-embedding.gguf")
    assert fake_manager.start_calls == []


def test_factory_only_one_key_set_falls_back_to_ollama(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    config.tutor_llama_bin = "/opt/llama.cpp/llama-server"
    # tutor_embed_gguf left empty → GGUF path disabled.
    provider = create_embedding_provider(config)
    assert isinstance(provider, OllamaEmbeddingProvider)
