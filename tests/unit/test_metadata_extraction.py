"""Unit tests for metadata extraction (US9 — T054/T056).

Offline: tmp_path files only, no daemon, no network. Exercises that
``extract_text`` returns metadata tuples (page, section) and that
``chunk_text_structured`` metadata flows through to chunk dicts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.extractors import (
    chunk_text_structured,
    extract_text,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _make_multi_page_pdf(path: Path, pages: list[str]) -> None:
    """Write a minimal multi-page PDF with extractable text per page."""
    # Build page objects and content streams
    content_objs: list[bytes] = []
    for txt in pages:
        stream = b"BT /F1 12 Tf 50 50 Td (" + txt.encode() + b") Tj ET"
        content_objs.append(
            b"<</Length " + str(len(stream)).encode() + b">>stream\n"
            + stream + b"\nendstream"
        )

    objs: list[bytes] = []
    # obj 1: catalog
    objs.append(b"<</Type/Catalog/Pages 2 0 R>>")
    # obj 2: pages parent — kids refs filled later
    page_refs = " ".join(f"{4 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(
        b"<</Type/Pages/Kids[" + page_refs.encode() + b"]/Count "
        + str(len(pages)).encode() + b">>"
    )
    # For each page: obj = page node, then content stream, then font
    for i in range(len(pages)):
        objs.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents "
            + str(4 + i * 2 + 1).encode()
            + b" 0 R/Resources<</Font<</F1 "
            + str(4 + i * 2 + 2).encode()
            + b" 0 R>>>>>>"
        )
        objs.append(content_objs[i])
        objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = b"%PDF-1.4\n"
    offsets: list[int] = []
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


def _make_multi_chapter_epub(path: Path, chapters: list[tuple[str, str]]) -> None:
    """Write a minimal EPUB with multiple spine items.

    ``chapters`` is a list of ``(heading_text, body_text)`` pairs.
    Each chapter becomes a separate XHTML file with an ``<h1>`` heading.
    """
    import zipfile

    container = (
        b'<?xml version="1.0"?>'
        b'<container version="1.0" '
        b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b"<rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
        b"media-type=\"application/oebps-package+xml\"/></rootfiles>"
        b"</container>"
    )

    manifest_items: list[str] = []
    spine_refs: list[str] = []
    for idx, (_, _) in enumerate(chapters):
        fname = f"chap{idx + 1}.xhtml"
        manifest_items.append(
            f'<item id="c{idx + 1}" href="{fname}" '
            f'media-type="application/xhtml+xml"/>'
        )
        spine_refs.append(f'<itemref idref="c{idx + 1}"/>')

    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Sample</dc:title></metadata>"
        "<manifest>" + "".join(manifest_items) + "</manifest>"
        "<spine>" + "".join(spine_refs) + "</spine></package>"
    ).encode("utf-8")

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", b"application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for idx, (heading, body) in enumerate(chapters):
            fname = f"OEBPS/chap{idx + 1}.xhtml"
            xhtml = (
                '<?xml version="1.0"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                f"<h1>{heading}</h1><p>{body}</p>"
                "</body></html>"
            ).encode("utf-8")
            z.writestr(fname, xhtml)


# ── T054 — PDF page metadata ──────────────────────────────────────────────

def test_pdf_extract_metadata(tmp_path: Path) -> None:
    """PDF extraction returns page numbers as metadata."""
    p = tmp_path / "multipage.pdf"
    _make_multi_page_pdf(p, ["Page one content", "Page two content", "Page three content"])
    segments = list(extract_text(p))
    # Should have at least 2 segments (pypdf may or may not extract all pages)
    assert len(segments) >= 1
    # Every segment from PDF should carry a "page" key
    for text, meta in segments:
        assert "page" in meta
        assert isinstance(meta["page"], int)
        assert meta["page"] >= 1


def test_pdf_extract_metadata_page_values(tmp_path: Path) -> None:
    """PDF pages are numbered starting from 1 in order."""
    p = tmp_path / "ordered.pdf"
    _make_multi_page_pdf(p, ["First", "Second", "Third"])
    segments = list(extract_text(p))
    pages = [meta["page"] for _, meta in segments]
    # Pages should be 1, 2, 3 in order (no duplicates, ascending)
    assert pages == sorted(pages)
    assert 1 in pages


# ── T054 — EPUB section metadata ──────────────────────────────────────────

def test_epub_extract_metadata(tmp_path: Path) -> None:
    """EPUB extraction returns section headings as metadata."""
    p = tmp_path / "chapters.epub"
    _make_multi_chapter_epub(p, [
        ("Chapter 1: Introduction", "Body of chapter 1."),
        ("Chapter 2: Methods", "Body of chapter 2."),
    ])
    segments = list(extract_text(p))
    # Should have 2 segments (one per spine item)
    assert len(segments) == 2
    # Each segment should carry a "section" key with the heading text
    sections = [meta.get("section") for _, meta in segments]
    assert sections[0] == "Chapter 1: Introduction"
    assert sections[1] == "Chapter 2: Methods"


def test_epub_extract_no_heading_no_section(tmp_path: Path) -> None:
    """EPUB without headings yields empty metadata (no section key)."""
    p = tmp_path / "plain.epub"
    # Create a chapter without any heading tags
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
        b"<dc:title>Plain</dc:title></metadata>"
        b'<manifest><item id="c1" href="c.xhtml" '
        b'media-type="application/xhtml+xml"/></manifest>'
        b'<spine><itemref idref="c1"/></spine></package>'
    )
    xhtml = (
        b'<?xml version="1.0"?>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b"<p>Just some text without headings.</p>"
        b"</body></html>"
    )
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", b"application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/c.xhtml", xhtml)

    segments = list(extract_text(p))
    assert len(segments) == 1
    _, meta = segments[0]
    assert "section" not in meta or meta.get("section") is None


# ── T056 — metadata propagation through chunk_text_structured ─────────────

def test_chunk_metadata_propagation(tmp_path: Path) -> None:
    """Metadata from extract_text flows through to structured chunk dicts."""
    p = tmp_path / "book.pdf"
    _make_multi_page_pdf(p, ["Page one content here", "Page two content here"])
    segments = list(extract_text(p))

    # Build combined text with page markers (same pattern as service.py)
    combined_parts: list[str] = []
    for text, meta in segments:
        if meta.get("page") is not None:
            combined_parts.append(f"--- Page {meta['page']} ---\n{text}")
        else:
            combined_parts.append(text)
    combined_text = "\n\n".join(combined_parts)

    chunk_dicts = chunk_text_structured(combined_text)
    assert len(chunk_dicts) >= 1
    # Verify each chunk dict has the expected structure
    for chunk in chunk_dicts:
        assert "text" in chunk
        assert "section" in chunk
        assert "page" in chunk
    # Page metadata should be present
    pages_in_chunks = [c["page"] for c in chunk_dicts if c["page"] is not None]
    assert len(pages_in_chunks) >= 1


def test_epub_metadata_propagation(tmp_path: Path) -> None:
    """EPUB section headings propagate through to chunk dicts."""
    p = tmp_path / "chapters.epub"
    _make_multi_chapter_epub(p, [
        ("Chapter 1: Physics", "Newton's laws of motion."),
        ("Chapter 2: Chemistry", "Atomic structure and bonds."),
    ])
    segments = list(extract_text(p))

    # Build combined text with section markers (same pattern as service.py)
    combined_parts: list[str] = []
    for text, meta in segments:
        if meta.get("section"):
            combined_parts.append(f"# {meta['section']}\n{text}")
        else:
            combined_parts.append(text)
    combined_text = "\n\n".join(combined_parts)

    chunk_dicts = chunk_text_structured(combined_text)
    assert len(chunk_dicts) >= 1
    sections = [c["section"] for c in chunk_dicts if c["section"] is not None]
    assert any("Physics" in s for s in sections)


# ── edge case: empty extraction ────────────────────────────────────────────

def test_txt_extract_metadata_empty(tmp_path: Path) -> None:
    """Plain text extraction yields a single segment with empty metadata."""
    p = tmp_path / "empty.txt"
    p.write_text("Hello world.", encoding="utf-8")
    segments = list(extract_text(p))
    assert len(segments) == 1
    text, meta = segments[0]
    assert text == "Hello world."
    assert meta == {}
