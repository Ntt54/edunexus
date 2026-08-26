"""GGUF LLM provider backed by ``llama-server`` (US7).

Wraps the ``llama-server`` OpenAI-compatible ``/v1/chat/completions``
endpoint with SSE streaming, yielding ``StreamEvent`` objects identical
to ``OllamaClient.chat_stream`` so the tutor service can swap providers
transparently.

Also exposes an ``embed`` method delegating to ``/v1/embeddings``.

This module is UI-agnostic (no fastapi/textual) and depends only on the
standard library, ``httpx``, and sibling modules.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from ...client import StreamEvent
from ...models import InferenceStats

__all__ = ["GGUFLLMError", "GGUFLLMProvider", "create_gguf_llm_provider"]

_DEFAULT_BASE_URL = "http://localhost:8080"


class GGUFLLMError(RuntimeError):
    """Raised on HTTP or protocol errors from the GGUF LLM backend."""


@dataclass
class _LLMStreamState:
    """Mutable accumulator for SSE chunk parsing."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


class GGUFLLMProvider:
    """Duck-typed LLM client for ``llama-server``'s ``/v1/chat/completions``.

    The ``chat_stream`` / ``embed`` signatures are compatible with
    ``OllamaClient`` so ``TutorService`` can swap transparently.

    Parameters
    ----------
    base_url:
        Root URL of the running ``llama-server`` instance.
        Defaults to ``http://localhost:8080``.
    model:
        Model name sent in the request body.  ``None`` or empty string
        means *do not send a ``model`` field* — ``llama-server`` uses
        whatever model it was started with.
    transport:
        Optional ``httpx.AsyncBaseTransport`` for testing (mock).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model or None
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=None,  # no timeout for streaming
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Streaming chat (OllamaClient-compatible signature)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        *,
        think: bool = False,
        options: Any = None,
        format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion from ``llama-server``, yielding ``StreamEvent``.

        Accepts both ``list[Message]`` and ``list[dict]`` for messages
        (duck-typed to match ``OllamaClient``).
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

        # Model: per-request override > constructor default > omit.
        effective_model = model or self.model

        payload: dict[str, Any] = {
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if effective_model:
            payload["model"] = effective_model

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
        if "num_predict" in opts_dict and opts_dict["num_predict"] is not None:
            payload["max_tokens"] = opts_dict["num_predict"]
        if "repeat_penalty" in opts_dict and opts_dict["repeat_penalty"] is not None:
            payload["frequency_penalty"] = opts_dict["repeat_penalty"]
        if "seed" in opts_dict and opts_dict["seed"] is not None:
            payload["seed"] = opts_dict["seed"]

        t_start = time.monotonic()
        state = _LLMStreamState()

        try:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise GGUFLLMError(
                        f"llama-server error {resp.status_code}: "
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

                    # Reasoning / thinking content.
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

        except httpx.ConnectError as exc:
            raise GGUFLLMError(
                f"Impossible de se connecter à {self.base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise GGUFLLMError(f"Requête expirée: {exc}") from exc
        except GGUFLLMError:
            raise
        except Exception as exc:
            raise GGUFLLMError(f"Erreur inattendue: {exc}") from exc

        # Build InferenceStats from accumulated usage.
        elapsed = time.monotonic() - t_start
        stats = None
        if state.prompt_tokens or state.completion_tokens:
            stats = InferenceStats(
                model=effective_model or "",
                prompt_tokens=state.prompt_tokens,
                generated_tokens=state.completion_tokens,
                prompt_eval_duration=0,  # not available from llama-server API
                eval_duration=elapsed,
                total_duration=elapsed,
            )

        yield StreamEvent(kind="done", stats=stats)

    # ------------------------------------------------------------------
    # Embeddings (non-streaming, /v1/embeddings)
    # ------------------------------------------------------------------

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        """Generate embeddings via ``POST /v1/embeddings``.

        Parameters
        ----------
        model:
            Embedding model name (forwarded to ``llama-server``).
        inputs:
            List of texts to embed.

        Returns
        -------
        A list of vectors, one per input, in the same order.

        Raises
        ------
        GGUFLLMError
            On connection, timeout, or non-200 HTTP response.
        """
        client = self._get_client()
        payload: dict[str, Any] = {"model": model, "input": inputs}
        try:
            resp = await client.post("/v1/embeddings", json=payload)
        except httpx.ConnectError as exc:
            raise GGUFLLMError(
                f"Impossible de se connecter à {self.base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise GGUFLLMError(f"Requête expirée: {exc}") from exc
        except Exception as exc:
            raise GGUFLLMError(f"Erreur inattendue: {exc}") from exc
        if resp.status_code != 200:
            raise GGUFLLMError(
                f"Erreur d'embedding {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item.get("embedding", []) for item in items]


# ---------------------------------------------------------------------------
# Factory (attribute-access duck typing on config)
# ---------------------------------------------------------------------------


def create_gguf_llm_provider(config: Any) -> GGUFLLMProvider:
    """Create a GGUF LLM provider from app config.

    Reads ``config.llm_base_url`` and ``config.tutor_llm_gguf`` via
    attribute access (duck-typed :class:`Config`).  Construction is
    lazy: no subprocess is spawned and no network call is made.
    """
    base_url = getattr(config, "llm_base_url", None) or _DEFAULT_BASE_URL
    model = getattr(config, "tutor_llm_gguf", None) or None
    return GGUFLLMProvider(base_url=base_url, model=model)
