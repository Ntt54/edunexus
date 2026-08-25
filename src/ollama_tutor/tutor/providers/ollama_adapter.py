"""Ollama-backed implementations of the provider interfaces (Phase 0).

Adapters receive the ``OllamaClient`` by injection (duck-typed) — they never
import httpx nor construct clients themselves. Client API adapted:

- ``client.embed(model, inputs) -> list[list[float]]`` (non-streaming)
- ``client.chat_stream(messages, model, *, options=...) -> AsyncIterator[StreamEvent]``
  (the client has no non-streaming chat; content chunks are accumulated,
  mirroring ``TutorService._llm_collect``).
"""

from __future__ import annotations

from typing import Any

from ...models import Message, MessageRole
from .base import EmbeddingProvider, LLMProvider


class OllamaEmbeddingProvider(EmbeddingProvider):
    """EmbeddingProvider over ``OllamaClient.embed`` (Granite Embedding R2 via GGUF/Ollama).

    ``dims`` is learned from the first returned vector and cached — it is
    ``None`` until the first successful embed and is never hardcoded.
    """

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model
        self._dims: int | None = None

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dims(self) -> int | None:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await self._client.embed(self._model, texts)
        if self._dims is None:
            for vec in vectors:
                if vec:
                    self._dims = len(vec)
                    break
        return vectors


class OllamaLLMProvider(LLMProvider):
    """LLMProvider over ``OllamaClient.chat_stream`` (Granite 4.1 3B Instruct via Ollama).

    The client only offers streaming chat, so ``generate`` accumulates the
    ``kind == "content"`` chunks into a single string.
    """

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        messages: list[Message] = []
        if system:
            messages.append(Message(role=MessageRole.SYSTEM, content=system))
        messages.append(Message(role=MessageRole.USER, content=prompt))
        parts: list[str] = []
        async for ev in self._client.chat_stream(messages, self._model, options=options):
            if ev.kind == "content":
                parts.append(ev.text)
        return "".join(parts)
