"""Provider interfaces + Ollama adapters for the CPU/GGUF pipeline (Granite models)."""

from __future__ import annotations

from .base import (
    DocumentParser,
    EmbeddingProvider,
    LLMProvider,
    OCRProvider,
    VectorStore,
)
from .gguf_llm import GGUFLLMProvider, create_gguf_llm_provider
from .ollama_adapter import OllamaEmbeddingProvider, OllamaLLMProvider

__all__ = [
    "DocumentParser",
    "EmbeddingProvider",
    "GGUFLLMProvider",
    "LLMProvider",
    "OCRProvider",
    "OllamaEmbeddingProvider",
    "OllamaLLMProvider",
    "VectorStore",
    "create_gguf_llm_provider",
]
