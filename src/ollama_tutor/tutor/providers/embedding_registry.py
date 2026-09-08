"""Embedding modes auto/eager/skip (US2 P0-B, FR-005 + NFR-006).

Adapted from ``autreprojet/OpenTutor-main/apps/api/services/embedding/registry.py``
(``NoOpEmbeddingProvider`` skip, ``FallbackEmbeddingProvider`` chain + log,
``get_embedding_provider`` with auto/eager/skip modes).

Contract: ``specs/010-greffe-autreprojet/contracts/providers.md`` (section
Embedding) and ``data-model.md`` E-005: ``embed(text) -> vec``,
``embed_batch(texts) -> vecs``, ``dimension`` auto-detected at first call and
persisted, model/dimension change ⇒ re-index signalled.

CPU-only (NFR-006, NON NEGOTIABLE): real embeddings are delegated to the
existing CPU backends (GGUF ``llama-server`` / Ollama) via
:class:`DelegatedEmbeddingProvider`. ``sentence-transformers``/``torch`` are
explicitly excluded — never imported here. Lazy startup: the factory opens no
connection and spawns no process; backends start on first ``embed`` call.

Stdlib-only (Constitution V, NFR-003): no fastapi/textual, no new dependency.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from typing import Any

logger = logging.getLogger(__name__)

#: Modes supported by :func:`get_embedding_provider`.
EMBEDDING_MODES = ("auto", "eager", "skip")

#: Placeholder width for skip-mode zero vectors (never indexed meaningfully).
NOOP_DIMENSION = 768

_provider: EmbeddingProvider | None = None


class EmbeddingProvider(ABC):
    """Contract-shaped embedding backend (single-text ``embed`` + batch)."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector width (auto-detected at first successful call for real backends)."""
        raise NotImplementedError

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Return one embedding vector for ``text``."""
        raise NotImplementedError

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in order (default: sequential ``embed``)."""
        return [await self.embed(text) for text in texts]


class NoOpEmbeddingProvider(EmbeddingProvider):
    """Zero-cost provider: instant zero vectors, no network, no model (``skip``)."""

    def __init__(self, dimension: int = NOOP_DIMENSION) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        return [0.0] * self._dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimension for _ in texts]


class FallbackEmbeddingProvider(EmbeddingProvider):
    """Primary → backup chain: first success wins, failures are logged."""

    def __init__(self, providers: list[tuple[str, EmbeddingProvider]]) -> None:
        if not providers:
            raise RuntimeError("No embedding providers available.")
        self._providers = list(providers)
        self._primary_name = providers[0][0]

    @property
    def dimension(self) -> int:
        return self._providers[0][1].dimension

    async def embed(self, text: str) -> list[float]:
        last_error: BaseException | None = None
        for name, provider in self._providers:
            try:
                result = await provider.embed(text)
                if name != self._primary_name:
                    logger.info(
                        "Embedding fallback: using '%s' (primary '%s' failed)",
                        name,
                        self._primary_name,
                    )
                return result
            except (ConnectionError, TimeoutError, RuntimeError, ValueError, OSError) as exc:
                last_error = exc
                logger.warning("Embedding provider '%s' failed: %s", name, exc)
        raise RuntimeError(f"All embedding providers failed. Last error: {last_error}")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        last_error: BaseException | None = None
        for name, provider in self._providers:
            try:
                result = await provider.embed_batch(texts)
                if name != self._primary_name:
                    logger.info(
                        "Embedding fallback (batch): using '%s' (primary '%s' failed)",
                        name,
                        self._primary_name,
                    )
                return result
            except (ConnectionError, TimeoutError, RuntimeError, ValueError, OSError) as exc:
                last_error = exc
                logger.warning("Embedding provider '%s' batch failed: %s", name, exc)
        raise RuntimeError(
            f"All embedding providers failed (batch). Last error: {last_error}"
        )


def check_reindex(
    state: MutableMapping[str, Any],
    *,
    model: str,
    dimension: int,
) -> bool:
    """Persist ``(model, dimension)``; return True if re-indexation is required.

    First run (nothing persisted yet) returns False: there is nothing indexed.
    Any model or dimension change returns True: stored vectors are stale.
    """
    prev_model = state.get("embedding_model")
    prev_dim = state.get("embedding_dimension")
    state["embedding_model"] = model
    state["embedding_dimension"] = dimension
    if prev_model is None:
        return False
    return prev_model != model or prev_dim != dimension


class DelegatedEmbeddingProvider(EmbeddingProvider):
    """Adapter over an existing CPU backend (GGUF ``llama-server`` / Ollama).

    The backend is duck-typed ``async embed(texts: list[str]) -> list[vec]``
    (the shape of :class:`OllamaEmbeddingProvider` / ``GGUFEmbeddingProvider``).
    Dimension is auto-detected from the first returned vector — never
    hardcoded — then persisted via ``state``; a model/dimension change sets
    :attr:`needs_reindex`.
    """

    def __init__(
        self,
        backend: Any,
        *,
        model: str,
        state: MutableMapping[str, Any] | None = None,
    ) -> None:
        self._backend = backend
        self._model = model
        self._state = state
        self._dimension: int | None = None
        self.needs_reindex = False

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError(
                "Embedding dimension not detected yet: call embed() first."
            )
        return self._dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._backend.embed(list(texts))
        if not vectors:
            raise RuntimeError("Embedding backend returned no vectors.")
        width = len(vectors[0])
        if self._dimension is None:
            self._dimension = width
            if self._state is not None:
                self.needs_reindex = check_reindex(
                    self._state, model=self._model, dimension=width
                )
        return vectors

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_batch([text]))[0]


def get_embedding_provider(
    mode: str = "auto",
    *,
    providers: list[tuple[str, EmbeddingProvider]] | None = None,
    state: MutableMapping[str, Any] | None = None,
) -> EmbeddingProvider:
    """Return the embedding provider for ``mode`` (singleton, lazy).

    - ``skip``: always :class:`NoOpEmbeddingProvider` (zero cost).
    - ``auto`` (default): configured providers when present, else ``skip``
      (on a CPU machine without API key / local backend, auto ⇒ skip).
    - ``eager``: configured providers, always — :class:`RuntimeError` when
      none is configured (set ``skip`` to disable embeddings explicitly).

    Real CPU backends (GGUF/Ollama, wrapped in :class:`DelegatedEmbeddingProvider`
    by the caller) are passed via ``providers``; wiring them to config happens
    in a later tranche. No connection is opened here.
    """
    global _provider
    if _provider is not None:
        return _provider
    if mode not in EMBEDDING_MODES:
        raise ValueError(f"Unknown embedding mode: {mode!r} (expected auto|eager|skip).")
    if mode == "skip":
        _provider = NoOpEmbeddingProvider()
        logger.info("Embedding mode=skip: using no-op provider (embeddings disabled).")
        return _provider
    chain = list(providers) if providers else []
    if not chain:
        if mode == "auto":
            _provider = NoOpEmbeddingProvider()
            logger.info(
                "Embedding mode=auto: no backend configured, using no-op provider. "
                "Search falls back to keyword-only."
            )
            return _provider
        raise RuntimeError(
            "No embedding provider available. Configure a CPU backend (GGUF/Ollama) "
            "or set EMBEDDING_MODE=skip to disable embeddings."
        )
    if len(chain) == 1:
        _provider = chain[0][1]
    else:
        _provider = FallbackEmbeddingProvider(chain)
        logger.info(
            "Embedding fallback chain: %s",
            " -> ".join(name for name, _ in chain),
        )
    return _provider


def reset_embedding_provider() -> None:
    """Drop the cached singleton (tests / mode switch)."""
    global _provider
    _provider = None


__all__ = [
    "EMBEDDING_MODES",
    "NOOP_DIMENSION",
    "DelegatedEmbeddingProvider",
    "EmbeddingProvider",
    "FallbackEmbeddingProvider",
    "NoOpEmbeddingProvider",
    "check_reindex",
    "get_embedding_provider",
    "reset_embedding_provider",
]
