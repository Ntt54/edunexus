"""Unit tests for program capture pipeline (Feature 008, US6).

Covers structuring recognized text into an editable tree (T034) and the
incremental capture flow with status (T035). OCR is not required: the
service falls back to reading the source file as text when no parser is
provided.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.program_capture import ProgramCaptureService
from src.ollama_tutor.tutor.store import LibraryStore


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def _write_source(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "program.txt"
    p.write_text(content, encoding="utf-8")
    return p


def test_capture_structures_numbered_headings(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    src = _write_source(tmp_path, "1 Introduction\n1.1 Variables\n1.2 Boucles\n2 Fonctions\n")
    svc = ProgramCaptureService(store)  # no parser -> text fallback
    result = _run(svc.capture(subject.id, str(src), source_type="pdf"))
    program = result["program"]
    assert program["status"] == "ready"
    nodes = program["nodes"]
    assert len(nodes) >= 4
    kinds = [n["kind"] for n in nodes]
    assert "chapter" in kinds and "sub_part" in kinds


def test_capture_marks_uncertain_lines_pending(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    src = _write_source(tmp_path, "1 Introduction\nxyz\n")
    svc = ProgramCaptureService(store)
    result = _run(svc.capture(subject.id, str(src)))
    nodes = result["program"]["nodes"]
    assert all(n["validation_status"] == "pending" for n in nodes)


def test_correct_node_updates_title(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    src = _write_source(tmp_path, "1 Introduction\n")
    svc = ProgramCaptureService(store)
    result = _run(svc.capture(subject.id, str(src)))
    node_id = result["program"]["nodes"][0]["id"]
    corrected = svc.correct_node(node_id, "Introduction à Java")
    assert corrected["title"] == "Introduction à Java"
    assert corrected["validation_status"] == "corrected"


def test_confirm_marks_program_confirmed(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    src = _write_source(tmp_path, "1 Introduction\n")
    svc = ProgramCaptureService(store)
    result = _run(svc.capture(subject.id, str(src)))
    program_id = result["program"]["id"]
    confirmed = svc.confirm(program_id)
    assert confirmed["program"]["validation_status"] == "confirmed"


def test_capture_error_surfaces_on_program(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    svc = ProgramCaptureService(store)
    result = _run(svc.capture(subject.id, str(tmp_path / "missing.txt")))
    assert result["program"]["status"] == "error"
