"""Tests du provider OpenAI-compatible (B1 multi-fournisseurs).

Vérifie le parsing SSE, le mapping des options, et la compatibilité
d'interface avec OllamaClient.chat_stream (swap transparent).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.ollama_tutor.models import Message, MessageRole
from src.ollama_tutor.tutor.providers.openai_compat import (
    OpenAICompatProvider,
    OpenAIClientError,
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


@pytest.mark.asyncio
async def test_chat_stream_content_and_done():
    chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": "Bonjour"}}]},
        {"choices": [{"delta": {"content": " le monde"}}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    provider = OpenAICompatProvider(
        base_url="https://fake.api/v1",
        transport=FakeSSETransport(chunks),
    )
    events = []
    async for ev in provider.chat_stream(
        [_msg("user", "Salut")], model="gpt-4o-mini"
    ):
        events.append(ev)

    assert len(events) == 3
    assert events[0].kind == "content" and events[0].text == "Bonjour"
    assert events[1].kind == "content" and events[1].text == " le monde"
    assert events[2].kind == "done"
    assert events[2].stats.prompt_tokens == 10
    assert events[2].stats.generated_tokens == 5
    await provider.close()


@pytest.mark.asyncio
async def test_chat_stream_reasoning_content():
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "Analyse..."}}]},
        {"choices": [{"delta": {"content": "Réponse"}}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}},
    ]
    provider = OpenAICompatProvider(
        base_url="https://fake.api/v1",
        transport=FakeSSETransport(chunks),
    )
    events = []
    async for ev in provider.chat_stream(
        [_msg("user", "Pense")], model="gpt-o1"
    ):
        events.append(ev)

    assert events[0].kind == "thinking" and events[0].text == "Analyse..."
    assert events[1].kind == "content"
    await provider.close()


@pytest.mark.asyncio
async def test_chat_stream_maps_options():
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

    provider = OpenAICompatProvider(
        base_url="https://fake.api/v1",
        transport=CaptureTransport(),
    )
    async for _ in provider.chat_stream(
        [_msg("user", "Test")],
        model="gpt-4o",
        options={"temperature": 0.7, "top_p": 0.9, "num_predict": 256,
                 "repeat_penalty": 1.2, "seed": 42},
    ):
        pass

    assert captured["model"] == "gpt-4o"
    assert captured["temperature"] == 0.7
    assert captured["top_p"] == 0.9
    assert captured["max_tokens"] == 256
    # repeat_penalty is Ollama-specific and must not be mis-mapped to
    # OpenAI's frequency_penalty, which has different semantics.
    assert "frequency_penalty" not in captured
    assert captured["seed"] == 42
    assert captured["stream"] is True
    await provider.close()


@pytest.mark.asyncio
async def test_chat_stream_api_error():
    provider = OpenAICompatProvider(
        base_url="https://fake.api/v1",
        transport=FakeSSETransport([], status=401),
    )
    with pytest.raises(OpenAIClientError, match="401"):
        async for _ in provider.chat_stream(
            [_msg("user", "Bad key")], model="gpt-4o"
        ):
            pass
    await provider.close()


@pytest.mark.asyncio
async def test_chat_stream_auth_header():
    captured_headers = {}

    class HeaderCapture(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal captured_headers
            captured_headers = dict(request.headers)
            body = _sse_lines([
                {"choices": [{"delta": {"content": ""}}]},
                {"choices": [], "usage": {}},
            ])
            return httpx.Response(200, content=body.encode("utf-8"))

    provider = OpenAICompatProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test-123",
        transport=HeaderCapture(),
    )
    async for _ in provider.chat_stream([_msg("user", "Hi")], model="gpt-4o"):
        pass

    assert captured_headers.get("authorization") == "Bearer sk-test-123"
    await provider.close()


@pytest.mark.asyncio
async def test_chat_stream_connect_error():
    class FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused")

    provider = OpenAICompatProvider(
        base_url="https://unreachable.api/v1",
        transport=FailTransport(),
    )
    with pytest.raises(OpenAIClientError, match="Cannot connect"):
        async for _ in provider.chat_stream(
            [_msg("user", "Hi")], model="gpt-4o"
        ):
            pass
    await provider.close()
