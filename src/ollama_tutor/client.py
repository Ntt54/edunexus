"""Ollama HTTP API client for chat streaming and metadata.

Uses a module-lifetime httpx.AsyncClient with connection pooling.
All streaming via POST /api/chat with NDJSON incremental parsing.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from .models import (
    InferenceStats,
    Message,
    MessageRole,
    OllamaModel,
    OllamaOptions,
)

DEFAULT_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class OllamaConnectionError(Exception):
    """Raised when Ollama server cannot be reached."""
    pass


class OllamaAPIError(Exception):
    """Raised when Ollama returns an HTTP error."""
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Ollama API error {status}: {body[:200]}")


@dataclass
class StreamEvent:
    """Events yielded by chat_stream."""
    kind: str  # "thinking" | "content" | "done"
    text: str = ""
    stats: InferenceStats | None = None


class OllamaClient:
    """HTTP API client for Ollama with persistent connection."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=None,  # No timeout for streaming
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Metadata (non-streaming)
    # ------------------------------------------------------------------

    async def check_health(self) -> bool:
        try:
            client = self._get_client()
            resp = await client.get("/api/tags")
            return resp.status_code == 200
        except Exception:
            return False

    async def get_version(self) -> str:
        try:
            client = self._get_client()
            resp = await client.get("/api/version")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("version", "unknown")
        except Exception:
            pass
        return "unknown"

    async def list_models(self) -> list[OllamaModel]:
        try:
            client = self._get_client()
            resp = await client.get("/api/tags")
            if resp.status_code != 200:
                return []
            data = resp.json()
            models: list[OllamaModel] = []
            for m in data.get("models", []):
                models.append(OllamaModel(
                    name=m.get("name", ""),
                    size=m.get("size", 0),
                    digest=m.get("digest", ""),
                    modified_at=m.get("modified_at", ""),
                    details=m.get("details", {}),
                ))
            return models
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[Message],
        model: str,
        *,
        think: bool = False,
        options: OllamaOptions | None = None,
        format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion via the HTTP API.

        Yields StreamEvent objects:
        - kind="thinking", text=<thinking chunk>
        - kind="content", text=<content chunk>
        - kind="done", stats=InferenceStats
        """
        client = self._get_client()

        # Build request payload
        api_messages = [msg.to_dict() for msg in messages]

        payload: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "stream": True,
        }

        # Always send the key explicitly: Ollama's thinking-capable models
        # think by DEFAULT when "think" is absent — omitting it is NOT the
        # same as disabling it (user-visible bug on gemma4:e2b).
        payload["think"] = bool(think)

        if options:
            # Accept both OllamaOptions instances and plain dicts
            opts_dict = options.to_dict() if hasattr(options, "to_dict") else dict(options)
            if opts_dict:
                payload["options"] = {k: v for k, v in opts_dict.items() if v is not None}

        if format:
            payload["format"] = format

        if tools:
            payload["tools"] = tools

        # Make streaming request
        try:
            async with client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise OllamaAPIError(resp.status_code, body.decode("utf-8", "replace"))

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = asyncio.get_event_loop().run_in_executor(
                            None, lambda: __import__("json").loads(line)
                        )
                        chunk = await chunk
                    except Exception:
                        continue

                    # Thinking chunk
                    if chunk.get("message", {}).get("thinking"):
                        yield StreamEvent(
                            kind="thinking",
                            text=chunk["message"]["thinking"],
                        )

                    # Content chunk
                    if chunk.get("message", {}).get("content"):
                        yield StreamEvent(
                            kind="content",
                            text=chunk["message"]["content"],
                        )

                    # Final frame with stats
                    if chunk.get("done"):
                        stats = self._parse_stats(chunk)
                        yield StreamEvent(kind="done", stats=stats)
                        break

        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}: {e}")
        except httpx.TimeoutException as e:
            raise OllamaConnectionError(f"Request to Ollama timed out: {e}")
        except OllamaAPIError:
            raise
        except Exception as e:
            raise OllamaConnectionError(f"Unexpected error: {e}")

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        """Generate embeddings via ``POST /api/embed`` (non-streaming).

        Args:
            model: Embedding model name (e.g. ``embeddinggemma``).
            inputs: List of texts to embed.

        Returns:
            A list of vectors, one per input, in the same order.

        Raises:
            OllamaConnectionError: on connect/timeout failures.
            OllamaAPIError: on a non-200 HTTP response.
        """
        client = self._get_client()
        payload: dict[str, Any] = {"model": model, "input": inputs}
        try:
            resp = await client.post("/api/embed", json=payload)
        except httpx.ConnectError as e:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self.base_url}: {e}"
            )
        except httpx.TimeoutException as e:
            raise OllamaConnectionError(f"Request to Ollama timed out: {e}")
        except OllamaAPIError:
            raise
        except Exception as e:
            raise OllamaConnectionError(f"Unexpected error: {e}")
        if resp.status_code != 200:
            raise OllamaAPIError(resp.status_code, resp.text)
        data = resp.json()
        return data.get("embeddings", [])

    @staticmethod
    def _parse_stats(chunk: dict[str, Any]) -> InferenceStats | None:
        """Extract InferenceStats from final chunk."""
        if not chunk.get("done"):
            return None
        prompt_tokens = chunk.get("prompt_eval_count", 0)
        generated_tokens = chunk.get("eval_count", 0)
        if not prompt_tokens and not generated_tokens:
            return None
        model = chunk.get("model", "")
        prompt_eval_duration = chunk.get("prompt_eval_duration", 0) / 1e9
        eval_duration = chunk.get("eval_duration", 0) / 1e9
        total_duration = chunk.get("total_duration", 0) / 1e9
        stats = InferenceStats(
            model=model,
            prompt_tokens=prompt_tokens,
            generated_tokens=generated_tokens,
            prompt_eval_duration=prompt_eval_duration,
            eval_duration=eval_duration,
            total_duration=total_duration,
        )
        # If the API provides eval_rate directly, use it to override the computed speed
        if "eval_rate" in chunk and chunk["eval_rate"] > 0:
            # We can't directly set generation_speed as it's a property,
            # but the eval_duration will give the correct speed
            # If eval_duration is 0 but eval_rate is provided, compute duration
            if eval_duration == 0 and generated_tokens > 0:
                stats.eval_duration = generated_tokens / chunk["eval_rate"]
        return stats


# Backwards compatibility: parse_stats function
def parse_stats(chunk: dict[str, Any]) -> InferenceStats | None:
    """Extract InferenceStats from a final chunk (legacy function)."""
    return OllamaClient._parse_stats(chunk)