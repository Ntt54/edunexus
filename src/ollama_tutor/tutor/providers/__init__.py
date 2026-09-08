"""Provider interfaces + Ollama adapters for the CPU/GGUF pipeline (Granite models).

US2 P0-B (T018) wiring: lazy factories over the existing CPU backends.

Everything here is lazy by contract (FR-004/005, NFR-006): importing this
module — or building adapters / the default registry — opens no connection
and spawns no process. Backends (``OllamaClient``, ``llama-server``) start
only on the first real ``chat`` / ``embed`` call.

- LLM side: duck-typed :class:`LLMClient` adapters over the existing CPU
  providers (Ollama via ``client.py``, GGUF ``llama-server``, OpenAI-compat)
  plus :class:`MockLLMClient` for offline tests. :func:`create_default_registry`
  exposes the default :class:`ProviderRegistry` (``ollama`` primary →
  ``gguf`` fallback, ``openai`` only when an API key / base URL is
  configured, ``mock`` only on explicit request).
- Embedding side: :func:`resolve_embedding_mode` + :func:`get_default_embedding_provider`.
  Default ``auto`` resolves to ``skip`` (zero-cost null vectors) when neither
  an API key nor a fast local backend is configured (plain CPU box, NFR-006);
  real embeddings go through the existing CPU backends wrapped in
  :class:`DelegatedEmbeddingProvider`. ``sentence-transformers`` / ``torch``
  are never imported here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import (
    DocumentParser,
    EmbeddingProvider,
    LLMProvider,
    OCRProvider,
    VectorStore,
)
from .embedding_registry import (
    EMBEDDING_MODES,
    NOOP_DIMENSION,
    DelegatedEmbeddingProvider,
    FallbackEmbeddingProvider,
    NoOpEmbeddingProvider,
    check_reindex,
    get_embedding_provider,
    reset_embedding_provider,
)
from .gguf_llm import GGUFLLMProvider, create_gguf_llm_provider
from .ollama_adapter import OllamaEmbeddingProvider, OllamaLLMProvider
from .pleias import PleiasRAGProvider, parse_pleias_response
from .registry import (
    CIRCUIT_OPEN_THRESHOLD,
    CIRCUIT_RESET_TIMEOUT,
    COOLDOWN_STEPS,
    LLMClient,
    LLMConfigurationError,
    MockLLMClient,
    ProviderRegistry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Duck-typed LLMClient adapters over the existing CPU backends (FR-004)
# ---------------------------------------------------------------------------


def _message_role(message: Any) -> str:
    """Return the role string of a ``Message`` or plain dict (duck-typed)."""
    if isinstance(message, dict):
        return str(message.get("role", "user"))
    role = getattr(message, "role", "user")
    return str(getattr(role, "value", role))


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", message))


class _BackendLLMAdapter(LLMClient):
    """Generic :class:`LLMClient` over an existing CPU backend (lazy).

    The backend is duck-typed: ``generate(prompt, system=..., options=...)``
    (e.g. :class:`OllamaLLMProvider`) is preferred, otherwise
    ``chat_stream(messages, model, ...)`` (e.g. :class:`GGUFLLMProvider`,
    :class:`OpenAICompatProvider`, ``OllamaClient``). Construction stores the
    reference only — no connection, no process.
    """

    def __init__(self, backend: Any, name: str = "cpu", model: str | None = None) -> None:
        super().__init__(name=name)
        self._backend = backend
        self._model = model or getattr(backend, "model", None) or getattr(
            backend, "model_name", None
        )

    @property
    def backend(self) -> Any:
        """The wrapped CPU backend (for introspection/tests)."""
        return self._backend

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    async def chat(self, messages: list[Any], **opts: Any) -> dict[str, Any]:
        try:
            content = await self._collect(messages, **opts)
        except Exception as exc:  # noqa: BLE001 - health tracking needs the message
            self.mark_unhealthy(str(exc))
            raise
        self.mark_healthy()
        usage = {
            "input_tokens": self._estimate_tokens(str(messages)),
            "output_tokens": self._estimate_tokens(content),
        }
        self._last_usage = usage
        return {"content": content, "usage": usage}

    async def _collect(self, messages: list[Any], **opts: Any) -> str:
        backend = self._backend
        if hasattr(backend, "generate"):
            system_parts = [
                _message_content(m) for m in (messages or []) if _message_role(m) == "system"
            ]
            prompt_parts = [
                _message_content(m)
                for m in (messages or [])
                if _message_role(m) != "system"
            ]
            system = "\n".join(system_parts) or None
            prompt = "\n\n".join(prompt_parts)
            options = opts.get("options", None)
            try:
                return await backend.generate(prompt, system=system, options=options)
            except TypeError:
                # Minimal generate(prompt) backends.
                return await backend.generate(prompt)
        if hasattr(backend, "chat_stream"):
            api_messages = [
                {"role": _message_role(m), "content": _message_content(m)}
                for m in (messages or [])
            ]
            model = opts.get("model", None) or self._model
            kwargs: dict[str, Any] = {}
            if opts.get("options", None) is not None:
                kwargs["options"] = opts["options"]
            stream = None
            if model is not None:
                try:
                    stream = backend.chat_stream(api_messages, model, **kwargs)
                except TypeError:
                    stream = None
            if stream is None:
                stream = backend.chat_stream(api_messages, **kwargs)
            parts: list[str] = []
            async for event in stream:
                if getattr(event, "kind", None) == "content":
                    parts.append(getattr(event, "text", ""))
            return "".join(parts)
        raise TypeError(
            f"Backend {type(backend).__name__!r} exposes neither generate() "
            "nor chat_stream(); cannot adapt to LLMClient."
        )


class OllamaAdapter(_BackendLLMAdapter):
    """ :class:`LLMClient` over the Ollama backend (``client.py`` based). """


class GGUFAdapter(_BackendLLMAdapter):
    """ :class:`LLMClient` over the GGUF ``llama-server`` backend. """


class OpenAICompatAdapter(_BackendLLMAdapter):
    """ :class:`LLMClient` over an OpenAI-compatible backend (key or local). """


# ---------------------------------------------------------------------------
# Lazy LLM factories (no connection / process at construction)
# ---------------------------------------------------------------------------


def create_ollama_adapter(
    config: Any = None,
    *,
    client: Any = None,
    model: str | None = None,
    transport: Any = None,
) -> OllamaAdapter:
    """Build the Ollama registry adapter (lazy: no HTTP call here)."""
    if client is None:
        from ...client import OllamaClient

        base_url = getattr(config, "ollama_base_url", "") or ""
        if transport is not None:
            client = (
                OllamaClient(base_url=base_url, transport=transport)
                if base_url
                else OllamaClient(transport=transport)
            )
        elif base_url:
            client = OllamaClient(base_url=base_url)
        else:
            client = OllamaClient()
    resolved_model = (
        model or getattr(config, "tutor_model", "") or getattr(client, "model", "") or "gemma4:e2b"
    )
    backend = OllamaLLMProvider(client, resolved_model)
    return OllamaAdapter(backend, name="ollama", model=resolved_model)


def create_gguf_adapter(
    config: Any = None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    transport: Any = None,
) -> GGUFAdapter:
    """Build the GGUF ``llama-server`` registry adapter (lazy: no spawn)."""
    if config is not None and model is None and base_url is None and transport is None:
        backend = create_gguf_llm_provider(config)
    else:
        resolved_base = (
            base_url
            or getattr(config, "llm_base_url", "") or "http://localhost:8080"
        )
        resolved_model = model or getattr(config, "tutor_llm_gguf", "") or None
        backend = GGUFLLMProvider(
            base_url=resolved_base, model=resolved_model, transport=transport
        )
    return GGUFAdapter(backend, name="gguf", model=getattr(backend, "model", model))


def create_openai_compat_adapter(
    config: Any = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    transport: Any = None,
) -> OpenAICompatAdapter:
    """Build the OpenAI-compatible registry adapter (lazy: no HTTP call)."""
    from .openai_compat import OpenAICompatProvider

    resolved_base = (
        base_url
        or getattr(config, "llm_base_url", "")
        or os.environ.get("OPENAI_BASE_URL", "")
    )
    resolved_key = (
        api_key
        if api_key is not None
        else getattr(config, "llm_api_key", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("LLM_API_KEY", "")
    )
    backend = OpenAICompatProvider(
        base_url=resolved_base, api_key=resolved_key, transport=transport
    )
    return OpenAICompatAdapter(backend, name="openai")


def create_mock_client(
    name: str = "mock", responses: list[str] | None = None
) -> MockLLMClient:
    """Build a scripted offline client (tests only, never hits the network)."""
    return MockLLMClient(name=name, responses=responses)


def _has_api_key(config: Any = None) -> bool:
    """True when an LLM API key is configured (config or environment)."""
    if config is not None and getattr(config, "llm_api_key", ""):
        return True
    return bool(os.environ.get("OPENAI_API_KEY", "") or os.environ.get("LLM_API_KEY", ""))


def create_default_registry(
    config: Any = None,
    *,
    include_mock: bool = False,
    mock_responses: list[str] | None = None,
    transport: Any = None,
) -> ProviderRegistry:
    """Build the default provider registry (lazy: no connection, no process).

    Registers the CPU adapters — ``ollama`` (primary) → ``gguf`` fallback —
    plus ``openai`` only when an API key / base URL is configured, plus a
    scripted ``mock`` client only on explicit request (tests).
    """
    registry = ProviderRegistry()
    registry.register("ollama", create_ollama_adapter(config, transport=transport), primary=True)
    registry.register("gguf", create_gguf_adapter(config, transport=transport))
    if _has_api_key(config) or getattr(config, "llm_base_url", ""):
        registry.register(
            "openai", create_openai_compat_adapter(config, transport=transport)
        )
    if include_mock:
        registry.register("mock", MockLLMClient(name="mock", responses=mock_responses))
    return registry


_default_registry: ProviderRegistry | None = None


def get_default_registry(config: Any = None) -> ProviderRegistry:
    """Return the process-wide default registry (built lazily on first call)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = create_default_registry(config)
    return _default_registry


def reset_default_registry() -> None:
    """Drop the cached default registry (tests / config switch)."""
    global _default_registry
    _default_registry = None


# ---------------------------------------------------------------------------
# Embedding mode resolution (FR-005 + NFR-006, CPU-only)
# ---------------------------------------------------------------------------


def resolve_embedding_mode(config: Any = None, explicit: str | None = None) -> str:
    """Resolve the embedding mode (``auto`` / ``eager`` / ``skip``).

    Precedence: explicit argument → ``EMBEDDING_MODE`` env → config attribute
    (``tutor_embedding_mode`` / ``embedding_mode``) → ``"auto"``.
    Raises :class:`ValueError` on unknown modes.
    """
    if explicit is not None:
        mode = explicit
    else:
        env_mode = os.environ.get("EMBEDDING_MODE", "").strip().lower()
        if env_mode:
            mode = env_mode
        else:
            mode = (
                getattr(config, "tutor_embedding_mode", None)
                or getattr(config, "embedding_mode", None)
                or "auto"
            )
    if mode not in EMBEDDING_MODES:
        raise ValueError(f"Unknown embedding mode: {mode!r} (expected auto|eager|skip).")
    return mode


def _has_fast_embedding_backend(config: Any = None) -> bool:
    """True when a real embedding backend is explicitly configured.

    Fast here means: an API key, an explicit remote base URL, or local GGUF
    embedding paths (``tutor_llama_bin`` + ``tutor_embed_gguf``). The plain
    Ollama default is deliberately NOT counted: on a bare CPU box ``auto``
    must resolve to ``skip`` (NFR-006) instead of attempting the network.
    """
    if config is None:
        return False
    if _has_api_key(config):
        return True
    if getattr(config, "llm_base_url", ""):
        return True
    return bool(
        getattr(config, "tutor_llama_bin", "")
        and getattr(config, "tutor_embed_gguf", "")
    )


def resolve_embedding_backend(config: Any = None) -> str:
    """Return which embedding backend ``auto`` would pick: ``"cpu"`` or ``"skip"``.

    ``"skip"`` on a CPU box with neither API key nor fast backend (NFR-006);
    ``"cpu"`` when an explicitly configured CPU backend (GGUF/Ollama/API)
    is available. Never imports ``sentence-transformers`` / ``torch``.
    """
    return "cpu" if _has_fast_embedding_backend(config) else "skip"


def get_default_embedding_provider(
    config: Any = None,
    *,
    mode: str | None = None,
    state: Any = None,
    providers: list[tuple[str, Any]] | None = None,
) -> Any:
    """Return the embedding provider for the resolved mode (lazy, CPU-only).

    - ``skip``: always :class:`NoOpEmbeddingProvider` (zero cost).
    - ``auto`` (default): the configured CPU backend wrapped in
      :class:`DelegatedEmbeddingProvider` when one is explicitly configured,
      else ``skip`` (plain CPU box, NFR-006).
    - ``eager``: the configured CPU backend, always — :class:`RuntimeError`
      when none is configured.

    No connection is opened and no process is spawned here; backends start
    on the first ``embed`` call. Delegates singleton caching to
    :func:`get_embedding_provider`.
    """
    resolved = resolve_embedding_mode(config, explicit=mode)
    chain: Any = list(providers) if providers else None
    if chain is None and resolved in ("auto", "eager") and _has_fast_embedding_backend(
        config
    ):
        from .gguf_embedding import create_embedding_provider as _create_cpu_backend

        try:
            cpu_backend = _create_cpu_backend(config) if config is not None else None
        except Exception:  # noqa: BLE001 - fall through to base factory error path
            cpu_backend = None
        if cpu_backend is not None:
            model_name = getattr(config, "tutor_embedding_model", "") or "embeddinggemma"
            chain = [
                (
                    "cpu",
                    DelegatedEmbeddingProvider(
                        cpu_backend, model=model_name, state=state
                    ),
                )
            ]
    return get_embedding_provider(resolved, providers=chain, state=state)


__all__ = [
    "CIRCUIT_OPEN_THRESHOLD",
    "CIRCUIT_RESET_TIMEOUT",
    "COOLDOWN_STEPS",
    "EMBEDDING_MODES",
    "NOOP_DIMENSION",
    "DelegatedEmbeddingProvider",
    "DocumentParser",
    "EmbeddingProvider",
    "FallbackEmbeddingProvider",
    "GGUFAdapter",
    "GGUFLLMProvider",
    "LLMClient",
    "LLMConfigurationError",
    "LLMProvider",
    "MockLLMClient",
    "NoOpEmbeddingProvider",
    "OCRProvider",
    "OllamaAdapter",
    "OllamaEmbeddingProvider",
    "OllamaLLMProvider",
    "OpenAICompatAdapter",
    "PleiasRAGProvider",
    "ProviderRegistry",
    "VectorStore",
    "check_reindex",
    "create_default_registry",
    "create_gguf_adapter",
    "create_gguf_llm_provider",
    "create_mock_client",
    "create_ollama_adapter",
    "create_openai_compat_adapter",
    "get_default_embedding_provider",
    "get_default_registry",
    "get_embedding_provider",
    "parse_pleias_response",
    "reset_default_registry",
    "reset_embedding_provider",
    "resolve_embedding_backend",
    "resolve_embedding_mode",
]
