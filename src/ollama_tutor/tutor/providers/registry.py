"""LLM provider registry with fallback + circuit breaker (US2 P0-B, FR-004).

Adapted from ``autreprojet/OpenTutor-main/apps/api/services/llm/``
(``router.py:50-230`` ``ProviderRegistry`` fallback chain,
``circuit_breaker.py:22-80`` progressive cooldown + circuit breaker,
``base_client.py:13-60`` ``LLMClient`` base,
``providers/mock_client.py`` scripted offline client).

Contract: ``specs/010-greffe-autreprojet/contracts/providers.md`` (section LLM).

Stdlib-only by design (Constitution V, NFR-003): no fastapi/textual,
no sentence-transformers/torch. Lazy startup: no process, no connection
is opened at construction time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# Progressive cooldown steps in seconds (openakita pattern).
COOLDOWN_STEPS = [5, 10, 20, 60]

# Consecutive failures before the circuit opens.
CIRCUIT_OPEN_THRESHOLD = 3

# Seconds before an open circuit is probed again (auto-reset).
CIRCUIT_RESET_TIMEOUT = 120


class LLMConfigurationError(Exception):
    """Raised when no LLM provider is configured (empty registry)."""


class CircuitBreakerMixin:
    """Health tracking mixin: progressive cooldown + circuit breaker.

    A provider in fault is sidelined (``is_healthy`` False) then retested
    after its cooldown / the circuit reset timeout.
    """

    provider_name: str = "base"

    def __init__(self) -> None:
        self._healthy = True
        self._cooldown_until: float = 0
        self._consecutive_failures: int = 0
        self._circuit_open: bool = False
        self._circuit_open_time: float = 0

    @property
    def is_healthy(self) -> bool:
        if self._circuit_open:
            if time.time() - self._circuit_open_time >= CIRCUIT_RESET_TIMEOUT:
                self._circuit_open = False
                self._healthy = True
                self._consecutive_failures = 0
                logger.info("Circuit breaker reset for %s", self.provider_name)
            else:
                return False
        if self._cooldown_until > 0 and time.time() >= self._cooldown_until:
            self._healthy = True
            self._cooldown_until = 0
        return self._healthy

    def mark_unhealthy(self, error: str) -> None:
        self._healthy = False
        self._consecutive_failures += 1
        if self._consecutive_failures >= CIRCUIT_OPEN_THRESHOLD:
            self._circuit_open = True
            self._circuit_open_time = time.time()
            logger.error(
                "Circuit OPEN for %s after %d failures: %s",
                self.provider_name,
                self._consecutive_failures,
                error,
            )
            return
        idx = min(self._consecutive_failures - 1, len(COOLDOWN_STEPS) - 1)
        cooldown = COOLDOWN_STEPS[idx]
        self._cooldown_until = time.time() + cooldown
        logger.warning(
            "LLM %s unhealthy: %s, cooldown %ds",
            self.provider_name,
            error,
            cooldown,
        )

    def mark_healthy(self) -> None:
        self._healthy = True
        self._consecutive_failures = 0
        self._cooldown_until = 0
        self._circuit_open = False

    async def ping(self) -> bool:
        """Active liveness check (default: cached health flag)."""
        return self.is_healthy


class LLMClient(CircuitBreakerMixin, ABC):
    """Abstract LLM client: circuit-breaker health + chat contract."""

    name: str = "base"

    def __init__(self, name: str = "base") -> None:
        super().__init__()
        self.name = name
        self.provider_name = name
        self._last_usage: dict[str, Any] = {}

    def get_last_usage(self) -> dict[str, Any]:
        """Token counters from the last call (observability)."""
        return dict(self._last_usage)

    @abstractmethod
    async def chat(self, messages: list[Any], **opts: Any) -> dict[str, Any]:
        """Non-streaming chat; returns ``{"content": str, "usage": dict}``."""
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """Scripted offline client (tests only, never hits the network)."""

    def __init__(
        self,
        name: str = "mock",
        responses: list[str] | None = None,
    ) -> None:
        super().__init__(name=name)
        self._responses = list(responses) if responses else []
        self._calls = 0

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    async def chat(self, messages: list[Any], **opts: Any) -> dict[str, Any]:
        self.mark_healthy()
        if self._responses:
            content = self._responses[min(self._calls, len(self._responses) - 1)]
        else:
            prompt = " ".join(
                m.get("content", "") if isinstance(m, dict) else str(m)
                for m in messages
            )
            content = f"[mock] no scripted response; got: {prompt}"
        self._calls += 1
        usage = {
            "input_tokens": self._estimate_tokens(str(messages)),
            "output_tokens": self._estimate_tokens(content),
        }
        self._last_usage = usage
        return {"content": content, "usage": usage}


class ProviderRegistry:
    """Registry of LLM providers with automatic fallback.

    Fallback chain: model variant (``large``/``small``/``fast``) ->
    requested provider -> primary -> registration (fallback) order,
    first healthy wins.
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMClient] = {}
        self._primary: str | None = None
        self._fallback_order: list[str] = []
        self._model_variants: dict[str, LLMClient] = {}

    def register(self, name: str, client: LLMClient, primary: bool = False) -> None:
        """Register a provider; ``primary=True`` marks the default."""
        self._providers[name] = client
        if primary:
            self._primary = name
        if name not in self._fallback_order:
            self._fallback_order.append(name)

    def register_variant(self, hint: str, client: LLMClient) -> None:
        """Register a model variant for size hints (``large``/``small``/``fast``)."""
        self._model_variants[hint] = client

    def get(self, name: str | None = None) -> LLMClient:
        """Return a healthy provider, with automatic fallback.

        Raises:
            LLMConfigurationError: registry is empty.
            RuntimeError: all providers are unhealthy.
        """
        if not self._providers:
            raise LLMConfigurationError(
                "No LLM provider is configured. Register a provider before "
                "using AI features."
            )
        if name and name in self._model_variants:
            client = self._model_variants[name]
            if client.is_healthy:
                return client
        if name and name not in self._providers:
            name = None
        if name and name in self._providers:
            client = self._providers[name]
            if client.is_healthy:
                return client
        if self._primary and self._primary in self._providers:
            client = self._providers[self._primary]
            if client.is_healthy:
                return client
        for provider_name in self._fallback_order:
            client = self._providers[provider_name]
            if client.is_healthy:
                logger.info("Falling back to %s", provider_name)
                return client
        raise RuntimeError(
            "All LLM providers are unhealthy. Please check backends and network."
        )

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    @property
    def primary_name(self) -> str | None:
        return self._primary

    @property
    def provider_health(self) -> dict[str, bool]:
        return {name: client.is_healthy for name, client in self._providers.items()}

    async def ping_all(self) -> dict[str, bool]:
        """Actively probe all providers, return live status per name."""
        results = await asyncio.gather(
            *(client.ping() for client in self._providers.values()),
            return_exceptions=True,
        )
        return {
            name: (result is True)
            for (name, _), result in zip(self._providers.items(), results)
        }


__all__ = [
    "CIRCUIT_OPEN_THRESHOLD",
    "CIRCUIT_RESET_TIMEOUT",
    "COOLDOWN_STEPS",
    "CircuitBreakerMixin",
    "LLMClient",
    "LLMConfigurationError",
    "MockLLMClient",
    "ProviderRegistry",
]
