"""Hybrid document ingestion: pypdf text-layer vs Granite-Docling OCR (Phase 3).

Routing rule per page: when the extracted text layer carries enough content
(stripped length >= ``text_threshold``) it is used directly; otherwise the
page is considered scanned, rasterized with poppler's ``pdftoppm``, and sent
through an :class:`~.base.OCRProvider` for transcription.

Both extraction steps are injectable seams so tests run fully offline:
``text_extractor`` defaults to :func:`_extract_page_texts` (pypdf) and
``rasterizer`` defaults to :func:`_rasterize_page` (external ``pdftoppm``).

This module is UI-agnostic and depends only on the standard library,
``pypdf``, and sibling provider modules.
"""

from __future__ import annotations

import inspect
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

from .base import DocumentParser, OCRProvider

__all__ = [
    "DocumentParserError",
    "HybridDocumentParser",
]

_PDFTOPPM_TIMEOUT_S = 60


class DocumentParserError(RuntimeError):
    """Raised when a document cannot be parsed or rasterized."""


# ---------------------------------------------------------------------------
# Default seams
# ---------------------------------------------------------------------------


def _extract_page_texts(source: Path) -> list[str]:
    """Extract per-page text with pypdf; a failing page yields ``""``.

    Opens the PDF lazily via :class:`pypdf.PdfReader`; per-page extraction
    errors are tolerated page-by-page (corrupt streams degrade to empty
    text rather than aborting the whole document).
    """
    from pypdf import PdfReader  # Local import keeps module import light.

    try:
        reader = PdfReader(str(source))
        pages = list(reader.pages)
    except Exception as exc:  # noqa: BLE001 - any reader failure is fatal here.
        raise DocumentParserError(
            f"could not open PDF {source}: {exc}"
        ) from exc

    texts: list[str] = []
    for page in pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - tolerate per-page corruption.
            texts.append("")
    return texts


def _rasterize_page(
    source: Path,
    page_index: int,
    dpi: int,
    out_dir: Path,
    *,
    binary: str = "pdftoppm",
) -> Path:
    """Rasterize one PDF page to PNG via external ``pdftoppm`` (poppler).

    Raises:
        DocumentParserError: If the binary is missing, exits non-zero, or
            produces no output file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = str(out_dir / "page")
    argv = [
        binary,
        "-f", str(page_index + 1),  # pdftoppm pages are 1-based.
        "-l", str(page_index + 1),
        "-r", str(dpi),
        "-png",
        str(source),
        prefix,
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=_PDFTOPPM_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise DocumentParserError(
            f"pdftoppm binary not found at {binary!r}; "
            "install poppler-utils (e.g. 'apt install poppler-utils') "
            "or set tutor.pdftoppm_bin"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentParserError(
            f"pdftoppm timed out after {_PDFTOPPM_TIMEOUT_S}s on {source}"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise DocumentParserError(
            f"pdftoppm exited with code {result.returncode} for page "
            f"{page_index + 1} of {source}: {stderr or 'no stderr'}"
        )

    produced = sorted(out_dir.glob("page*-*.png")) or sorted(
        out_dir.glob("page*.png")
    )
    if not produced:
        raise DocumentParserError(
            f"pdftoppm produced no output for page {page_index + 1} of {source} "
            f"(looked for {prefix}*.png)"
        )
    return produced[0]


# ---------------------------------------------------------------------------
# Hybrid parser
# ---------------------------------------------------------------------------


class HybridDocumentParser(DocumentParser):
    """DocumentParser routing each page to text-layer extraction or OCR."""

    def __init__(
        self,
        ocr_provider: OCRProvider | None = None,
        *,
        text_threshold: int = 32,
        dpi: int = 150,
        text_extractor: Callable[[Path], list[str]] | None = None,
        rasterizer: Callable[..., Path] | None = None,
        pdftoppm_bin: str = "pdftoppm",
    ) -> None:
        self._ocr_provider = ocr_provider
        self._text_threshold = text_threshold
        self._dpi = dpi
        self._text_extractor = (
            text_extractor if text_extractor is not None else _extract_page_texts
        )
        if rasterizer is not None:
            self._rasterizer = rasterizer
        else:
            # Bind the configured binary into the default rasterizer;
            # injected fakes keep their own signature.
            self._rasterizer = partial(_rasterize_page, binary=pdftoppm_bin)

    async def parse(self, source: Path) -> dict[str, Any]:
        """Parse ``source`` into ``{"pages": [{"index", "text", "source"}]}``.

        Pages with enough text-layer content use it verbatim; sparse pages
        go through rasterization + OCR. Page order and indices are preserved.
        """
        source = Path(source)
        texts = self._text_extractor(source)

        pages: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="ollama-tutor-parse-") as tmp:
            out_dir = Path(tmp)
            for index, text in enumerate(texts):
                if len(text.strip()) >= self._text_threshold:
                    pages.append(
                        {"index": index, "text": text, "source": "text-layer"}
                    )
                    continue
                if self._ocr_provider is None:
                    raise DocumentParserError(
                        f"page {index + 1} looks scanned but no OCR provider "
                        "configured (set tutor.docling_gguf and tutor.llama_bin "
                        "to enable Granite-Docling OCR)"
                    )
                png = await _maybe_await(
                    self._rasterizer(source, index, self._dpi, out_dir)
                )
                transcription = await self._ocr_provider.transcribe_page(png)
                pages.append(
                    {"index": index, "text": transcription, "source": "ocr"}
                )
        return {"pages": pages}


async def _maybe_await(value: Any) -> Any:
    """Allow rasterizer seams to be sync or async."""
    if inspect.isawaitable(value):
        return await value
    return value
