"""Tests for US8 — parallel embedding batches (T052) and llama-server --parallel flag (T051)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from ollama_tutor.tutor.embeddings import embed_texts
from ollama_tutor.tutor.providers.llama_server import (
    LlamaServerConfig,
    LlamaServerManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProcess:
    """Stands in for asyncio.subprocess.Process; records argv."""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv

    def terminate(self) -> None:
        pass

    async def wait(self) -> int:
        return 0


def make_launcher(spawned: list[FakeProcess]) -> Callable[[list[str]], FakeProcess]:
    def launcher(argv: list[str]) -> FakeProcess:
        p = FakeProcess(argv)
        spawned.append(p)
        return p
    return launcher


async def ok_prober(url: str) -> bool:
    return True


class FakeEmbedClient:
    """Records embed() calls and returns deterministic vectors."""

    def __init__(
        self,
        vector_factory: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self._factory = vector_factory or _default_factory
        self.embed_calls: list[tuple[str, list[str]]] = []

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append((model, list(texts)))
        return self._factory(texts)


def _default_factory(texts: list[str]) -> list[list[float]]:
    """Return a vector per text based on length (deterministic)."""
    return [[float(len(t))] for t in texts]


class FakeStore:
    """Minimal cache backing store for embed_texts."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], list[float]] = {}

    def get_embedding(self, key: str, model: str) -> list[float] | None:
        return self._cache.get((key, model))

    def add_embedding(self, key: str, model: str, vector: list[float]) -> None:
        self._cache[(key, model)] = vector


def argv_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# T051 — LlamaServerManager.start() passes --parallel N
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_passes_parallel_flag_when_gt_1() -> None:
    """--parallel 4 appears in argv when config.parallel == 4."""
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)
    config = LlamaServerConfig(
        binary_path="/bin/llama-server",
        model_path="/m.gguf",
        port=9200,
        parallel=4,
    )

    server = await manager.start(config)

    assert "--parallel" in spawned[0].argv
    assert argv_value(spawned[0].argv, "--parallel") == "4"
    await server.stop()


@pytest.mark.asyncio
async def test_start_omits_parallel_flag_when_1() -> None:
    """--parallel is absent when config.parallel == 1 (default)."""
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)
    config = LlamaServerConfig(
        binary_path="/bin/llama-server",
        model_path="/m.gguf",
        port=9201,
    )

    server = await manager.start(config)

    assert "--parallel" not in spawned[0].argv
    await server.stop()


@pytest.mark.asyncio
async def test_parallel_comes_before_extra_args() -> None:
    """--parallel appears before user-supplied extra_args."""
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)
    config = LlamaServerConfig(
        binary_path="/bin/llama-server",
        model_path="/m.gguf",
        port=9202,
        parallel=2,
        extra_args=["-t", "4"],
    )

    server = await manager.start(config)

    argv = spawned[0].argv
    parallel_idx = argv.index("--parallel")
    extra_idx = argv.index("-t")
    assert parallel_idx < extra_idx
    await server.stop()


# ---------------------------------------------------------------------------
# T052 — embed_texts parallel batching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_embed_batch_size() -> None:
    """Chunks are split into batches of max_parallel_embed and sent concurrently."""
    # 7 chunks, batch_size=3 → batches of 3, 3, 1 = 3 embed() calls.
    chunks = [f"chunk-{i}" for i in range(7)]
    client = FakeEmbedClient()
    store = FakeStore()

    results = await embed_texts(
        client, "mymodel", chunks, store, max_parallel_embed=3
    )

    # All 7 chunks should have a vector.
    assert len(results) == 7
    for vec in results:
        assert len(vec) == 1  # factory returns [float(len(text))]

    # 3 batches → 3 embed() calls.
    assert len(client.embed_calls) == 3
    assert client.embed_calls[0][0] == "mymodel"
    # Batch sizes: 3, 3, 1.
    call_sizes = [len(t) for _, t in client.embed_calls]
    assert call_sizes == [3, 3, 1]


@pytest.mark.asyncio
async def test_embed_texts_single_still_works() -> None:
    """When max_parallel_embed=1 (default), all cache misses go in one call."""
    chunks = ["alpha", "beta", "gamma"]
    client = FakeEmbedClient()
    store = FakeStore()

    results = await embed_texts(client, "model1", chunks, store)

    assert len(results) == 3
    # All 3 in a single embed() call.
    assert len(client.embed_calls) == 1
    assert len(client.embed_calls[0][1]) == 3


@pytest.mark.asyncio
async def test_parallel_embed_respects_cache() -> None:
    """Cached chunks are skipped even in parallel mode; only misses are batched."""
    chunks = ["cached", "miss-a", "miss-b", "cached-too", "miss-c"]
    client = FakeEmbedClient()
    store = FakeStore()

    # Pre-populate cache for two chunks.
    from ollama_tutor.tutor.embeddings import _hash_text

    store.add_embedding(_hash_text("cached", "m"), "m", [1.0])
    store.add_embedding(_hash_text("cached-too", "m"), "m", [2.0])

    results = await embed_texts(client, "m", chunks, store, max_parallel_embed=2)

    assert results[0] == [1.0]  # cached
    assert results[3] == [2.0]  # cached
    # 3 cache misses, batch_size=2 → 2 batches (2+1) → 2 embed() calls.
    assert len(client.embed_calls) == 2
    call_sizes = [len(t) for _, t in client.embed_calls]
    assert call_sizes == [2, 1]
    # Non-cached results should have vectors.
    assert results[1] != []
    assert results[2] != []
    assert results[4] != []


@pytest.mark.asyncio
async def test_parallel_embed_exact_batch_boundary() -> None:
    """When num_misses == batch_size exactly, a single batch is sent (no empty trailing)."""
    chunks = ["a", "b", "c"]
    client = FakeEmbedClient()
    store = FakeStore()

    results = await embed_texts(client, "m", chunks, store, max_parallel_embed=3)

    assert len(results) == 3
    assert len(client.embed_calls) == 1
    assert len(client.embed_calls[0][1]) == 3


@pytest.mark.asyncio
async def test_parallel_embed_all_cached() -> None:
    """When every chunk is cached, no embed() calls are made even with parallel > 1."""
    chunks = ["x", "y"]
    client = FakeEmbedClient()
    store = FakeStore()

    from ollama_tutor.tutor.embeddings import _hash_text

    store.add_embedding(_hash_text("x", "m"), "m", [10.0])
    store.add_embedding(_hash_text("y", "m"), "m", [20.0])

    results = await embed_texts(client, "m", chunks, store, max_parallel_embed=4)

    assert results == [[10.0], [20.0]]
    assert len(client.embed_calls) == 0
