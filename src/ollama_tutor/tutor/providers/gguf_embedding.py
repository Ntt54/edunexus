"""GGUF embedding provider backed by a managed ``llama-server`` (Phase 2).

Wraps :class:`~ollama_tutor.tutor.providers.llama_server.LlamaServerManager`
so embeddings are served locally from a Granite Embedding GGUF via
``llama-server``'s OpenAI-compatible ``/v1/embeddings`` endpoint.

Laziness contract: constructing this provider (directly or through
:func:`create_embedding_provider`) never spawns a process nor opens a
network connection. The server starts on the first :meth:`embed` call and
is reused afterward; a dead server triggers exactly one transparent
restart per request before the error propagates.

This module is UI-agnostic (no fastapi/textual) and depends only on the
standard library, ``httpx``, and sibling provider modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .base import EmbeddingProvider
from .llama_server import LlamaServerConfig, LlamaServerManager
from .ollama_adapter import OllamaEmbeddingProvider

__all__ = [
    "GGUFEmbeddingError",
    "GGUFEmbeddingProvider",
    "create_embedding_provider",
    "get_default_manager",
]

_DEFAULT_STARTUP_TIMEOUT_S = 120.0


class GGUFEmbeddingError(RuntimeError):
    """Raised when the GGUF embedding backend returns an unusable response."""


class GGUFEmbeddingProvider(EmbeddingProvider):
    """EmbeddingProvider over a lazily-started managed ``llama-server``.

    Args:
        manager: Duck-typed server manager exposing
            ``async start(LlamaServerConfig) -> ManagedLlamaServer`` with a
            ``.base_url`` attribute on the handle.
        binary_path: Path to the ``llama-server`` binary.
        model_path: Path to the embedding GGUF file.
        model_name: Identifier reported by :attr:`model_name`.
        extra_args: Extra CLI flags appended to the launch command.
        timeout_s: Server startup timeout in seconds (default 120).
        transport: TEST SEAM forwarded to the internal
            ``httpx.AsyncClient(transport=...)``; ``None`` in production.
    """

    def __init__(
        self,
        manager: Any,
        *,
        binary_path: str,
        model_path: str,
        model_name: str = "granite-embedding",
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._manager = manager
        self._binary_path = binary_path
        self._model_path = model_path
        self._model_name = model_name
        self._extra_args = list(extra_args) if extra_args else []
        self._timeout_s = float(timeout_s) if timeout_s else _DEFAULT_STARTUP_TIMEOUT_S
        self._transport = transport
        self._server: Any | None = None  # ManagedLlamaServer handle, set lazily.
        self._client: httpx.AsyncClient | None = None
        self._dims: int | None = None

    # --- EmbeddingProvider interface -------------------------------------

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dims(self) -> int | None:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order.

        Starts the managed server on first use and reuses it afterward.
        On a connection failure the server is restarted exactly once and
        the request retried once before the error propagates.
        """
        if not texts:
            return []

        client = self._ensure_client()
        server = await self._ensure_server()
        try:
            vectors = await self._post_embeddings(client, server.base_url, texts)
        except httpx.TransportError:
            # Stale/dead server: stop it if possible, start a fresh one,
            # retry the request exactly once.
            server = await self._restart_server()
            vectors = await self._post_embeddings(client, server.base_url, texts)

        if len(vectors) != len(texts):
            raise GGUFEmbeddingError(
                f"embedding backend returned {len(vectors)} vectors "
                f"for {len(texts)} inputs"
            )
        if self._dims is None:
            for vec in vectors:
                if vec:
                    self._dims = len(vec)
                    break
        return vectors

    async def aclose(self) -> None:
        """Close the HTTP client. Server lifetime belongs to the manager."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Internals ---------------------------------------------------------

    def _build_server_config(self) -> LlamaServerConfig:
        return LlamaServerConfig(
            binary_path=self._binary_path,
            model_path=self._model_path,
            extra_args=self._extra_args,
            startup_timeout_s=self._timeout_s,
        )

    async def _ensure_server(self) -> Any:
        if self._server is None:
            self._server = await self._manager.start(self._build_server_config())
        return self._server

    async def _restart_server(self) -> Any:
        old = self._server
        self._server = None
        if old is not None and hasattr(old, "stop"):
            try:
                await old.stop()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        self._server = await self._manager.start(self._build_server_config())
        return self._server

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s),
                transport=self._transport,
            )
        return self._client

    async def _post_embeddings(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        texts: list[str],
    ) -> list[list[float]]:
        try:
            response = await client.post(
                f"{base_url}/v1/embeddings",
                json={"input": texts},
            )
        except httpx.HTTPStatusError:
            raise
        except httpx.TransportError:
            raise  # Caller decides whether to restart once.
        except httpx.HTTPError as exc:
            raise GGUFEmbeddingError(f"embedding request failed: {exc}") from exc

        if response.status_code != 200:
            raise GGUFEmbeddingError(
                f"embedding backend returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            data = payload["data"]
            vectors = [item["embedding"] for item in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise GGUFEmbeddingError(
                f"malformed embedding response: {exc}"
            ) from exc
        return vectors


# ---------------------------------------------------------------------------
# Process-wide default manager + config factory
# ---------------------------------------------------------------------------

_default_manager: LlamaServerManager | None = None


def get_default_manager() -> LlamaServerManager:
    """Return the process-wide manager (max_servers=1 → sequential load/unload)."""
    global _default_manager
    if _default_manager is None:
        _default_manager = LlamaServerManager(max_servers=1)
    return _default_manager


def create_embedding_provider(config: Any) -> EmbeddingProvider:
    """Build the embedding provider from app config (attribute-access duck typing).

    Uses the local GGUF path when both ``tutor_llama_bin`` and
    ``tutor_embed_gguf`` are configured; otherwise falls back to
    :class:`OllamaEmbeddingProvider`. Construction performs no subprocess
    spawns and no network calls — servers start lazily on first embed.
    """
    llama_bin = getattr(config, "tutor_llama_bin", "") or ""
    embed_gguf = getattr(config, "tutor_embed_gguf", "") or ""

    if llama_bin and embed_gguf:
        models_dir = getattr(config, "tutor_llama_models_dir", "") or ""
        gguf_path = Path(embed_gguf)
        if gguf_path.is_absolute():
            model_path = str(gguf_path)
        elif models_dir:
            model_path = str(Path(models_dir) / gguf_path)
        else:
            model_path = embed_gguf
        timeout_s = float(getattr(config, "tutor_llama_health_timeout_s", 120))
        return GGUFEmbeddingProvider(
            get_default_manager(),
            binary_path=llama_bin,
            model_path=model_path,
            timeout_s=timeout_s,
        )

    from ...client import OllamaClient  # Local import keeps module import light.

    model = getattr(config, "tutor_embedding_model", "") or "embeddinggemma"
    return OllamaEmbeddingProvider(client=OllamaClient(), model=model)
