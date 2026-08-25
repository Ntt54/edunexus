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
