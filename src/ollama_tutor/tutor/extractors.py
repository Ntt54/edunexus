"""Text extraction + chunking for the local AI tutor (research D3).

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import hashlib
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


SUPPORTED_FORMATS = {".txt", ".md", ".pdf", ".epub"}


class _TagStripper(HTMLParser):
    """Collect visible text, dropping all markup (research D3 EPUB path)."""

    _BLOCK = {
        "p", "div", "br", "li", "tr", "td", "th", "section", "article",
        "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._BLOCK:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK:
            self._parts.append(" ")

    def get_text(self) -> str:
        return "".join(self._parts)

    def reset_text(self) -> None:
        self._parts = []


def extract_text(path, fmt: str | None = None) -> str:
    """Extract plain text from a book file.

    Args:
        path: Path to the document.
        fmt: Optional explicit format (``"txt"``, ``"md"``, ``"pdf"``,
            ``"epub"``); auto-detected from the file extension when omitted.

    Returns:
        The extracted plain-text content.

    Raises:
        FileNotFoundError: when the path does not exist.
        ValueError: for unsupported formats.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No such file: {p}")
    suffix = p.suffix.lower()
    if fmt is None:
        if suffix not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {suffix or '(none)'}")
        fmt = suffix.lstrip(".")
    else:
        fmt = fmt.lower().lstrip(".")
        if fmt not in {f.lstrip(".") for f in SUPPORTED_FORMATS}:
            raise ValueError(f"Unsupported format: {fmt}")

    if fmt in ("txt", "md"):
        return p.read_text(encoding="utf-8", errors="replace")
    if fmt == "pdf":
        return _extract_pdf(p)
    if fmt == "epub":
        return _extract_epub(p)
    raise ValueError(f"Unsupported format: {fmt}")


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text:
            parts.append(text)
    return "\n".join(parts)


_OPF_NS = "{http://www.idpf.org/2007/opf}"


def _find_opf(zf: zipfile.ZipFile, names: list[str]) -> str | None:
    if "META-INF/container.xml" in names:
        try:
            root = ET.fromstring(zf.read("META-INF/container.xml"))
        except ET.ParseError:
            root = None
        if root is not None:
            for el in root.iter():
                if el.tag.endswith("rootfile"):
                    fp = el.get("full-path")
                    if fp:
                        return fp
    for n in names:
        if n.endswith(".opf"):
            return n
    return None


def _spine_hrefs(zf: zipfile.ZipFile, opf_path: str) -> list[str]:
    try:
        root = ET.fromstring(zf.read(opf_path))
    except ET.ParseError:
        return []
    manifest: dict[str, str] = {}
    for item in root.iter(_OPF_NS + "item"):
        hid = item.get("id")
        href = item.get("href")
        if hid and href:
            manifest[hid] = href
    base = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    hrefs: list[str] = []
    for ref in root.iter(_OPF_NS + "itemref"):
        hid = ref.get("idref")
        if hid and hid in manifest:
            href = manifest[hid]
            hrefs.append(f"{base}/{href}" if base and not href.startswith("/") else href)
    return hrefs


def _extract_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        opf_path = _find_opf(zf, names)
        hrefs = _spine_hrefs(zf, opf_path) if opf_path else []
        if not hrefs:
            hrefs = [n for n in names if n.endswith((".xhtml", ".html", ".htm"))]
        stripper = _TagStripper()
        texts: list[str] = []
        for href in hrefs:
            try:
                data = zf.read(href).decode("utf-8", "replace")
            except KeyError:
                continue
            stripper.feed(data)
            texts.append(stripper.get_text())
            stripper.reset_text()
    return "\n".join(t for t in texts if t).strip()


def fingerprint(text: str) -> str:
    """sha256 of whitespace-normalized text (research D5)."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def chunk_text(
    text: str,
    max_chars: int = 1200,
    overlap: int = 200,
) -> list[str]:
    """Split ``text`` into word-boundary chunks (research D3).

    Each chunk holds at most ``max_chars`` characters (a single over-long word
    is kept whole). Consecutive chunks overlap by roughly ``overlap``
    characters, snapped to word boundaries so no word is ever split across a
    chunk boundary.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= max_chars:
        overlap = max_chars - 1

    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    n = len(words)
    while start < n:
        end = start
        total = 0
        while end < n:
            w = words[end]
            add = len(w) + (1 if end > start else 0)
            if total + add > max_chars and end > start:
                break
            total += add
            end += 1
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end >= n:
            break
        # step back for overlap, snapped to word boundaries
        step = 0
        covered = 0
        for w in reversed(words[start:end]):
            if covered >= overlap:
                break
            covered += len(w) + 1
            step += 1
        next_start = end - step
        if next_start <= start:
            next_start = start + 1  # guarantee forward progress
        start = next_start
    return chunks
