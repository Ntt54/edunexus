"""Provider interfaces + Ollama adapters for the CPU/GGUF pipeline (Granite models)."""

from __future__ import annotations

from .base import (
    DocumentParser,
    EmbeddingProvider,
    LLMProvider,
    OCRProvider,
    VectorStore,
)
from .ollama_adapter import OllamaEmbeddingProvider, OllamaLLMProvider

__all__ = [
    "DocumentParser",
    "EmbeddingProvider",
    "LLMProvider",
    "OCRProvider",
    "OllamaEmbeddingProvider",
    "OllamaLLMProvider",
    "VectorStore",
]
