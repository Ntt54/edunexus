"""Granite-Docling OCR provider backed by a managed ``llama-server`` (Phase 3).

Serves page-image transcription via ``llama-server``'s OpenAI-compatible
``/v1/chat/completions`` endpoint with a multimodal (text + image_url)
payload. The vision projector (``--mmproj``) is appended to the launch
config so Granite-Docling can see images.

Laziness contract mirrors :mod:`.gguf_embedding`: constructing this
provider (directly or through :func:`create_ocr_provider`) never spawns a
process nor opens a network connection; the server starts on the first
:meth:`transcribe_page` call and is reused afterward, with exactly one
transparent restart+retry on connection failure.

This module is UI-agnostic and depends only on the standard library,
``httpx``, and sibling provider modules.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from .base import OCRProvider
from .gguf_embedding import get_default_manager
from .llama_server import LlamaServerConfig

__all__ = [
    "DEFAULT_PROMPT",
    "DoclingOCRError",
    "DoclingOCRProvider",
    "create_ocr_provider",
]

DEFAULT_PROMPT = (
    "Transcribe all text on this page exactly as it appears, "
    "preserving reading order, headings, and line breaks. "
    "Output only the transcription."
)

_DEFAULT_STARTUP_TIMEOUT_S = 120.0
_MAX_TOKENS = 4096


class DoclingOCRError(RuntimeError):
    """Raised when the Docling OCR backend returns an unusable response."""


class DoclingOCRProvider(OCRProvider):
    """OCRProvider over a lazily-started managed ``llama-server`` (vision).

    Args:
        manager: Duck-typed server manager exposing
            ``async start(LlamaServerConfig) -> ManagedLlamaServer`` with a
            ``.base_url`` attribute on the handle.
        binary_path: Path to the ``llama-server`` binary.
        model_path: Path to the Granite-Docling GGUF file.
        mmproj_path: Path to the vision projector (``--mmproj``); required
            for image input in production.
        model_name: Identifier reported by :attr:`model_name`.
        extra_args: Extra CLI flags appended to the launch command.
        timeout_s: Server startup timeout in seconds (default 120).
        transport: TEST SEAM forwarded to the internal
            ``httpx.AsyncClient(transport=...)``; ``None`` in production.
    """

    def __init__(
        self,
        manager: Any,
        *,
        binary_path: str,
        model_path: str,
        mmproj_path: str = "",
        model_name: str = "granite-docling",
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._manager = manager
        self._binary_path = binary_path
        self._model_path = model_path
        self._mmproj_path = mmproj_path
        self._model_name = model_name
        self._extra_args = list(extra_args) if extra_args else []
        self._timeout_s = float(timeout_s) if timeout_s else _DEFAULT_STARTUP_TIMEOUT_S
        self._transport = transport
        self._server: Any | None = None  # ManagedLlamaServer handle, set lazily.
        self._client: httpx.AsyncClient | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    async def transcribe_page(self, image_path: Path, prompt: str = "") -> str:
        """Transcribe a single page image to plain text.

        Starts the managed server on first use and reuses it afterward.
        On a connection failure the server is restarted exactly once and
        the request retried once before the error propagates.
        """
        payload = self._build_payload(Path(image_path), prompt)

        client = self._ensure_client()
        server = await self._ensure_server()
        try:
            response = await client.post(
                f"{server.base_url}/v1/chat/completions",
                json=payload,
            )
        except httpx.TransportError:
            # Stale/dead server: stop it if possible, start a fresh one,
            # retry the request exactly once.
            server = await self._restart_server()
            response = await client.post(
                f"{server.base_url}/v1/chat/completions",
                json=payload,
            )

        return self._parse_response(response)

    async def aclose(self) -> None:
        """Close the HTTP client. Server lifetime belongs to the manager."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Internals ---------------------------------------------------------

    def _build_payload(self, image_path: Path, prompt: str) -> dict[str, Any]:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt or DEFAULT_PROMPT,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{encoded}",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": _MAX_TOKENS,
        }

    @staticmethod
    def _parse_response(response: httpx.Response) -> str:
        if response.status_code != 200:
            raise DoclingOCRError(
                f"OCR backend returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            choices = payload["choices"]
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DoclingOCRError(f"malformed OCR response: {exc}") from exc
        if not isinstance(content, str):
            raise DoclingOCRError("malformed OCR response: content is not a string")
        return content

    def _build_server_config(self) -> LlamaServerConfig:
        return LlamaServerConfig(
            binary_path=self._binary_path,
            model_path=self._model_path,
            mmproj_path=self._mmproj_path,
            extra_args=self._extra_args,
            startup_timeout_s=self._timeout_s,
        )

    async def _ensure_server(self) -> Any:
        if self._server is None:
            self._server = await self._manager.start(self._build_server_config())
        return self._server

    async def _restart_server(self) -> Any:
        old = self._server
        self._server = None
        if old is not None and hasattr(old, "stop"):
            try:
                await old.stop()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        self._server = await self._manager.start(self._build_server_config())
        return self._server

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s),
                transport=self._transport,
            )
        return self._client


# ---------------------------------------------------------------------------
# Config factory
# ---------------------------------------------------------------------------


def _resolve_model_path(raw: str, models_dir: str) -> str:
    """Absolute paths pass through; relative ones join ``models_dir`` when set."""
    path = Path(raw)
    if path.is_absolute():
        return str(path)
    if models_dir:
        return str(Path(models_dir) / path)
    return raw


def create_ocr_provider(config: Any) -> OCRProvider | None:
    """Build the OCR provider from app config, or ``None`` when unavailable.

    Returns a :class:`DoclingOCRProvider` only when both ``tutor_llama_bin``
    and ``tutor_docling_gguf`` are configured; otherwise ``None`` (OCR
    unavailable — callers decide their fallback). Construction performs no
    subprocess spawns and no network calls.
    """
    llama_bin = getattr(config, "tutor_llama_bin", "") or ""
    docling_gguf = getattr(config, "tutor_docling_gguf", "") or ""
    if not (llama_bin and docling_gguf):
        return None

    models_dir = getattr(config, "tutor_llama_models_dir", "") or ""
    timeout_s = float(getattr(config, "tutor_llama_health_timeout_s", 120))
    return DoclingOCRProvider(
        get_default_manager(),
        binary_path=llama_bin,
        model_path=_resolve_model_path(docling_gguf, models_dir),
        mmproj_path=_resolve_model_path(
            getattr(config, "tutor_docling_mmproj", "") or "", models_dir
        ),
        timeout_s=timeout_s,
    )
