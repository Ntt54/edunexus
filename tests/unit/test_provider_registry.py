"""US2 P0-B (T014) — ProviderRegistry: fallback, unhealthy skip, empty registry,
large/small/fast variants, ping_all, MockLLMClient. 100% offline (no network)."""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.providers.registry import (
    CIRCUIT_OPEN_THRESHOLD,
    CIRCUIT_RESET_TIMEOUT,
    COOLDOWN_STEPS,
    LLMClient,
    LLMConfigurationError,
    MockLLMClient,
    ProviderRegistry,
)


def _healthy(name: str = "ok", text: str = "hello") -> MockLLMClient:
    return MockLLMClient(name=name, responses=[text])


def _unhealthy(name: str = "ko") -> MockLLMClient:
    client = MockLLMClient(name=name, responses=["never"])
    client.mark_unhealthy("boom")
    client.mark_unhealthy("boom")
    client.mark_unhealthy("boom")  # threshold -> circuit open
    assert not client.is_healthy
    return client


def test_circuit_breaker_constants():
    assert COOLDOWN_STEPS == [5, 10, 20, 60]
    assert CIRCUIT_OPEN_THRESHOLD == 3
    assert CIRCUIT_RESET_TIMEOUT == 120


def test_empty_registry_raises_configuration_error():
    registry = ProviderRegistry()
    with pytest.raises(LLMConfigurationError):
        registry.get()


def test_fallback_primary_to_backup():
    registry = ProviderRegistry()
    registry.register("primary", _unhealthy("primary"))
    registry.register("backup", _healthy("backup", text="from-backup"))
    assert registry.get().name == "backup"


def test_unhealthy_requested_provider_is_skipped():
    registry = ProviderRegistry()
    registry.register("a", _unhealthy("a"), primary=True)
    registry.register("b", _healthy("b"))
    assert registry.get("a").name == "b"


def test_unknown_name_falls_back_to_primary():
    registry = ProviderRegistry()
    registry.register("main", _healthy("main"), primary=True)
    registry.register("other", _healthy("other"))
    assert registry.get("nope").name == "main"


def test_all_unhealthy_raises_runtime_error():
    registry = ProviderRegistry()
    registry.register("a", _unhealthy("a"))
    registry.register("b", _unhealthy("b"))
    with pytest.raises(RuntimeError):
        registry.get()


@pytest.mark.parametrize("hint", ["large", "small", "fast"])
def test_variants_large_small_fast(hint: str):
    registry = ProviderRegistry()
    registry.register("main", _healthy("main"), primary=True)
    registry.register_variant(hint, _healthy(f"variant-{hint}", text=f"via-{hint}"))
    assert registry.get(hint).name == f"variant-{hint}"


def test_unhealthy_variant_falls_through_to_primary():
    registry = ProviderRegistry()
    registry.register("main", _healthy("main"), primary=True)
    registry.register_variant("fast", _unhealthy("variant-fast"))
    assert registry.get("fast").name == "main"


def test_provider_is_llmclient_instance():
    assert isinstance(_healthy(), LLMClient)


@pytest.mark.asyncio
async def test_mock_client_chat_offline():
    client = MockLLMClient(name="mock", responses=["one", "two"])
    first = await client.chat([{"role": "user", "content": "hi"}])
    second = await client.chat([{"role": "user", "content": "hi again"}])
    assert first["content"] == "one"
    assert second["content"] == "two"
    assert "usage" in first
    assert await client.ping() is True


@pytest.mark.asyncio
async def test_mock_client_default_response_offline():
    client = MockLLMClient()
    result = await client.chat([{"role": "user", "content": "ping"}])
    assert result["content"]
    assert client.get_last_usage()["output_tokens"] >= 1


@pytest.mark.asyncio
async def test_ping_all_reports_per_provider():
    registry = ProviderRegistry()
    registry.register("up", _healthy("up"))
    registry.register("down", _unhealthy("down"))
    assert await registry.ping_all() == {"up": True, "down": False}


@pytest.mark.asyncio
async def test_registry_get_result_can_chat():
    registry = ProviderRegistry()
    registry.register("main", _healthy("main", text="answer!"), primary=True)
    result = await registry.get().chat([{"role": "user", "content": "q"}])
    assert result["content"] == "answer!"


def test_circuit_breaker_opens_after_threshold_and_resets():
    client = MockLLMClient(name="cb")
    assert client.is_healthy
    client.mark_unhealthy("e1")
    client.mark_unhealthy("e2")
    assert client._circuit_open is False
    client.mark_unhealthy("e3")
    assert client._circuit_open is True
    assert client.is_healthy is False
    client.mark_healthy()
    assert client.is_healthy is True
