"""Unit tests for the hybrid document parser (Phase 3).

Fully OFFLINE: text extraction, rasterization, and OCR are injectable
seams backed by fakes. One integration test exercises the real pypdf
reader path against a minimal PDF built in-test with ``pypdf.PdfWriter``.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.providers.base import DocumentParser, OCRProvider
from src.ollama_tutor.tutor.providers.hybrid_parser import (
    DocumentParserError,
    HybridDocumentParser,
    _extract_page_texts,
    _rasterize_page,
)

LONG_TEXT = "word " * 20  # 100 chars — comfortably above any threshold.
SHORT_TEXT = "hi"


# ---------------------------------------------------------------------------
# Offline doubles
# ---------------------------------------------------------------------------


class FakeOCRProvider(OCRProvider):
    """Records transcribe_page calls; returns canned text per image path."""

    def __init__(self, transcription: str = "OCR TEXT") -> None:
        self.transcription = transcription
        self.calls: list[tuple[Path, str]] = []

    @property
    def model_name(self) -> str:
        return "fake-docling"

    @property
    def dims(self):
        return None

    async def transcribe_page(self, image_path: Path, prompt: str = "") -> str:
        self.calls.append((Path(image_path), prompt))
        return self.transcription


def fake_extractor(texts: list[str]):
    def extract(source: Path) -> list[str]:
        return list(texts)

    return extract


def fake_rasterizer(record: list[tuple[Path, int, int]]):
    def rasterize(source: Path, page_index: int, dpi: int, out_dir: Path) -> Path:
        record.append((source, page_index, dpi))
        png = Path(out_dir) / f"page-{page_index + 1}.png"
        png.write_bytes(b"\x89PNG-fake")
        return png

    return rasterize


# ---------------------------------------------------------------------------
# 1. Threshold routing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_page_uses_text_layer_without_ocr() -> None:
    ocr = FakeOCRProvider()
    parser = HybridDocumentParser(
        ocr, text_threshold=32, text_extractor=fake_extractor([LONG_TEXT])
    )

    result = await parser.parse(Path("doc.pdf"))

    assert result == {
        "pages": [{"index": 0, "text": LONG_TEXT, "source": "text-layer"}]
    }
    assert ocr.calls == []


@pytest.mark.asyncio
async def test_short_page_goes_through_ocr_verbatim() -> None:
    ocr = FakeOCRProvider(transcription="SCANNED WORDS")
    raster_calls: list[tuple[Path, int, int]] = []
    parser = HybridDocumentParser(
        ocr,
        text_threshold=32,
        dpi=200,
        text_extractor=fake_extractor([SHORT_TEXT]),
        rasterizer=fake_rasterizer(raster_calls),
    )

    result = await parser.parse(Path("scan.pdf"))

    assert result == {
        "pages": [{"index": 0, "text": "SCANNED WORDS", "source": "ocr"}]
    }
    assert len(raster_calls) == 1
    source, page_index, dpi = raster_calls[0]
    assert source == Path("scan.pdf")
    assert page_index == 0
    assert dpi == 200
    assert ocr.calls[0][0].name.startswith("page-")  # the produced PNG


@pytest.mark.asyncio
async def test_threshold_boundary_is_inclusive() -> None:
    exactly_at_threshold = "a" * 32
    ocr = FakeOCRProvider()
    parser = HybridDocumentParser(
        ocr,
        text_threshold=32,
        text_extractor=fake_extractor([exactly_at_threshold]),
    )

    result = await parser.parse(Path("doc.pdf"))

    assert result["pages"][0]["source"] == "text-layer"
    assert ocr.calls == []


# ---------------------------------------------------------------------------
# 2. Mixed documents: order and indices preserved.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_pages_preserve_order_and_indices() -> None:
    ocr = FakeOCRProvider(transcription="FROM OCR")
    raster_calls: list[tuple[Path, int, int]] = []
    texts = [LONG_TEXT, SHORT_TEXT, LONG_TEXT + " tail", "", SHORT_TEXT * 2]
    parser = HybridDocumentParser(
        ocr,
        text_threshold=32,
        text_extractor=fake_extractor(texts),
        rasterizer=fake_rasterizer(raster_calls),
    )

    result = await parser.parse(Path("mixed.pdf"))

    pages = result["pages"]
    assert [page["index"] for page in pages] == [0, 1, 2, 3, 4]
    assert [page["source"] for page in pages] == [
        "text-layer",
        "ocr",
        "text-layer",
        "ocr",
        "ocr",
    ]
    assert pages[0]["text"] == LONG_TEXT  # verbatim, not stripped
    assert pages[2]["text"] == LONG_TEXT + " tail"
    assert all(page["text"] == "FROM OCR" for page in pages if page["source"] == "ocr")
    # Rasterizer saw the sparse page indices only, in order.
    assert [index for _, index, _ in raster_calls] == [1, 3, 4]


# ---------------------------------------------------------------------------
# 3. No OCR provider + sparse page ⇒ DocumentParserError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sparse_page_without_ocr_provider_raises() -> None:
    parser = HybridDocumentParser(
        None, text_extractor=fake_extractor([LONG_TEXT, SHORT_TEXT])
    )

    with pytest.raises(DocumentParserError, match="no OCR provider"):
        await parser.parse(Path("scanned.pdf"))


# ---------------------------------------------------------------------------
# 4. Real pypdf reader path: two blank pages ⇒ both routed to OCR.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_pypdf_blank_pages_route_to_ocr(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf_path)

    # Sanity: the real extractor yields empty text for blank pages.
    assert _extract_page_texts(pdf_path) == ["", ""]

    ocr = FakeOCRProvider(transcription="BLANK PAGE OCR")
    raster_calls: list[tuple[Path, int, int]] = []
    parser = HybridDocumentParser(
        ocr,
        text_threshold=32,
        dpi=150,
        rasterizer=fake_rasterizer(raster_calls),  # real extractor, fake raster
    )

    result = await parser.parse(pdf_path)

    assert result == {
        "pages": [
            {"index": 0, "text": "BLANK PAGE OCR", "source": "ocr"},
            {"index": 1, "text": "BLANK PAGE OCR", "source": "ocr"},
        ]
    }
    assert [index for _, index, _ in raster_calls] == [0, 1]


def test_hybrid_parser_satisfies_document_parser_interface() -> None:
    parser = HybridDocumentParser(None)
    assert isinstance(parser, DocumentParser)


# ---------------------------------------------------------------------------
# 5. Default rasterizer (_rasterize_page) error handling.
# ---------------------------------------------------------------------------


def _make_script(tmp_path: Path, name: str, body: str) -> str:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_rasterize_missing_binary_raises(tmp_path: Path) -> None:
    with pytest.raises(DocumentParserError, match="pdftoppm binary not found"):
        _rasterize_page(
            tmp_path / "doc.pdf",
            0,
            150,
            tmp_path / "out",
            binary=str(tmp_path / "does-not-exist"),
        )


def test_rasterize_nonzero_exit_raises_with_stderr(tmp_path: Path) -> None:
    failing = _make_script(tmp_path, "failing-pdftoppm", "echo boom >&2; exit 3")
    with pytest.raises(DocumentParserError, match="code 3.*boom"):
        _rasterize_page(
            tmp_path / "doc.pdf", 0, 150, tmp_path / "out", binary=failing
        )


def test_rasterize_success_locates_output(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    # Mimic pdftoppm: last argv element is the output prefix; it writes
    # <prefix>-<page>.png (zero-padding depends on total page count).
    binary = _make_script(
        tmp_path,
        "fake-pdftoppm",
        'for last; do :; done\nprintf \'PNG\' > "$last-1.png"\n',
    )

    produced = _rasterize_page(
        tmp_path / "doc.pdf", 0, 150, out_dir, binary=binary
    )

    assert produced.exists()
    assert produced.name == "page-1.png"


def test_rasterize_no_output_raises(tmp_path: Path) -> None:
    silent = _make_script(tmp_path, "silent-pdftoppm", "exit 0")
    with pytest.raises(DocumentParserError, match="produced no output"):
        _rasterize_page(
            tmp_path / "doc.pdf", 0, 150, tmp_path / "out", binary=silent
        )


# ---------------------------------------------------------------------------
# 6. Config round-trip for the three new keys (incl. clamping).
# ---------------------------------------------------------------------------


def test_config_ocr_keys_round_trip_and_clamp(tmp_path: Path) -> None:
    config = Config(config_dir=tmp_path / "cfg")

    # Defaults.
    assert config.tutor_ocr_text_threshold == 32
    assert config.tutor_ocr_dpi == 150
    assert config.tutor_pdftoppm_bin == "pdftoppm"

    # Round-trip.
    config.tutor_ocr_text_threshold = 64
    config.tutor_ocr_dpi = 200
    config.tutor_pdftoppm_bin = "/usr/local/bin/pdftoppm"
    assert config.tutor_ocr_text_threshold == 64
    assert config.tutor_ocr_dpi == 200
    assert config.tutor_pdftoppm_bin == "/usr/local/bin/pdftoppm"

    # Clamping.
    config.tutor_ocr_text_threshold = -5
    assert config.tutor_ocr_text_threshold == 0
    config.tutor_ocr_text_threshold = 99999
    assert config.tutor_ocr_text_threshold == 10000
    config.tutor_ocr_dpi = 10
    assert config.tutor_ocr_dpi == 72
    config.tutor_ocr_dpi = 1200
    assert config.tutor_ocr_dpi == 300

    # Snapshot includes all three.
    snapshot = config.get_tutor_config_snapshot()
    assert snapshot["ocr_text_threshold"] == 10000
    assert snapshot["ocr_dpi"] == 300
    assert snapshot["pdftoppm_bin"] == "/usr/local/bin/pdftoppm"
