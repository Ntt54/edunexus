"""Unit tests for the Granite-Docling OCR provider (Phase 3).

Fully OFFLINE: the server manager is a fake (records start calls) and HTTP
traffic goes through an ``httpx.MockTransport`` injected via the provider's
``transport`` seam. No subprocess is ever spawned and no real socket opened.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.providers.docling_ocr import (
    DEFAULT_PROMPT,
    DoclingOCRError,
    DoclingOCRProvider,
    create_ocr_provider,
)
from src.ollama_tutor.tutor.providers.llama_server import LlamaServerConfig

BASE_URL = "http://127.0.0.1:9922"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"


# ---------------------------------------------------------------------------
# Offline doubles
# ---------------------------------------------------------------------------


class FakeManagedServer:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class FakeManager:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.start_calls: list[LlamaServerConfig] = []
        self.server = FakeManagedServer(base_url)

    async def start(self, config: LlamaServerConfig) -> FakeManagedServer:
        self.start_calls.append(config)
        return self.server


def chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def make_provider(manager: FakeManager, handler, **kwargs) -> DoclingOCRProvider:
    defaults: dict = {
        "binary_path": "/usr/bin/llama-server",
        "model_path": "/models/granite-docling-Q5_K_M.gguf",
        "transport": httpx.MockTransport(handler),
    }
    defaults.update(kwargs)
    return DoclingOCRProvider(manager, **defaults)


def write_image(tmp_path: Path) -> Path:
    image = tmp_path / "page.png"
    image.write_bytes(PNG_BYTES)
    return image


# ---------------------------------------------------------------------------
# 1. Construction starts NO server (laziness contract).
# ---------------------------------------------------------------------------


def test_construction_starts_no_server() -> None:
    manager = FakeManager()
    provider = DoclingOCRProvider(
        manager,
        binary_path="/usr/bin/llama-server",
        model_path="/models/docling.gguf",
        mmproj_path="/models/mmproj.gguf",
    )
    assert manager.start_calls == []
    assert provider.model_name == "granite-docling"


# ---------------------------------------------------------------------------
# 2. First transcribe starts server exactly once, with mmproj wired in.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_starts_server_once_with_mmproj(tmp_path: Path) -> None:
    manager = FakeManager()
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=chat_payload("hello"))

    provider = make_provider(
        manager,
        handle,
        mmproj_path="/models/granite-docling-mmproj-f16.gguf",
        extra_args=["-t", "4"],
        timeout_s=45,
    )

    text = await provider.transcribe_page(write_image(tmp_path))

    assert len(manager.start_calls) == 1
    cfg = manager.start_calls[0]
    assert cfg.binary_path == "/usr/bin/llama-server"
    assert cfg.model_path == "/models/granite-docling-Q5_K_M.gguf"
    assert cfg.mmproj_path == "/models/granite-docling-mmproj-f16.gguf"
    assert cfg.extra_args == ["-t", "4"]
    assert cfg.startup_timeout_s == 45
    assert text == "hello"

    await provider.aclose()


@pytest.mark.asyncio
async def test_mmproj_omitted_when_empty(tmp_path: Path) -> None:
    manager = FakeManager()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_payload("x"))

    provider = make_provider(manager, handle)
    await provider.transcribe_page(write_image(tmp_path))

    assert manager.start_calls[0].mmproj_path == ""

    await provider.aclose()


# ---------------------------------------------------------------------------
# 3. Payload carries data:image/png;base64 and the prompt.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_contains_base64_image_and_prompt(tmp_path: Path) -> None:
    import base64

    manager = FakeManager()
    requests: list[httpx.Request] = []
    bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat_payload("ok"))

    provider = make_provider(manager, handle)

    # Default prompt kicks in when prompt="".
    await provider.transcribe_page(write_image(tmp_path))
    # Explicit prompt is passed through verbatim.
    await provider.transcribe_page(write_image(tmp_path), prompt="Read the table.")

    expected_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    assert bodies[0]["messages"][0]["content"][0] == {
        "type": "text",
        "text": DEFAULT_PROMPT,
    }
    assert bodies[1]["messages"][0]["content"][0]["text"] == "Read the table."
    for body in bodies:
        image_part = body["messages"][0]["content"][1]
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"] == (
            f"data:image/png;base64,{expected_b64}"
        )
        assert body["max_tokens"] == 4096
    assert all(
        request.url.path == "/v1/chat/completions" for request in requests
    )

    await provider.aclose()


# ---------------------------------------------------------------------------
# 4. Content parsed from choices[0].message.content.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_parsed_from_choices(tmp_path: Path) -> None:
    manager = FakeManager()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=chat_payload("Chapter 1\nThe beginning of everything."),
        )

    provider = make_provider(manager, handle)
    text = await provider.transcribe_page(write_image(tmp_path))
    assert text == "Chapter 1\nThe beginning of everything."

    await provider.aclose()


# ---------------------------------------------------------------------------
# 5. Malformed / non-200 / empty-choices responses raise DoclingOCRError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"unexpected": True}),
    ],
)
async def test_bad_responses_raise(tmp_path: Path, response: httpx.Response) -> None:
    manager = FakeManager()
    provider = make_provider(manager, lambda request: response)

    with pytest.raises(DoclingOCRError):
        await provider.transcribe_page(write_image(tmp_path))

    await provider.aclose()


# ---------------------------------------------------------------------------
# 6. Connection failure triggers exactly ONE restart, then succeeds.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_failure_restarts_once_then_succeeds(tmp_path: Path) -> None:
    manager = FakeManager()
    state = {"requests": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        if state["requests"] == 1:
            raise httpx.ConnectError("server died", request=request)
        return httpx.Response(200, json=chat_payload("recovered"))

    provider = make_provider(manager, handle)
    text = await provider.transcribe_page(write_image(tmp_path))

    assert text == "recovered"
    assert len(manager.start_calls) == 2  # initial start + one restart
    assert manager.server.stop_calls == 1
    assert state["requests"] == 2

    await provider.aclose()


@pytest.mark.asyncio
async def test_persistent_failure_raises_after_single_restart(tmp_path: Path) -> None:
    manager = FakeManager()

    def always_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("still dead", request=request)

    provider = make_provider(manager, always_down)

    with pytest.raises(httpx.ConnectError):
        await provider.transcribe_page(write_image(tmp_path))

    assert len(manager.start_calls) == 2  # no infinite retry loop

    await provider.aclose()


# ---------------------------------------------------------------------------
# 7. Factory: None when keys missing; provider when bin+gguf set (lazy).
# ---------------------------------------------------------------------------


def _fresh_config(tmp_path: Path) -> Config:
    return Config(config_dir=tmp_path / "cfg")


def test_factory_returns_none_when_keys_missing(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    assert create_ocr_provider(config) is None


def test_factory_returns_none_when_only_bin_set(tmp_path: Path) -> None:
    config = _fresh_config(tmp_path)
    config.tutor_llama_bin = "/opt/llama.cpp/llama-server"
    assert create_ocr_provider(config) is None


def test_factory_returns_docling_provider_when_keys_set(tmp_path: Path) -> None:
    import src.ollama_tutor.tutor.providers.docling_ocr as docling_module

    config = _fresh_config(tmp_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    config.tutor_llama_bin = "/opt/llama.cpp/llama-server"
    config.tutor_llama_models_dir = str(models_dir)
    config.tutor_docling_gguf = "granite-docling-Q5_K_M.gguf"  # relative
    config.tutor_docling_mmproj = str(tmp_path / "mmproj-f16.gguf")  # absolute

    fake_manager = FakeManager()
    original = docling_module.get_default_manager
    docling_module.get_default_manager = lambda: fake_manager
    try:
        provider = create_ocr_provider(config)
    finally:
        docling_module.get_default_manager = original

    assert isinstance(provider, DoclingOCRProvider)
    assert provider.model_name == "granite-docling"
    assert provider._model_path == str(models_dir / "granite-docling-Q5_K_M.gguf")
    assert provider._mmproj_path == str(tmp_path / "mmproj-f16.gguf")
    assert provider._binary_path == "/opt/llama.cpp/llama-server"
    # Laziness contract: factory construction must not start any server.
    assert fake_manager.start_calls == []


def test_default_manager_singleton_shared_with_embedding() -> None:
    """OCR and embedding factories share one max_servers=1 manager."""
    from src.ollama_tutor.tutor.providers.gguf_embedding import get_default_manager

    manager = get_default_manager()
    assert get_default_manager() is manager
    assert manager._max_servers == 1
