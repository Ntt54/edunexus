"""Vector index protocol + re-export of the shared ``NumpyVectorIndex``.

Research D1: the default implementation lives in ``embeddings.py`` (Lane 2).
This module re-exports it so the ``VectorIndex`` contract (contracts/
tutor-core-api.md) is satisfied without a second, divergent implementation,
and defines the ``ScoredChunk`` value object returned by retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from .embeddings import NumpyVectorIndex

__all__ = ["NumpyVectorIndex", "VectorIndex", "ScoredChunk"]


@runtime_checkable
class VectorIndex(Protocol):
    """Contract for a subject-scoped cosine index (tutor-core-api.md)."""

    def search(
        self,
        subject_id: str,
        query_vec: list[float],
        k: int,
        floor: float,
    ) -> list[tuple[Any, float]]:
        ...

    def invalidate(self, subject_id: str) -> None:
        ...


@dataclass
class ScoredChunk:
    """A retrieved chunk with its cosine score and book metadata."""

    chunk_id: str
    book_id: str
    book_title: str
    chapter: str | None
    page: int | None
    score: float
    text: str
