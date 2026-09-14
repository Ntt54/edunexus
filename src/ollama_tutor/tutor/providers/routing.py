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
import dataclasses
import json
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
        """Refresh cached catalogs (TTL, best-effort, keeps old on failure).

        A failed or empty listing never wipes a known catalog.
        """
        if not force and time.monotonic() - self._refreshed_at < self._ttl:
            return
        if self._ollama is not None and hasattr(self._ollama, "list_models"):
            try:
                names = {
                    _model_name(m).lower()
                    for m in await self._ollama.list_models()
                }
                names.discard("")
                if names:
                    self._ollama_names = names
            except Exception as exc:
                logger.warning("LLM route: Ollama catalog refresh failed: %s", exc)
        if self._cloud is not None and hasattr(self._cloud, "list_models"):
            try:
                names = {
                    _model_name(m).lower()
                    for m in await self._cloud.list_models()
                }
                names.discard("")
                if names:
                    self._cloud_names = names
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

    def iter_chat_tokens(
        self,
        messages: Any,
        model: Any,
        *,
        sync: SyncRouteContext,
        timeout: float,
        extra: dict[str, Any] | None = None,
    ):
        """Sync generator yielding ``("thinking"|"token", text)`` routed by model.

        Same decision/fallback rule as :meth:`chat_sync_with_thinking`
        (Ollama NDJSON ``stream:true``, cloud OpenAI SSE), but streams
        provider chunks as they arrive instead of returning final text.
        A failure BEFORE any chunk falls back to the other configured
        backend; once chunks flowed, the error propagates (partial text
        already yielded — the caller emits an explicit error event, never
        silent). Raw ``httpx`` errors propagate like :meth:`chat_sync`.
        """
        import httpx as _httpx

        payload_messages = _coerce_sync_messages(messages)
        with _httpx.Client(
            timeout=_httpx.Timeout(timeout), transport=sync.transport
        ) as http:
            self.refresh_models_sync(http, sync)
            backend, _effective, raw_name = self._decide_sync(model, sync)
            order: list[str] = [backend]
            other = "cloud" if backend == "ollama" else "ollama"
            other_ok = (
                (other == "cloud" and sync.cloud_base_url)
                or (other == "ollama" and sync.ollama_base_url)
            )
            if other_ok and other != backend and self.is_healthy(other):
                order.append(other)
            yielded_any = False
            last_exc: Exception | None = None
            for be in order:
                eff_model = _strip_openai_prefix(raw_name) if be == "ollama" else raw_name
                if not eff_model:
                    eff_model = _strip_openai_prefix(str(model or "").strip())
                try:
                    if be == "ollama":
                        chunks = _iter_ollama_tokens(
                            http, sync.ollama_base_url, eff_model,
                            payload_messages, extra,
                        )
                    else:
                        chunks = _iter_cloud_tokens(
                            http, sync, eff_model, payload_messages,
                        )
                    for kind, text in chunks:
                        yielded_any = True
                        yield kind, text
                    self.mark_healthy(be)
                    return
                except SyncChatHTTPError:
                    raise
                except Exception as exc:
                    self.mark_unhealthy(be)
                    if yielded_any:
                        raise
                    last_exc = exc
                    logger.warning("LLM route (sync stream) %s failed for %r: %s", be, model, exc)
            if last_exc is not None and len(order) == 1:
                raise last_exc
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

    # -- synchronous path (lesson hook: no asyncio allowed) --------------

    def refresh_models_sync(self, http: Any, sync: SyncRouteContext) -> None:
        """Sync catalog refresh into the shared caches (TTL, best-effort).

        ``http`` is a sync ``httpx.Client`` (injected transport in tests).
        A failed or empty listing never wipes a known catalog.
        """
        if time.monotonic() - self._refreshed_at < self._ttl:
            return
        if sync.ollama_base_url:
            try:
                resp = http.get(f"{sync.ollama_base_url.rstrip('/')}/api/tags")
                if resp.status_code == 200:
                    names = {
                        str(m.get("name", "")).lower()
                        for m in (resp.json().get("models") or [])
                        if isinstance(m, dict)
                    }
                    names.discard("")
                    if names:
                        self._ollama_names = names
            except Exception as exc:
                logger.warning("LLM route (sync): Ollama catalog failed: %s", exc)
        if sync.cloud_base_url:
            try:
                headers = (
                    {"Authorization": f"Bearer {sync.cloud_api_key}"}
                    if sync.cloud_api_key
                    else {}
                )
                resp = http.get(
                    f"{sync.cloud_base_url.rstrip('/')}/models", headers=headers
                )
                if resp.status_code == 200:
                    names = {
                        str(m.get("id", "")).lower()
                        for m in (resp.json().get("data") or [])
                        if isinstance(m, dict)
                    }
                    names.discard("")
                    if names:
                        self._cloud_names = names
            except Exception as exc:
                logger.warning("LLM route (sync): cloud catalog failed: %s", exc)
        self._refreshed_at = time.monotonic()

    def _decide_sync(
        self, model: Any, sync: SyncRouteContext
    ) -> tuple[str, str, str]:
        """Sync decision from live config (URLs), not frozen clients.

        Same rule as :meth:`client_for` (Ollama name ⇒ Ollama with
        ``openai/`` stripped, else configured cloud, unknown on both known
        catalogs ⇒ :exc:`LLMRoutingError`). Without a cloud, legacy holds
        (everything to Ollama, as before). Empty model ⇒ configured
        default (cloud when a cloud base URL is set).
        """
        name = str(model or "").strip()
        if not name:
            if sync.cloud_base_url:
                return ("cloud", name, name)
            if sync.ollama_base_url:
                return ("ollama", name, name)
            raise LLMRoutingError("Aucun backend LLM configuré")
        base = _strip_openai_prefix(name)
        if base and base.lower() in self._ollama_names:
            if not sync.ollama_base_url:
                raise LLMRoutingError(f"Aucun backend Ollama pour le modèle {name!r}")
            return ("ollama", base, name)
        if sync.cloud_base_url:
            known = self._cloud_names
            if known and name.lower() not in known and base.lower() not in known:
                raise LLMRoutingError(
                    f"Modèle inconnu : {name!r} (ni dans le catalogue Ollama "
                    "ni chez le fournisseur configuré)"
                )
            return ("cloud", name, name)
        if sync.ollama_base_url:
            return ("ollama", base or name, name)
        raise LLMRoutingError(f"Aucun backend LLM pour le modèle {name!r}")

    def chat_sync(
        self,
        messages: Any,
        model: Any,
        *,
        sync: SyncRouteContext,
        timeout: float,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Non-streaming sync chat routed by model (one HTTP client).

        Same rule as :meth:`chat_sync_with_thinking`, thinking discarded.
        Contract kept: returns the text (``str``).
        """
        text, _ = self.chat_sync_with_thinking(
            messages, model, sync=sync, timeout=timeout, extra=extra
        )
        return text

    def chat_sync_with_thinking(
        self,
        messages: Any,
        model: Any,
        *,
        sync: SyncRouteContext,
        timeout: float,
        extra: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Non-streaming sync chat routed by model, with provider thinking.

        Same rule as :meth:`chat_stream` (Ollama name ⇒ Ollama with
        ``openai/`` stripped, else configured cloud, unknown ⇒
        :exc:`LLMRoutingError`). Returns ``(text, thinking)`` — thinking is
        the provider's reasoning when supplied (Ollama ``thinking``, cloud
        ``reasoning_content``/``reasoning``), else ``""``. Transient
        failures (network, timeout, HTTP 5xx/429) fall back to the other
        configured backend; HTTP 4xx raises :exc:`SyncChatHTTPError`
        directly. With a single backend, the real error surfaces unmasked
        (legacy behavior). Raw ``httpx`` errors propagate (the caller maps
        them, as before).
        """
        import httpx as _httpx

        payload_messages = _coerce_sync_messages(messages)
        with _httpx.Client(
            timeout=_httpx.Timeout(timeout), transport=sync.transport
        ) as http:
            self.refresh_models_sync(http, sync)
            backend, effective, raw_name = self._decide_sync(model, sync)
            order: list[str] = [backend]
            other = "cloud" if backend == "ollama" else "ollama"
            other_ok = (
                (other == "cloud" and sync.cloud_base_url)
                or (other == "ollama" and sync.ollama_base_url)
            )
            if other_ok and other != backend and self.is_healthy(other):
                order.append(other)
            last_exc: Exception | None = None
            for be in order:
                # Per-backend effective name (stripped for Ollama only).
                eff_model = _strip_openai_prefix(raw_name) if be == "ollama" else raw_name
                if not eff_model:
                    eff_model = effective
                try:
                    if be == "ollama":
                        text, thinking = _post_ollama_chat_full(
                            http, sync.ollama_base_url, eff_model,
                            payload_messages, extra,
                        )
                    else:
                        text, thinking = _post_cloud_chat_full(
                            http, sync, eff_model, payload_messages,
                        )
                    self.mark_healthy(be)
                    return text, thinking
                except SyncChatHTTPError:
                    # HTTP 4xx: request error, not backend health — no marking.
                    raise
                except Exception as exc:
                    self.mark_unhealthy(be)
                    last_exc = exc
                    logger.warning("LLM route (sync) %s failed for %r: %s", be, model, exc)
            if last_exc is not None and len(order) == 1:
                raise last_exc
            raise LLMRoutingError(
                f"Génération impossible pour le modèle {model!r} : {last_exc}"
            ) from last_exc


class SyncChatHTTPError(Exception):
    """Sync chat HTTP failure (status kept for the caller's mapping)."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status
        self.text = text


@dataclasses.dataclass
class SyncRouteContext:
    """Sync transport coordinates (no network at construction).

    ``transport`` is an ``httpx`` sync transport (tests) or ``None``
    (production: real connection).
    """

    ollama_base_url: str
    cloud_base_url: str | None = None
    cloud_api_key: str | None = None
    transport: Any | None = None


def _iter_ollama_tokens(
    http: Any, base_url: str, model: str,
    messages: list[dict[str, str]], extra: dict[str, Any] | None,
):
    """Yield ``("thinking"|"token", text)`` from Ollama NDJSON ``stream:true``."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if extra:
        payload.update(extra)
    with http.stream("POST", f"{base_url.rstrip('/')}/api/chat", json=payload) as resp:
        if resp.status_code != 200:
            raise SyncChatHTTPError(resp.status_code, resp.read().decode("utf-8", "replace")[:500])
        for line in resp.iter_lines():
            s = (line or "").strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("done"):
                return
            message = obj.get("message") or {}
            if not isinstance(message, dict):
                continue
            thinking = message.get("thinking", "")
            if isinstance(thinking, str) and thinking.strip():
                yield "thinking", thinking
            content = message.get("content", "")
            if isinstance(content, str) and content:
                yield "token", content


def _iter_cloud_tokens(
    http: Any, sync: SyncRouteContext, model: str,
    messages: list[dict[str, str]],
):
    """Yield ``("thinking"|"token", text)`` from OpenAI SSE ``stream:true``."""
    assert sync.cloud_base_url, "no cloud backend configured"
    headers = (
        {"Authorization": f"Bearer {sync.cloud_api_key}"}
        if sync.cloud_api_key
        else {}
    )
    with http.stream(
        "POST",
        f"{sync.cloud_base_url.rstrip('/')}/chat/completions",
        json={"model": model, "messages": messages, "stream": True},
        headers=headers,
    ) as resp:
        if resp.status_code != 200:
            raise SyncChatHTTPError(resp.status_code, resp.read().decode("utf-8", "replace")[:500])
        for line in resp.iter_lines():
            s = (line or "").strip()
            if not s.startswith("data:"):
                continue
            payload = s[len("data:"):].strip()
            if payload == "[DONE]":
                return
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            choices = obj.get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices else {}
            if not isinstance(delta, dict):
                continue
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if isinstance(reasoning, str) and reasoning.strip():
                yield "thinking", reasoning
            content = delta.get("content") or ""
            if isinstance(content, str) and content:
                yield "token", content


def _coerce_sync_messages(messages: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in messages or []:
        m: Any = raw
        if isinstance(m, dict) and "content" in m:
            out.append({"role": str(m.get("role", "user")), "content": str(m.get("content", ""))})
        elif hasattr(m, "to_dict"):
            d: Any = m.to_dict()
            out.append({"role": str(d.get("role", "user")), "content": str(d.get("content", ""))})
        elif hasattr(m, "role") and hasattr(m, "content"):
            out.append({"role": str(m.role), "content": str(m.content)})
        else:
            out.append({"role": "user", "content": str(m)})
    return out


def _post_ollama_chat_full(
    http: Any, base_url: str, model: str,
    messages: list[dict[str, str]], extra: dict[str, Any] | None,
) -> tuple[str, str]:
    """Ollama `/api/chat` non-stream → `(text, thinking)` (thinking peut être absent)."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if extra:
        payload.update(extra)
    resp = http.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
    if resp.status_code != 200:
        raise SyncChatHTTPError(resp.status_code, resp.text)
    try:
        data = resp.json()
    except Exception as exc:
        raise SyncChatHTTPError(resp.status_code, f"invalid JSON: {exc}")
    message = data.get("message") if isinstance(data, dict) else None
    text = message.get("content", "") if isinstance(message, dict) else ""
    thinking = message.get("thinking", "") if isinstance(message, dict) else ""
    if not isinstance(text, str) or not text.strip():
        raise SyncChatHTTPError(resp.status_code, "empty lesson content")
    return text, (thinking if isinstance(thinking, str) else "")


def _post_ollama_chat(
    http: Any, base_url: str, model: str,
    messages: list[dict[str, str]], extra: dict[str, Any] | None,
) -> str:
    return _post_ollama_chat_full(http, base_url, model, messages, extra)[0]


def _post_cloud_chat_full(
    http: Any, sync: SyncRouteContext, model: str,
    messages: list[dict[str, str]],
) -> tuple[str, str]:
    """OpenAI `/chat/completions` non-stream → `(text, thinking)`.

    Thinking capté via `reasoning_content` (DeepSeek/QwQ…) puis `reasoning`,
    chaînes vides si absents.
    """
    assert sync.cloud_base_url, "no cloud backend configured"
    headers = (
        {"Authorization": f"Bearer {sync.cloud_api_key}"}
        if sync.cloud_api_key
        else {}
    )
    resp = http.post(
        f"{sync.cloud_base_url.rstrip('/')}/chat/completions",
        json={"model": model, "messages": messages, "stream": False},
        headers=headers,
    )
    if resp.status_code != 200:
        raise SyncChatHTTPError(resp.status_code, resp.text)
    try:
        data = resp.json()
        message = data["choices"][0]["message"]
        text = message["content"]
    except Exception as exc:
        raise SyncChatHTTPError(resp.status_code, f"réponse cloud illisible: {exc}")
    if not isinstance(text, str) or not text.strip():
        raise SyncChatHTTPError(resp.status_code, "empty lesson content")
    thinking = ""
    if isinstance(message, dict):
        thinking = message.get("reasoning_content") or message.get("reasoning") or ""
    return text, (thinking if isinstance(thinking, str) else "")


def _post_cloud_chat(
    http: Any, sync: SyncRouteContext, model: str,
    messages: list[dict[str, str]],
) -> str:
    return _post_cloud_chat_full(http, sync, model, messages)[0]


__all__ = [
    "LLMRoutingError",
    "MODEL_CACHE_TTL_S",
    "ROUTE_COOLDOWN_S",
    "RoutingLLMClient",
    "SyncChatHTTPError",
    "SyncRouteContext",
]
