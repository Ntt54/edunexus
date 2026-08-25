"""Unit tests for Phase 0 provider interfaces + config plumbing.

Offline: fake clients only — no network, no subprocesses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.providers import (
    DocumentParser,
    EmbeddingProvider,
    LLMProvider,
    OCRProvider,
    OllamaEmbeddingProvider,
    OllamaLLMProvider,
    VectorStore,
)

PROVIDERS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "ollama_tutor" / "tutor" / "providers"
)


# ---------------------------------------------------------------------------
# Fakes (duck-typed clients — adapters must not construct/import real ones)
# ---------------------------------------------------------------------------


class FakeEmbedClient:
    """Records embed calls, returns fixed vectors."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[tuple[str, list[str]]] = []

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        self.calls.append((model, list(inputs)))
        return self.vectors


@dataclass
class FakeStreamEvent:
    kind: str
    text: str = ""


class FakeLLMClient:
    """Records chat_stream calls; yields scripted events."""

    def __init__(self, events: list[FakeStreamEvent]) -> None:
        self.events = events
        self.calls: list[tuple[list[dict[str, Any]], str, dict[str, Any] | None]] = []

    async def chat_stream(self, messages, model, *, options=None):
        self.calls.append(
            ([{"role": m.role.value, "content": m.content} for m in messages],
             model,
             options)
        )
        for ev in self.events:
            yield ev


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "abc_cls",
    [EmbeddingProvider, LLMProvider, OCRProvider, DocumentParser, VectorStore],
)
def test_abstract_base_classes_cannot_be_instantiated(abc_cls) -> None:
    with pytest.raises(TypeError):
        abc_cls()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# OllamaEmbeddingProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_provider_delegates_and_autodetects_dims() -> None:
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    fake = FakeEmbedClient(vectors)
    provider = OllamaEmbeddingProvider(fake, "granite-embedding-r2")

    assert provider.model_name == "granite-embedding-r2"
    # Auto-detect contract: dims unknown until first real vector observed.
    assert provider.dims is None

    out = await provider.embed(["hello", "world"])

    assert out == vectors
    assert fake.calls == [("granite-embedding-r2", ["hello", "world"])]
    assert provider.dims == 3


@pytest.mark.asyncio
async def test_embedding_provider_dims_cached_after_first_embed() -> None:
    fake = FakeEmbedClient([[1.0, 2.0]])
    provider = OllamaEmbeddingProvider(fake, "m")

    await provider.embed(["a"])
    first_dims = provider.dims
    assert first_dims == 2

    # Later calls with different-length vectors do not overwrite the cached dim.
    fake.vectors = [[1.0, 2.0, 3.0]]
    await provider.embed(["b"])
    assert provider.dims == first_dims == 2


# ---------------------------------------------------------------------------
# OllamaLLMProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_provider_accumulates_content_chunks() -> None:
    events = [
        FakeStreamEvent(kind="thinking", text="hmm"),
        FakeStreamEvent(kind="content", text="Hello "),
        FakeStreamEvent(kind="content", text="world"),
        FakeStreamEvent(kind="done"),
    ]
    fake = FakeLLMClient(events)
    provider = OllamaLLMProvider(fake, "granite4.1:3b")

    result = await provider.generate("What is 2+2?")

    assert result == "Hello world"  # thinking/done chunks excluded
    assert len(fake.calls) == 1
    messages, model, options = fake.calls[0]
    assert model == "granite4.1:3b"
    assert messages == [{"role": "user", "content": "What is 2+2?"}]
    assert options is None


@pytest.mark.asyncio
async def test_llm_provider_passes_system_and_options() -> None:
    fake = FakeLLMClient([FakeStreamEvent(kind="content", text="ok")])
    provider = OllamaLLMProvider(fake, "granite4.1:3b")

    result = await provider.generate(
        "prompt text",
        system="You are a tutor.",
        options={"temperature": 0.2},
    )

    assert result == "ok"
    _, _, options = fake.calls[0]
    assert options == {"temperature": 0.2}
    roles = [m["role"] for m in fake.calls[0][0]]
    assert roles == ["system", "user"]
    assert fake.calls[0][0][0]["content"] == "You are a tutor."


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def test_new_gguf_config_defaults(tmp_path: Path) -> None:
    cfg = Config(config_dir=tmp_path)

    assert cfg.tutor_llama_bin == ""
    assert cfg.tutor_llama_models_dir == ""
    assert cfg.tutor_embed_gguf == ""
    assert cfg.tutor_docling_gguf == ""
    assert cfg.tutor_docling_mmproj == ""
    assert cfg.tutor_llm_gguf == ""
    assert cfg.tutor_llama_health_timeout_s == 120


def test_new_gguf_config_round_trip_persists(tmp_path: Path) -> None:
    cfg = Config(config_dir=tmp_path)
    cfg.tutor_llama_bin = "/opt/llama.cpp/llama-server"
    cfg.tutor_llama_models_dir = "/opt/llama.cpp/models"
    cfg.tutor_embed_gguf = "/models/granite-embedding-R2-Q5_K_M.gguf"
    cfg.tutor_docling_gguf = "/models/GraniteDocling-Q5_K_M.gguf"
    cfg.tutor_docling_mmproj = "/models/mmproj-f16.gguf"
    cfg.tutor_llm_gguf = "/models/Granite-4.1-3B-Instruct-Q4_K_M.gguf"
    cfg.tutor_llama_health_timeout_s = 300

    # No running loop here => debounced save falls back to immediate write.
    raw = json.loads((tmp_path / "config.json").read_text())
    assert raw["tutor"]["llama_bin"] == "/opt/llama.cpp/llama-server"
    assert raw["tutor"]["embed_gguf"] == "/models/granite-embedding-R2-Q5_K_M.gguf"

    reloaded = Config(config_dir=tmp_path)
    assert reloaded.tutor_llama_bin == "/opt/llama.cpp/llama-server"
    assert reloaded.tutor_llama_models_dir == "/opt/llama.cpp/models"
    assert reloaded.tutor_embed_gguf == "/models/granite-embedding-R2-Q5_K_M.gguf"
    assert reloaded.tutor_docling_gguf == "/models/GraniteDocling-Q5_K_M.gguf"
    assert reloaded.tutor_docling_mmproj == "/models/mmproj-f16.gguf"
    assert reloaded.tutor_llm_gguf == "/models/Granite-4.1-3B-Instruct-Q4_K_M.gguf"
    assert reloaded.tutor_llama_health_timeout_s == 300


def test_health_timeout_clamped(tmp_path: Path) -> None:
    cfg = Config(config_dir=tmp_path)

    cfg.tutor_llama_health_timeout_s = 1
    assert cfg.tutor_llama_health_timeout_s == 10

    cfg.tutor_llama_health_timeout_s = 9999
    assert cfg.tutor_llama_health_timeout_s == 600


def test_snapshot_includes_gguf_keys(tmp_path: Path) -> None:
    cfg = Config(config_dir=tmp_path)
    snapshot = cfg.get_tutor_config_snapshot()

    for key in (
        "llama_bin",
        "llama_models_dir",
        "embed_gguf",
        "docling_gguf",
        "docling_mmproj",
        "llm_gguf",
        "llama_health_timeout_s",
    ):
        assert key in snapshot
    assert snapshot["llama_bin"] == ""
    assert snapshot["llama_health_timeout_s"] == 120


# ---------------------------------------------------------------------------
# UI-framework-free contract for the new subpackage
# ---------------------------------------------------------------------------


def test_providers_modules_have_no_ui_framework_imports() -> None:
    forbidden = ("import textual", "from textual", "import fastapi", "from fastapi")
    files = sorted(PROVIDERS_DIR.rglob("*.py"))
    assert files, "providers package must contain modules"
    for path in files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} must not contain {token!r}"
