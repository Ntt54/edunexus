"""OpenAI-compatible LLM provider (B1 multi-fournisseurs).

Streams via ``POST /v1/chat/completions`` with SSE parsing.  Yields
``StreamEvent`` objects compatible with the ``OllamaClient.chat_stream``
protocol so ``TutorService._stream_llm`` works unchanged.

Covers: OpenAI, Mistral, Groq, OpenRouter, LM Studio, vLLM,
llama.cpp server (``--openai`` flag), and any service exposing the
``/v1/chat/completions`` endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from ...client import StreamEvent
from ...models import InferenceStats


class OpenAIClientError(Exception):
    """Raised on HTTP or protocol errors from the OpenAI-compatible API."""


@dataclass
class _SimpleModel:
    """Minimal model wrapper with ``.name`` attribute matching OllamaModel protocol."""

    name: str


@dataclass
class _StreamState:
    """Mutable accumulator for SSE chunk parsing."""

    prompt_tokens: int = 0
    completion_tokens: int = 0




class OpenAICompatProvider:
    """Duck-typed client with the same ``chat_stream`` / ``embed`` signature
    as ``OllamaClient`` so ``TutorService`` can swap transparently.

    Parameters
    ----------
    base_url:
        API root, e.g. ``https://api.openai.com/v1``.
    api_key:
        Bearer token.  Empty string = no auth header (local servers).
    transport:
        Optional ``httpx.AsyncBaseTransport`` for testing (mock).
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=None,  # no timeout for streaming
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Model listing (GET /v1/models) — matches OllamaClient.list_models()
    # ------------------------------------------------------------------

    async def list_models(self) -> list[_SimpleModel]:
        """Fetch available models via ``GET /v1/models``."""
        client = self._get_client()
        try:
            resp = await client.get("/models", timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data.get("data", [])
            return [_SimpleModel(m.get("id", "")) for m in items if m.get("id")]
        except Exception:
            return []

    async def check_health(self) -> bool:
        """Check if the remote server is reachable."""
        try:
            models = await self.list_models()
            return True  # reachable if no exception
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Streaming chat (OllamaClient-compatible signature)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        think: bool = False,
        options: Any = None,
        format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion, yielding ``StreamEvent`` objects.

        Accepts both ``list[Message]`` and ``list[dict]`` for messages
        (duck-typed to match OllamaClient).
        """
        client = self._get_client()

        # Build OpenAI-compatible request body.
        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            if hasattr(msg, "to_dict"):
                api_messages.append(msg.to_dict())
            elif isinstance(msg, dict):
                api_messages.append(msg)
            else:
                api_messages.append({"role": "user", "content": str(msg)})

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
        # top_k is NOT supported by OpenAI — skip it.
        if "num_predict" in opts_dict and opts_dict["num_predict"] is not None:
            payload["max_tokens"] = opts_dict["num_predict"]
        # Ollama's repeat_penalty has no equivalent in the standard OpenAI
        # API; do not mis-map it to frequency_penalty (different semantics).
        if "seed" in opts_dict and opts_dict["seed"] is not None:
            payload["seed"] = opts_dict["seed"]
        if tools:
            payload["tools"] = tools
        if format:
            payload["response_format"] = (
                {"type": "json_schema", "json_schema": {
                    "name": "structured_response", "schema": format
                }}
                if isinstance(format, dict)
                else format
            )

        t_start = time.monotonic()
        state = _StreamState()

        try:
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise OpenAIClientError(
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

                    choices = chunk.get("choices", [])
                    delta = choices[0].get("delta", {}) if choices else {}

                    # Some providers (DeepSeek, QwQ) emit reasoning_content.
                    reasoning = delta.get("reasoning_content") or delta.get(
                        "reasoning"
                    )
                    if reasoning:
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
            raise OpenAIClientError(f"Cannot connect to {self.base_url}: {e}")
        except httpx.TimeoutException as e:
            raise OpenAIClientError(f"Request timed out: {e}")
        except OpenAIClientError:
            raise
        except Exception as e:
            raise OpenAIClientError(f"Unexpected error: {e}")

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
            raise OpenAIClientError(f"Cannot connect to {self.base_url}: {e}")
        except httpx.TimeoutException as e:
            raise OpenAIClientError(f"Request timed out: {e}")
        except Exception as e:
            raise OpenAIClientError(f"Unexpected error: {e}")
        if resp.status_code != 200:
            raise OpenAIClientError(
                f"Embedding API error {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        # OpenAI returns {"data": [{"embedding": [...], "index": 0}, ...]}
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item.get("embedding", []) for item in items]
