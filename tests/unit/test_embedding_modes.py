"""US2 P0-B (T015) — modes d'embedding auto/eager/skip.

NoOp (vecteurs nuls), Fallback (chaîne + log), dimension auto-détectée et
persistée, changement de modèle ⇒ ré-indexation signalée. 100% offline :
aucun réseau, aucun processus, backends factices en mémoire.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src.ollama_tutor.tutor.providers.embedding_registry import (
    DelegatedEmbeddingProvider,
    EmbeddingProvider,
    FallbackEmbeddingProvider,
    NoOpEmbeddingProvider,
    check_reindex,
    get_embedding_provider,
    reset_embedding_provider,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_embedding_provider()
    yield
    reset_embedding_provider()


class _ScriptedProvider(EmbeddingProvider):
    """Contract-shaped fake: preset vectors or preset failure."""

    def __init__(
        self,
        name: str = "fake",
        width: int = 4,
        fill: float = 0.5,
        fail_with: BaseException | None = None,
    ) -> None:
        self._name = name
        self._width = width
        self._fill = fill
        self._fail_with = fail_with
        self.calls = 0

    @property
    def dimension(self) -> int:
        return self._width

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        return [self._fill] * self._width

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


class _FakeCPUBackend:
    """Mimics GGUF/Ollama backends: embed(list[str]) -> list[vec], lazy dims."""

    def __init__(self, width: int = 3) -> None:
        self._width = width
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t) + i) for i in range(self._width)] for t in texts]


# --- modes ---------------------------------------------------------------

def test_skip_mode_returns_noop_zero_vectors():
    provider = get_embedding_provider("skip")
    assert isinstance(provider, NoOpEmbeddingProvider)


@pytest.mark.asyncio
async def test_noop_vectors_are_zero_and_dimension_consistent():
    provider = NoOpEmbeddingProvider(dimension=8)
    assert provider.dimension == 8
    vec = await provider.embed("n'importe quoi")
    assert vec == [0.0] * 8
    batch = await provider.embed_batch(["a", "b", "c"])
    assert batch == [[0.0] * 8] * 3


def test_auto_defaults_to_skip_without_backend():
    provider = get_embedding_provider()
    assert isinstance(provider, NoOpEmbeddingProvider)


def test_auto_uses_configured_provider():
    fake = _ScriptedProvider()
    provider = get_embedding_provider("auto", providers=[("cpu", fake)])
    assert provider is fake


def test_eager_uses_configured_cpu_provider():
    fake = _ScriptedProvider()
    provider = get_embedding_provider("eager", providers=[("cpu", fake)])
    assert provider is fake


def test_eager_without_backend_raises():
    with pytest.raises(RuntimeError):
        get_embedding_provider("eager")


def test_invalid_mode_raises_value_error():
    with pytest.raises(ValueError):
        get_embedding_provider("turbo")


def test_singleton_cached_and_resettable():
    first = get_embedding_provider("skip")
    assert get_embedding_provider("skip") is first
    reset_embedding_provider()
    assert get_embedding_provider("skip") is not first


# --- FallbackEmbeddingProvider --------------------------------------------

@pytest.mark.asyncio
async def test_fallback_chain_uses_backup_and_logs(caplog: pytest.LogCaptureFixture):
    primary = _ScriptedProvider(name="primary", fail_with=ConnectionError("down"))
    backup = _ScriptedProvider(name="backup", fill=0.25)
    chain = FallbackEmbeddingProvider([("primary", primary), ("backup", backup)])
    with caplog.at_level(logging.INFO):
        vec = await chain.embed("hello")
    assert vec == [0.25] * 4
    assert primary.calls == 1 and backup.calls == 1
    assert any("backup" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fallback_batch_preserves_order():
    primary = _ScriptedProvider(name="primary", width=2, fail_with=TimeoutError("slow"))
    backup = _ScriptedProvider(name="backup", width=2, fill=1.0)
    chain = FallbackEmbeddingProvider([("primary", primary), ("backup", backup)])
    assert await chain.embed_batch(["a", "b"]) == [[1.0, 1.0], [1.0, 1.0]]
    assert chain.dimension == 2


@pytest.mark.asyncio
async def test_fallback_all_fail_raises():
    chain = FallbackEmbeddingProvider(
        [
            ("a", _ScriptedProvider(name="a", fail_with=ConnectionError("x"))),
            ("b", _ScriptedProvider(name="b", fail_with=OSError("y"))),
        ]
    )
    with pytest.raises(RuntimeError):
        await chain.embed("hello")


def test_fallback_empty_chain_raises():
    with pytest.raises(RuntimeError):
        FallbackEmbeddingProvider([])


# --- DelegatedEmbeddingProvider (adaptateur backends CPU) ------------------

@pytest.mark.asyncio
async def test_delegated_dimension_autodetected_and_persisted():
    state: dict[str, Any] = {}
    backend = _FakeCPUBackend(width=3)
    provider = DelegatedEmbeddingProvider(backend, model="embeddinggemma", state=state)
    with pytest.raises(RuntimeError):
        _ = provider.dimension  # inconnue avant le premier appel
    vec = await provider.embed("salut")
    assert vec == [5.0, 6.0, 7.0]
    assert provider.dimension == 3
    assert state["embedding_dimension"] == 3
    assert state["embedding_model"] == "embeddinggemma"
    assert provider.needs_reindex is False  # premier run : rien à ré-indexer


@pytest.mark.asyncio
async def test_delegated_batch_order_and_single_backend_call():
    backend = _FakeCPUBackend(width=2)
    provider = DelegatedEmbeddingProvider(backend, model="m")
    batch = await provider.embed_batch(["ab", "abcd"])
    assert batch == [[2.0, 3.0], [4.0, 5.0]]
    assert len(backend.calls) == 1


def test_check_reindex_signals_model_or_dimension_change():
    state: dict[str, Any] = {}
    assert check_reindex(state, model="a", dimension=3) is False  # premier run
    assert check_reindex(state, model="a", dimension=3) is False  # inchangé
    assert check_reindex(state, model="b", dimension=3) is True  # modèle changé
    assert check_reindex(state, model="b", dimension=5) is True  # dimension changée


@pytest.mark.asyncio
async def test_delegated_flags_reindex_on_backend_change():
    state: dict[str, Any] = {"embedding_model": "old", "embedding_dimension": 3}
    provider = DelegatedEmbeddingProvider(_FakeCPUBackend(3), model="new", state=state)
    await provider.embed("x")
    assert provider.needs_reindex is True


@pytest.mark.asyncio
async def test_factory_wraps_multiple_providers_in_fallback():
    a = _ScriptedProvider(name="a")
    b = _ScriptedProvider(name="b")
    provider = get_embedding_provider("eager", providers=[("a", a), ("b", b)])
    assert isinstance(provider, FallbackEmbeddingProvider)
    assert (await provider.embed("x")) == [0.5] * 4
