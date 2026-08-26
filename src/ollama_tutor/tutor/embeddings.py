"""Embeddings + in-memory vector index for the local AI tutor (research D2/D1).

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Sequence

import numpy as np


def _hash_text(text: str, model: str) -> str:
    """Hash key for the embeddings cache: ``sha256(chunk + model)`` (D2)."""
    return hashlib.sha256((text + "|" + model).encode("utf-8")).hexdigest()


async def embed_texts(
    client,
    model: str,
    chunks: list[str],
    store,
    max_parallel_embed: int = 1,
    *,
    batch_size: int | None = None,
    max_concurrency: int | None = None,
) -> list[list[float]]:
    """Embed ``chunks`` via ``client.embed``, caching each by text hash.

    Returns one vector per input chunk. Vectors already present in the
    hash-keyed cache (``store.get_embedding``) are reused without a model
    call; only cache misses are sent to ``client.embed`` (batched). New
    vectors are written back via ``store.add_embedding``.

    ``batch_size`` controls the number of chunks in one request. The
    independent ``max_concurrency`` control is enforced with a semaphore.
    ``max_parallel_embed`` remains a backwards-compatible shorthand for the
    old API; explicit keyword arguments take precedence.
    """
    results: list[list[float] | None] = []
    to_embed: list[tuple[int, str]] = []
    for i, text in enumerate(chunks):
        cached = store.get_embedding(_hash_text(text, model), model)
        if cached is not None:
            results.append(cached)
        else:
            results.append(None)
            to_embed.append((i, text))

    if to_embed:
        if batch_size is None and max_concurrency is None:
            # Backwards-compatible mode: the old parameter was both the batch
            # size and the concurrency setting.
            effective_batch_size = (
                len(to_embed) if max_parallel_embed <= 1 else max_parallel_embed
            )
            effective_concurrency = max(1, max_parallel_embed)
        else:
            effective_batch_size = max(1, int(batch_size or 16))
            effective_concurrency = max(1, int(max_concurrency or 1))

        batches: list[list[tuple[int, str]]] = []
        for start in range(0, len(to_embed), effective_batch_size):
            batches.append(to_embed[start : start + effective_batch_size])

        semaphore = asyncio.Semaphore(effective_concurrency)

        async def _embed_batch(
            batch: list[tuple[int, str]],
        ) -> list[tuple[int, list[float]]]:
            async with semaphore:
                texts = [t for _, t in batch]
                vectors = await client.embed(model, texts)
                return [(i, vec) for (i, _), vec in zip(batch, vectors)]

        batch_results = await asyncio.gather(
            *[_embed_batch(batch) for batch in batches]
        )
        for batch_pairs in batch_results:
            for i, vec in batch_pairs:
                store.add_embedding(_hash_text(chunks[i], model), model, vec)
                results[i] = vec

    return [r if r is not None else [] for r in results]


class NumpyVectorIndex:
    """In-memory cosine-similarity index over float32 vectors (research D1).

    Implements the ``VectorIndex`` contract: ``add`` loads ``(id, vector)``
    pairs, ``search`` returns the top-``k`` ``(id, cosine_similarity)`` pairs.
    All vectors are stored as ``float32`` numpy arrays.
    """

    def __init__(self) -> None:
        self._ids: list[Any] = []
        self._matrix: np.ndarray | None = None  # (n, dim) float32

    def add(self, items: Sequence[tuple[Any, Sequence[float]]]) -> None:
        """Add ``(id, vector)`` pairs to the index."""
        if not items:
            return
        ids = [it[0] for it in items]
        vecs = np.array(
            [np.asarray(v, dtype=np.float32) for _, v in items],
            dtype=np.float32,
        )
        if self._matrix is None:
            self._ids = list(ids)
            self._matrix = vecs
        else:
            self._ids.extend(ids)
            self._matrix = np.vstack([self._matrix, vecs])

    def search(
        self,
        query_vector: Sequence[float],
        k: int,
        floor: float | None = None,
    ) -> list[tuple[Any, float]]:
        """Return the top-``k`` ``(id, cosine_similarity)`` pairs.

        When ``floor`` is provided, pairs scoring below it are excluded.
        """
        if self._matrix is None or len(self._ids) == 0 or k <= 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        mn = np.linalg.norm(self._matrix, axis=1)
        denom = qn * mn
        sims = np.zeros(len(self._ids), dtype=np.float32)
        nonzero = denom > 0
        if np.any(nonzero):
            dots = self._matrix @ q
            sims[nonzero] = dots[nonzero] / denom[nonzero]
        limit = min(k, len(self._ids))
        if limit < len(self._ids):
            candidate_idx = np.argpartition(-sims, limit - 1)[:limit]
            order = candidate_idx[np.argsort(-sims[candidate_idx])]
        else:
            order = np.argsort(-sims)
        out: list[tuple[Any, float]] = []
        for idx in order:
            score = float(sims[idx])
            if floor is not None and score < floor:
                continue
            out.append((self._ids[idx], score))
            if len(out) >= k:
                break
        return out

    def invalidate(self, subject_id: Any = None) -> None:
        """Drop all loaded vectors (called after writes)."""
        self._ids = []
        self._matrix = None
