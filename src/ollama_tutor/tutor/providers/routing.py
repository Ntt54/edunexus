"""Model-based LLM routing (per-call backend choice).

Prod bug: with an OpenAI-compatible provider active, selecting an Ollama
model answered 400 « Unable to determine provider… » — the single
``tutor_client`` followed the provider, not the chosen model, while the
model list already merges both catalogs for switching.

Rule: a name present in the Ollama list ⇒ Ollama client (an ``openai/``
prefix is tolerated and stripped before sending to Ollama, never
invented); otherwise ⇒ the configured cloud provider, with fallback to
the other backend when unhealthy. The provider switch stays the default
(unknown catalogs). Embeddings never go through here (always Ollama).

Stdlib-only by design (Constitution V, NFR-003): backends are
duck-typed (``chat_stream``/``list_models``/``close``), no fastapi/textual.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

#: Seconds a failed backend stays sidelined before a retry.
ROUTE_COOLDOWN_S = 60.0

#: Seconds a model catalog is cached (refresh is one local + one cloud call).
MODEL_CACHE_TTL_S = 60.0


class LLMRoutingError(Exception):
    """Raised when no backend can serve a model (explicit, local)."""


def _model_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("name", ""))
    return str(getattr(entry, "name", entry))


def _strip_openai_prefix(name: str) -> str:
    if name.lower().startswith("openai/"):
        return name[len("openai/") :]
    return name


class RoutingLLMClient:
    """``chat_stream``-compatible router: the MODEL picks the backend.

    Wraps an Ollama client and an optional cloud client. Refreshes both
    model catalogs lazily (TTL, best-effort, cached on failure) and tracks
    per-backend health with cooldown + fallback to the other backend.
    """

    def __init__(
        self,
        ollama_client: Any,
        cloud_client: Any | None = None,
        default: str = "ollama",
        model_cache_ttl: float = MODEL_CACHE_TTL_S,
    ) -> None:
        self._ollama = ollama_client
        self._cloud = cloud_client
        self.default = default if default in ("ollama", "cloud") else "ollama"
        self._ttl = max(1.0, float(model_cache_ttl))
        self._ollama_names: set[str] = set()
        self._cloud_names: set[str] | None = None
        self._refreshed_at = 0.0
        self._unhealthy_until: dict[str, float] = {}

    # -- accessors ---------------------------------------------------

    @property
    def ollama_client(self) -> Any:
        return self._ollama

    @property
    def cloud_client(self) -> Any | None:
        return self._cloud

    # -- health ------------------------------------------------------

    def mark_unhealthy(self, backend: str) -> None:
        self._unhealthy_until[backend] = time.monotonic() + ROUTE_COOLDOWN_S
        logger.warning("LLM route %s unhealthy, cooldown %ds", backend, int(ROUTE_COOLDOWN_S))

    def mark_healthy(self, backend: str) -> None:
        self._unhealthy_until.pop(backend, None)

    def is_healthy(self, backend: str) -> bool:
        until = self._unhealthy_until.get(backend, 0.0)
        if until and time.monotonic() >= until:
            self._unhealthy_until.pop(backend, None)
            return True
        return not bool(until and time.monotonic() < until)

    # -- catalogs ----------------------------------------------------

    async def refresh_models(self, force: bool = False) -> None:
        """Refresh cached catalogs (TTL, best-effort, keeps old on failure)."""
        if not force and time.monotonic() - self._refreshed_at < self._ttl:
            return
        if self._ollama is not None and hasattr(self._ollama, "list_models"):
            try:
                names = {
                    _model_name(m).lower()
                    for m in await self._ollama.list_models()
                }
                self._ollama_names = {n for n in names if n}
            except Exception as exc:
                logger.warning("LLM route: Ollama catalog refresh failed: %s", exc)
        if self._cloud is not None and hasattr(self._cloud, "list_models"):
            try:
                names = {
                    _model_name(m).lower()
                    for m in await self._cloud.list_models()
                }
                self._cloud_names = {n for n in names if n}
            except Exception as exc:
                logger.warning("LLM route: cloud catalog refresh failed: %s", exc)
        self._refreshed_at = time.monotonic()

    # -- decision ----------------------------------------------------

    def client_for(self, model: Any) -> tuple[Any, str, str]:
        """Return ``(client, effective_model, backend)`` for *model*.

        Ollama-listed name ⇒ Ollama (``openai/`` prefix stripped for the
        send); otherwise ⇒ configured cloud; unknown on both known
        catalogs ⇒ :exc:`LLMRoutingError` (explicit, no faraway 400).
        Without a cloud, legacy behavior holds (everything to Ollama).
        Empty model ⇒ configured default, untouched.
        """
        name = str(model or "").strip()
        if not name:
            return self._default_client()
        base = _strip_openai_prefix(name)
        if base and base.lower() in self._ollama_names:
            if self._ollama is None:
                raise LLMRoutingError(f"Aucun backend Ollama pour le modèle {name!r}")
            return (self._ollama, base, "ollama")
        if self._cloud is not None:
            known = self._cloud_names
            if known and name.lower() not in known and base.lower() not in known:
                raise LLMRoutingError(
                    f"Modèle inconnu : {name!r} (ni dans le catalogue Ollama "
                    "ni chez le fournisseur configuré)"
                )
            return (self._cloud, name, "cloud")
        if self._ollama is None:
            raise LLMRoutingError(f"Aucun backend LLM pour le modèle {name!r}")
        return (self._ollama, base or name, "ollama")

    def _default_client(self) -> tuple[Any, str, str]:
        if self.default == "cloud" and self._cloud is not None:
            return (self._cloud, "", "cloud")
        if self._ollama is None:
            raise LLMRoutingError("Aucun backend LLM configuré")
        return (self._ollama, "", "ollama")

    def _other(self, backend: str) -> tuple[Any, str] | None:
        if backend == "ollama" and self._cloud is not None:
            return (self._cloud, "cloud")
        if backend == "cloud" and self._ollama is not None:
            return (self._ollama, "ollama")
        return None

    # -- generation --------------------------------------------------

    async def chat_stream(
        self, messages: Any, model: Any = None, **kwargs: Any
    ):
        """Yield backend events for *model*, with fallback on failure."""
        await self.refresh_models()
        try:
            primary, effective, backend = self.client_for(model)
        except LLMRoutingError:
            raise
        candidates: list[tuple[Any, str, str]] = []
        if self.is_healthy(backend):
            candidates.append((primary, effective, backend))
        other = self._other(backend)
        if other is not None and self.is_healthy(other[1]):
            # Same model name forwarded (Ollama answers its own error for
            # truly unknown names; a single backend is always attempted).
            candidates.append((other[0], effective, other[1]))
        if not candidates:
            if self._cloud is not None or self._ollama is not None:
                # Everything sidelined: still try the nominal backend once
                # (cooldown may just have expired) before giving up.
                candidates.append((primary, effective, backend))
            else:
                raise LLMRoutingError("Aucun backend LLM configuré")
        last_exc: Exception | None = None
        for client, eff_model, be in candidates:
            try:
                async for ev in client.chat_stream(messages, eff_model, **kwargs):
                    yield ev
                self.mark_healthy(be)
                return
            except Exception as exc:
                self.mark_unhealthy(be)
                last_exc = exc
                logger.warning("LLM route %s failed for %r: %s", be, model, exc)
        raise LLMRoutingError(
            f"Génération impossible pour le modèle {model!r} : {last_exc}"
        ) from last_exc

    async def close(self) -> None:
        """Close the cloud client only (shared Ollama client stays alive)."""
        cloud, ollama = self._cloud, self._ollama
        if cloud is not None and cloud is not ollama:
            try:
                closer = getattr(cloud, "close", None)
                if callable(closer):
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as exc:
                logger.warning("LLM route: cloud close failed: %s", exc)


__all__ = ["LLMRoutingError", "MODEL_CACHE_TTL_S", "ROUTE_COOLDOWN_S", "RoutingLLMClient"]
