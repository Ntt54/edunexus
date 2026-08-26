"""OpenAI-compatible LLM provider (B1 multi-fournisseurs).

Streams via ``POST /v1/chat/completions`` with SSE parsing.  Yields
``StreamEvent`` objects compatible with the ``OllamaClient.chat_stream``
protocol so ``TutorService._stream_llm`` works unchanged.

Covers: OpenAI, Mistral, Groq, OpenRouter, LM Studio, vLLM,
llama.cpp server (``--openai`` flag), and any service exposing the
``/v1/chat/completions`` endpoint.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from ...models import InferenceStats, Message


class OpenAICompatError(Exception):
    """Raised on HTTP or protocol errors from the OpenAI-compatible API."""


@dataclass
class _StreamState:
    """Mutable accumulator for SSE chunk parsing."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    has_reasoning: bool = False


class OpenAICompatClient:
    """Duck-typed client with the same ``chat_stream`` / ``embed`` signature
    as ``OllamaClient`` so ``TutorService`` can swap transparently.

    Parameters
    ----------
    base_url:
        API root, e.g. ``https://api.openai.com/v1``.
    api_key:
        Bearer token.  Empty string = no auth header (local servers).
    """

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=None,  # no timeout for streaming
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Streaming chat (OllamaClient-compatible signature)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[Message],
        model: str,
        *,
        think: bool = False,
        options: Any = None,
        format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        """Stream a chat completion, yielding ``StreamEvent``-like objects.

        The yielded objects duck-type ``client.StreamEvent`` with attributes
        ``kind`` (``"thinking"`` | ``"content"`` | ``"done"``), ``text``
        and ``stats``.
        """
        from ...client import StreamEvent

        client = self._get_client()

        # Build OpenAI-compatible request body.
        api_messages = [msg.to_dict() for msg in messages]
        payload: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # Map OllamaOptions → OpenAI parameters where meaningful.
        opts_dict: dict[str, Any] = {}
        if options is not None:
            opts_dict = (
                options.to_dict() if hasattr(options, "to_dict") else dict(options)
            )
        if "temperature" in opts_dict and opts_dict["temperature"] is not None:
            payload["temperature"] = opts_dict["temperature"]
        if "top_p" in opts_dict and opts_dict["top_p"] is not None:
            payload["top_p"] = opts_dict["top_p"]
        if "top_k" in opts_dict and opts_dict["top_k"] is not None:
            payload["top_k"] = opts_dict["top_k"]
        if "num_predict" in opts_dict and opts_dict["num_predict"] is not None:
            payload["max_tokens"] = opts_dict["num_predict"]
        if "repeat_penalty" in opts_dict and opts_dict["repeat_penalty"] is not None:
            payload["frequency_penalty"] = opts_dict["repeat_penalty"]
        if "seed" in opts_dict and opts_dict["seed"] is not None:
            payload["seed"] = opts_dict["seed"]

        if format:
            payload["response_format"] = format

        t_start = time.monotonic()
        state = _StreamState()

        try:
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise OpenAICompatError(
                        f"OpenAI API error {resp.status_code}: "
                        f"{body.decode('utf-8', 'replace')[:300]}"
                    )

                async for raw_line in resp.aiter_lines():
                    if not raw_line or not raw_line.startswith("data: "):
                        continue
                    data_str = raw_line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    delta = (
                        chunk.get("choices", [{}])[0].get("delta", {})
                        if chunk.get("choices")
                        else {}
                    )

                    # Some providers (DeepSeek, QwQ) emit reasoning_content.
                    reasoning = delta.get("reasoning_content") or delta.get(
                        "reasoning"
                    )
                    if reasoning:
                        state.has_reasoning = True
                        yield StreamEvent(kind="thinking", text=reasoning)

                    content = delta.get("content")
                    if content:
                        yield StreamEvent(kind="content", text=content)

                    # Usage may appear in the last chunk or in a dedicated
                    # usage-only chunk (when stream_options.include_usage).
                    usage = chunk.get("usage")
                    if usage:
                        state.prompt_tokens = usage.get(
                            "prompt_tokens", state.prompt_tokens
                        )
                        state.completion_tokens = usage.get(
                            "completion_tokens", state.completion_tokens
                        )

        except httpx.ConnectError as e:
            raise OpenAICompatError(f"Cannot connect to {self.base_url}: {e}")
        except httpx.TimeoutException as e:
            raise OpenAICompatError(f"Request timed out: {e}")
        except OpenAICompatError:
            raise
        except Exception as e:
            raise OpenAICompatError(f"Unexpected error: {e}")

        # Build InferenceStats from accumulated usage.
        elapsed = time.monotonic() - t_start
        stats = None
        if state.prompt_tokens or state.completion_tokens:
            stats = InferenceStats(
                model=model,
                prompt_tokens=state.prompt_tokens,
                generated_tokens=state.completion_tokens,
                prompt_eval_duration=0,  # not available from OpenAI API
                eval_duration=elapsed,
                total_duration=elapsed,
            )

        yield StreamEvent(kind="done", stats=stats)

    # ------------------------------------------------------------------
    # Embeddings (non-streaming, /v1/embeddings)
    # ------------------------------------------------------------------

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        """Generate embeddings via ``POST /v1/embeddings``."""
        client = self._get_client()
        payload: dict[str, Any] = {"model": model, "input": inputs}
        try:
            resp = await client.post("/embeddings", json=payload)
        except httpx.ConnectError as e:
            raise OpenAICompatError(f"Cannot connect to {self.base_url}: {e}")
        except httpx.TimeoutException as e:
            raise OpenAICompatError(f"Request timed out: {e}")
        except Exception as e:
            raise OpenAICompatError(f"Unexpected error: {e}")
        if resp.status_code != 200:
            raise OpenAICompatError(
                f"Embedding API error {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        # OpenAI returns {"data": [{"embedding": [...], "index": 0}, ...]}
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item.get("embedding", []) for item in items]
