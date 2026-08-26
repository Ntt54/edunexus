"""Tests for US14 — Mode Épreuve (T079-T085).

Covers:
  - build_exam_analysis_prompt (T080): prompt correctly formatted
  - parse_exam_document (T079): extracts text from file paths
  - analyze_exam (T081): mock LLM, verify question extraction
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ollama_tutor.client import InferenceStats, StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.prompts import (
    build_exam_analysis_prompt,
    build_exam_resolve_prompt,
)
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# T080 — build_exam_analysis_prompt: prompt correctly formatted
# ---------------------------------------------------------------------------


def test_build_exam_analysis_prompt_contains_ocr_header():
    """The prompt should contain the OCR exam text."""
    messages = build_exam_analysis_prompt("Question 1: Calculez 2+2.")
    assert len(messages) == 2
    system_msg = messages[0]
    user_msg = messages[1]
    # System prompt mentions JSON output
    assert "JSON" in system_msg.content
    assert "questions" in system_msg.content
    # User prompt contains the OCR text
    assert "Question 1: Calculez 2+2." in user_msg.content
    assert "Texte OCR" in user_msg.content


def test_build_exam_analysis_prompt_empty_text():
    """Prompt should handle empty exam text gracefully."""
    messages = build_exam_analysis_prompt("")
    assert len(messages) == 2
    # Empty text should still be present in user message
    user_msg = messages[1]
    assert "Texte OCR" in user_msg.content


def test_build_exam_resolve_prompt_hint_level_0_full_answer():
    """Hint level 0 should request a full answer."""
    messages = build_exam_resolve_prompt(
        "Calculez la dérivée de x²",
        ["dérivation", "polynôme"],
        hint_level=0,
    )
    assert len(messages) == 2
    system_msg = messages[0]
    user_msg = messages[1]
    assert "Réponse complète" in system_msg.content
    assert "Calculez la dérivée de x²" in user_msg.content
    assert "dérivation" in user_msg.content


def test_build_exam_resolve_prompt_hint_level_2():
    """Hint level 2 should request an intermediate hint."""
    messages = build_exam_resolve_prompt(
        "Qu'est-ce que la photosynthèse ?",
        ["biologie"],
        hint_level=2,
    )
    system_msg = messages[0]
    user_msg = messages[1]
    assert "indice" in system_msg.content.lower()
    assert "Niveau d'indice demandé : 2/3" in user_msg.content


def test_build_exam_resolve_prompt_with_rag_context():
    """RAG context should be included when provided."""
    messages = build_exam_resolve_prompt(
        "Expliquez la mitose",
        ["cellule"],
        hint_level=0,
        rag_context="La mitose est la division cellulaire...",
    )
    user_msg = messages[1]
    assert "La mitose est la division cellulaire" in user_msg.content
    assert "Extraits pertinents" in user_msg.content


# ---------------------------------------------------------------------------
# T079 — parse_exam_document: extracts text from file paths
# ---------------------------------------------------------------------------


def test_parse_exam_document_txt(tmp_path: Path):
    """parse_exam_document should read .txt files."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient()
    service = TutorService(store, client, config)

    txt_file = tmp_path / "exam.txt"
    txt_file.write_text("Question 1: Quelle est la capitale de la France ?")

    result = service.parse_exam_document([str(txt_file)])
    assert "Question 1" in result
    assert "France" in result


def test_parse_exam_document_multiple_files(tmp_path: Path):
    """parse_exam_document should concatenate text from multiple files."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient()
    service = TutorService(store, client, config)

    f1 = tmp_path / "part1.txt"
    f1.write_text("Partie 1 du sujet")
    f2 = tmp_path / "part2.txt"
    f2.write_text("Partie 2 du sujet")

    result = service.parse_exam_document([str(f1), str(f2)])
    assert "Partie 1 du sujet" in result
    assert "Partie 2 du sujet" in result


def test_parse_exam_document_image_stub(tmp_path: Path):
    """parse_exam_document should return a placeholder for image files."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient()
    service = TutorService(store, client, config)

    img_file = tmp_path / "scan.png"
    img_file.write_bytes(b"\x89PNG dummy data")

    result = service.parse_exam_document([str(img_file)])
    assert "Image OCR non disponible" in result
    assert "scan.png" in result


def test_parse_exam_document_file_not_found(tmp_path: Path):
    """parse_exam_document should raise FileNotFoundError for missing paths."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient()
    service = TutorService(store, client, config)

    with pytest.raises(FileNotFoundError):
        service.parse_exam_document([str(tmp_path / "missing.pdf")])


def test_parse_exam_document_unsupported_format(tmp_path: Path):
    """parse_exam_document should raise ValueError for unsupported formats."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient()
    service = TutorService(store, client, config)

    bad_file = tmp_path / "data.xyz"
    bad_file.write_text("content")

    with pytest.raises(ValueError, match="Unsupported"):
        service.parse_exam_document([str(bad_file)])


# ---------------------------------------------------------------------------
# T081 — analyze_exam: mock LLM, verify question extraction
# ---------------------------------------------------------------------------


class _FakeExamClient:
    """Scripted Ollama client that returns a JSON exam analysis."""

    def __init__(self, response_json: dict | None = None):
        self._response = response_json or {
            "questions": [
                {
                    "number": 1,
                    "statement": "Calculez 2 + 2",
                    "concepts": ["addition", "arithmétique"],
                    "status": "pending",
                },
                {
                    "number": 2,
                    "statement": "Quelle est la capitale de la France ?",
                    "concepts": ["géographie"],
                    "status": "pending",
                },
            ]
        }
        self.call_count = 0

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0]]

    async def chat_stream(self, messages, model, *, think=False, options=None,
                          format=None, tools=None):
        self.call_count += 1
        content = json.dumps(self._response, ensure_ascii=False)
        yield StreamEvent(kind="content", text=content)
        yield StreamEvent(
            kind="done",
            stats=InferenceStats(
                model=model,
                prompt_tokens=10,
                generated_tokens=20,
                eval_duration=0.5,
            ),
        )


@pytest.mark.asyncio
async def test_analyze_exam_extracts_questions(tmp_path: Path):
    """analyze_exam should parse LLM JSON into structured question list."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient()
    service = TutorService(store, client, config)

    questions = await service.analyze_exam("Question 1: Calculez 2+2")

    assert len(questions) == 2
    assert questions[0]["number"] == 1
    assert "2 + 2" in questions[0]["statement"]
    assert "addition" in questions[0]["concepts"]
    assert questions[0]["status"] == "pending"
    assert questions[1]["number"] == 2
    assert "France" in questions[1]["statement"]


@pytest.mark.asyncio
async def test_analyze_exam_empty_response(tmp_path: Path):
    """analyze_exam should return empty list for empty LLM response."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeExamClient(response_json={"questions": []})
    service = TutorService(store, client, config)

    questions = await service.analyze_exam("some text")
    assert questions == []


@pytest.mark.asyncio
async def test_analyze_exam_malformed_json(tmp_path: Path):
    """analyze_exam should return empty list when LLM returns garbage."""

    class _BadClient:
        async def embed(self, model, inputs):
            return [[1.0]]

        async def chat_stream(self, messages, model, *, think=False,
                              options=None, format=None, tools=None):
            yield StreamEvent(kind="content", text="I don't know how to answer")
            yield StreamEvent(kind="done", stats=None)

    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    service = TutorService(store, _BadClient(), config)

    questions = await service.analyze_exam("some text")
    assert questions == []


# ---------------------------------------------------------------------------
# T082 — resolve_exam_question: mock LLM, full answer and hints
# ---------------------------------------------------------------------------


class _FakeResolveClient:
    """Scripted client for resolve_exam_question tests."""

    def __init__(self):
        self.last_messages = []

    async def embed(self, model, inputs):
        return [[1.0]]

    async def chat_stream(self, messages, model, *, think=False,
                          options=None, format=None, tools=None):
        self.last_messages = list(messages)
        yield StreamEvent(kind="content", text="Réponse complète du tuteur.")
        yield StreamEvent(kind="done", stats=None)


@pytest.mark.asyncio
async def test_resolve_exam_question_full_answer(tmp_path: Path):
    """resolve_exam_question with hint_level=0 should return a full answer."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeResolveClient()
    service = TutorService(store, client, config)

    result = await service.resolve_exam_question(
        "Calculez la dérivée de x²",
        ["dérivation"],
        hint_level=0,
    )
    assert result["hint_level"] == 0
    assert "Réponse complète" in result["text"]


@pytest.mark.asyncio
async def test_resolve_exam_question_hint(tmp_path: Path):
    """resolve_exam_question with hint_level>0 should return a hint."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeResolveClient()
    service = TutorService(store, client, config)

    result = await service.resolve_exam_question(
        "Qu'est-ce que la mitose ?",
        ["cellule"],
        hint_level=2,
    )
    assert result["hint_level"] == 2
    assert len(result["text"]) > 0


@pytest.mark.asyncio
async def test_resolve_exam_question_clamps_hint_level(tmp_path: Path):
    """resolve_exam_question should clamp hint_level to [0, 3]."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    client = _FakeResolveClient()
    service = TutorService(store, client, config)

    result = await service.resolve_exam_question(
        "Question ?", ["concept"], hint_level=10
    )
    assert result["hint_level"] == 3  # clamped to max
