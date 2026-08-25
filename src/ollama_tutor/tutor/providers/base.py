"""Abstract provider interfaces for the local CPU/GGUF pipeline (Granite models).

Phase 0 of the GGUF/llama.cpp migration: these ABCs define the seams between
the tutor core and its backends (Ollama today, llama-server/GGUF later).
Stdlib-only by design — no numpy/httpx/UI-framework imports here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence


class EmbeddingProvider(ABC):
    """Embedding backend for the CPU/GGUF pipeline (Granite Embedding R2).

    Dimension auto-detect contract: ``dims`` is ``None`` until the first real
    vector has been observed; it must NEVER be hardcoded.
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier of the backing embedding model."""
        raise NotImplementedError

    @property
    @abstractmethod
    def dims(self) -> int | None:
        """Vector dimensionality, or None until first real vector observed."""
        raise NotImplementedError

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""
        raise NotImplementedError


class LLMProvider(ABC):
    """Text-generation backend for the CPU/GGUF pipeline (Granite 4.1 3B Instruct)."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Run a non-streaming completion and return the full response text."""
        raise NotImplementedError


class OCRProvider(ABC):
    """Vision OCR backend for the CPU/GGUF pipeline (Granite-Docling mmproj).

    Contract refined in Phase 3.
    """

    @abstractmethod
    async def transcribe_page(self, image_path: Path, prompt: str = "") -> str:
        """Transcribe a single page image to plain text (refined in Phase 3)."""
        raise NotImplementedError


class DocumentParser(ABC):
    """Document ingestion backend for the CPU/GGUF pipeline (Granite-Docling).

    Contract refined in Phase 3.
    """

    @abstractmethod
    async def parse(self, source: Path) -> dict[str, Any]:
        """Parse a document into ``{"pages": [{"index": int, "text": str,
        "source": "text-layer" | "ocr"}]}`` (refined in Phase 3)."""
        raise NotImplementedError


class VectorStore(ABC):
    """Similarity-search storage for the CPU/GGUF pipeline (Granite embeddings).

    The existing :class:`ollama_tutor.tutor.embeddings.NumpyVectorIndex`
    already satisfies this shape (``add`` / ``search`` / ``invalidate``).
    """

    @abstractmethod
    def add(self, items: Sequence[tuple[Any, Sequence[float]]]) -> None:
        """Add ``(id, vector)`` pairs to the store."""
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query_vector: Sequence[float],
        k: int,
        floor: float | None = None,
    ) -> list[tuple[Any, float]]:
        """Return the top-``k`` ``(id, score)`` pairs above ``floor`` (if given)."""
        raise NotImplementedError

    @abstractmethod
    def invalidate(self) -> None:
        """Drop any cached vectors so subsequent searches see fresh data."""
        raise NotImplementedError
