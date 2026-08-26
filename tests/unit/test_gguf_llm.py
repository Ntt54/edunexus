"""Tests du provider GGUF LLM via llama-server (US7).

Vérifie le parsing SSE, le mapping des options, le endpoint d'embeddings,
et la factory de création.  Totalement offline via ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from src.ollama_tutor.models import InferenceStats, Message, MessageRole
from src.ollama_tutor.tutor.providers.gguf_llm import (
    GGUFLLMError,
    GGUFLLMProvider,
    create_gguf_llm_provider,
)


def _msg(role: str, content: str) -> Message:
    return Message(role=MessageRole(role), content=content)


def _sse_lines(chunks: list[dict[str, Any]]) -> str:
    lines = []
    for c in chunks:
        lines += ["data: " + json.dumps(c)]
    lines.append("data: [DONE]")
    return "\n".join(lines)


class FakeSSETransport(httpx.AsyncBaseTransport):
    """Mock transport that returns SSE-encoded ``chunks`` for any request."""

    def __init__(self, response_chunks: list[dict[str, Any]], status: int = 200):
        self._chunks = response_chunks
        self._status = status

    async def handle_async_request(self, request):
        body = _sse_lines(self._chunks)
        return httpx.Response(
            self._status,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )


# ---------------------------------------------------------------------------
# 1. Construction sans erreur
# ---------------------------------------------------------------------------


def test_gguf_llm_provider_init():
    """Crée une instance sans erreur."""
    provider = GGUFLLMProvider(base_url="http://localhost:8080")
    assert provider.base_url == "http://localhost:8080"
    assert provider.model is None
    assert provider._client is None

    provider_with_model = GGUFLLMProvider(
        base_url="http://127.0.0.1:9090", model="granite-4.1-3b"
    )
    assert provider_with_model.base_url == "http://127.0.0.1:9090"
    assert provider_with_model.model == "granite-4.1-3b"


# ---------------------------------------------------------------------------
# 2. chat_stream avec MockTransport : contenu + done + stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gguf_llm_chat_stream_mock():
    """Vérifie le parsing SSE : content, thinking, done avec stats."""
    chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": "Bonjour"}}]},
        {"choices": [{"delta": {"reasoning_content": "Réflexion..."}}]},
        {"choices": [{"delta": {"content": " le monde"}}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
    ]
    provider = GGUFLLMProvider(
        base_url="http://fake:8080",
        transport=FakeSSETransport(chunks),
    )

    events = []
    async for ev in provider.chat_stream(
        [_msg("user", "Salut")], model="granite"
    ):
        events.append(ev)

    assert len(events) == 4
    assert events[0].kind == "content" and events[0].text == "Bonjour"
    assert events[1].kind == "thinking" and events[1].text == "Réflexion..."
    assert events[2].kind == "content" and events[2].text == " le monde"
    assert events[3].kind == "done"
    assert events[3].stats is not None
    assert events[3].stats.prompt_tokens == 12
    assert events[3].stats.generated_tokens == 7

    await provider.close()


# ---------------------------------------------------------------------------
# 3. embed avec MockTransport : vectors retournées
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gguf_llm_embed_mock():
    """Vérifie que /v1/embeddings retourne les vecteurs correctement."""
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    class EmbedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            payload = {
                "data": [
                    {"embedding": vec, "index": i}
                    for i, vec in enumerate(vectors)
                ]
            }
            return httpx.Response(200, json=payload)

    provider = GGUFLLMProvider(
        base_url="http://fake:8080",
        transport=EmbedTransport(),
    )

    result = await provider.embed("granite-embedding", ["alpha", "beta"])
    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    await provider.close()


# ---------------------------------------------------------------------------
# 4. Factory : crée un provider à partir de config
# ---------------------------------------------------------------------------


def test_gguf_llm_factory():
    """La factory crée un provider avec les bonnes valeurs de config."""
    config = MagicMock()
    config.llm_base_url = "http://127.0.0.1:9090"
    config.tutor_llm_gguf = "granite-4.1-3b.gguf"

    provider = create_gguf_llm_provider(config)
    assert isinstance(provider, GGUFLLMProvider)
    assert provider.base_url == "http://127.0.0.1:9090"
    assert provider.model == "granite-4.1-3b.gguf"


def test_gguf_llm_factory_defaults():
    """La factory utilise les valeurs par défaut quand config est vide."""
    config = MagicMock()
    config.llm_base_url = ""
    config.tutor_llm_gguf = ""

    provider = create_gguf_llm_provider(config)
    assert isinstance(provider, GGUFLLMProvider)
    assert provider.base_url == "http://localhost:8080"
    assert provider.model is None


# ---------------------------------------------------------------------------
# 5. chat_stream maps options correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gguf_llm_chat_stream_maps_options():
    """Vérifie que les options Ollama sont mappées vers OpenAI."""
    captured = {}

    class CaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal captured
            captured = json.loads(request.content)
            body = _sse_lines([
                {"choices": [{"delta": {"content": "OK"}}]},
                {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            ])
            return httpx.Response(200, content=body.encode("utf-8"))

    provider = GGUFLLMProvider(
        base_url="http://fake:8080",
        transport=CaptureTransport(),
    )
    async for _ in provider.chat_stream(
        [_msg("user", "Test")],
        model="granite",
        options={"temperature": 0.5, "top_p": 0.95, "num_predict": 128,
                 "repeat_penalty": 1.1, "seed": 7},
    ):
        pass

    assert captured["model"] == "granite"
    assert captured["temperature"] == 0.5
    assert captured["top_p"] == 0.95
    assert captured["max_tokens"] == 128
    assert captured["repeat_penalty"] == 1.1
    assert "frequency_penalty" not in captured
    assert captured["seed"] == 7
    assert captured["stream"] is True

    await provider.close()
