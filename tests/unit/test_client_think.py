"""Regression: ``think`` must be sent explicitly, even when False.

Ollama's thinking-capable models (gemma4:e2b, qwen3, ...) think by DEFAULT
when the payload omits the ``think`` key. Omitting it is not the same as
disabling it — the tutor must send ``"think": false`` explicitly.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.models import OllamaOptions


def _make_transport(captured: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        lines = [
            json.dumps({"message": {"role": "assistant", "content": "Bonjour"}, "done": False}),
            json.dumps({
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "prompt_eval_count": 5,
                "eval_count": 7,
                "prompt_eval_duration": 1_000_000,
                "eval_duration": 2_000_000,
            }),
        ]
        return httpx.Response(
            200,
            content=("\n".join(lines) + "\n").encode(),
            headers={"Content-Type": "application/x-ndjson"},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_think_false_sent_explicitly() -> None:
    captured: list[dict] = []
    client = OllamaClient(transport=_make_transport(captured))
    async for _ in client.chat_stream([], "gemma4:e2b", think=False):
        pass
    await client.close()
    assert captured and captured[0]["think"] is False


@pytest.mark.asyncio
async def test_think_true_sent_explicitly() -> None:
    captured: list[dict] = []
    client = OllamaClient(transport=_make_transport(captured))
    async for _ in client.chat_stream([], "gemma4:e2b", think=True):
        pass
    await client.close()
    assert captured and captured[0]["think"] is True


@pytest.mark.asyncio
async def test_keep_alive_is_top_level_chat_parameter() -> None:
    captured: list[dict] = []
    client = OllamaClient(transport=_make_transport(captured))
    options = OllamaOptions(num_predict=128, keep_alive="1h")

    async for _ in client.chat_stream([], "gemma4:e2b", options=options):
        pass
    await client.close()

    payload = captured[0]
    assert payload["keep_alive"] == "1h"
    assert payload["options"]["num_predict"] == 128
    assert "keep_alive" not in payload["options"]


@pytest.mark.asyncio
async def test_keep_alive_is_sent_for_embeddings() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    client = OllamaClient(transport=httpx.MockTransport(handler))
    vectors = await client.embed("granite-embedding", ["texte"], keep_alive="45m")
    await client.close()

    assert vectors == [[0.1, 0.2]]
    assert captured[0]["keep_alive"] == "45m"
