"""Unit tests for tutor extractors (T010, US1).

Offline: tmp_path files only, no daemon, no network. Exercises
``extract_text`` (txt/md passthrough, pdf via pypdf, epub via
zipfile+xml.etree+html.parser) and ``chunk_text`` (max_chars/overlap,
word-boundary respect).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.extractors import chunk_text, extract_text


def _make_pdf(path: Path, text: str) -> None:
    """Write a minimal but valid single-page PDF with extractable text."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length "
        + str(len(text) + 26).encode()
        + b">>stream\nBT /F1 12 Tf 50 50 Td ("
        + text.encode()
        + b") Tj ET\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<</Size " + str(len(objs) + 1).encode() + b"/Root 1 0 R>>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    path.write_bytes(out)


def _make_epub(path: Path, html_text: str) -> None:
    """Write a minimal EPUB (zip of XHTML + OPF + container)."""
    import xml.etree.ElementTree as ET
    import zipfile

    container = (
        b'<?xml version="1.0"?>'
        b'<container version="1.0" '
        b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b"<rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
        b"media-type=\"application/oebps-package+xml\"/></rootfiles>"
        b"</container>"
    )
    opf = (
        b'<?xml version="1.0"?>'
        b'<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        b"<dc:title>Sample</dc:title></metadata>"
        b'<manifest><item id="c1" href="chap1.xhtml" '
        b'media-type="application/xhtml+xml"/></manifest>'
        b'<spine><itemref idref="c1"/></spine></package>'
    )
    xhtml = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"<p>{html_text}</p></body></html>"
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", b"application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/chap1.xhtml", xhtml)


def test_chunk_text_respects_max_chars_and_overlap() -> None:
    words = ["word"] * 5000  # ~4 chars each -> ~20000 chars
    text = " ".join(words)
    chunks = chunk_text(text, max_chars=1200, overlap=200)
    assert chunks, "expected at least one chunk"
    assert len(chunks) > 1
    for c in chunks:
        # each chunk holds at most max_chars (plus at most one trailing word)
        assert len(c) <= 1200 + len("word"), f"chunk too long: {len(c)}"
    # overlap: the first word of a later chunk appears inside the prior chunk
    for a, b in zip(chunks, chunks[1:]):
        assert b.split()[0] in a, "consecutive chunks should overlap"


def test_chunk_text_never_splits_a_word() -> None:
    text = " ".join(f"token{i}" for i in range(2000))
    tokens = set(text.split())
    chunks = chunk_text(text, max_chars=1200, overlap=200)
    for c in chunks:
        for tok in c.split():
            assert tok in tokens, f"partial word leaked: {tok!r}"


def test_chunk_text_defaults() -> None:
    text = "alpha beta gamma delta " * 1000
    chunks = chunk_text(text)  # defaults: 1200 / 200
    assert all(len(c) <= 1200 + 20 for c in chunks)
    assert len(chunks) > 1


def test_chunk_text_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_extract_txt_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "note.txt"
    p.write_text("Hello plain text world.", encoding="utf-8")
    segments = list(extract_text(p))
    assert len(segments) == 1
    assert segments[0][0] == "Hello plain text world."
    assert segments[0][1] == {}


def test_extract_md_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "note.md"
    p.write_text("# Title\n\nSome **markdown** body.", encoding="utf-8")
    segments = list(extract_text(p))
    assert len(segments) == 1
    text, meta = segments[0]
    assert "Title" in text and "markdown" in text
    assert meta == {}


def test_extract_pdf_returns_text(tmp_path: Path) -> None:
    p = tmp_path / "book.pdf"
    _make_pdf(p, "Hello PDF world from pypdf")
    segments = list(extract_text(p))
    assert len(segments) >= 1
    texts = [t for t, _ in segments]
    assert any("Hello PDF world from pypdf" in t for t in texts)


def test_extract_epub_strips_tags(tmp_path: Path) -> None:
    p = tmp_path / "book.epub"
    _make_epub(p, "Hello <b>epub</b> stripped world")
    segments = list(extract_text(p))
    assert len(segments) >= 1
    text = segments[0][0]
    assert "Hello" in text and "epub" in text and "stripped" in text
    assert "<b>" not in text and "</p>" not in text


def test_extract_unsupported_raises(tmp_path: Path) -> None:
    p = tmp_path / "doc.xyz_unknown"
    p.write_text("x")
    with pytest.raises(ValueError):
        extract_text(p)


def test_extract_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "nope.txt")
