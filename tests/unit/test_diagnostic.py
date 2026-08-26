"""Unit tests for US3 — diagnostic initial / quiz de positionnement (T025).

Tests the prompt builder and the TutorService diagnostic methods.
Fully offline (no daemon, no network).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ollama_tutor.client import InferenceStats, StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.prompts import (
    build_diagnostic_question_prompt,
)
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# T020 — build_diagnostic_question_prompt tests
# ---------------------------------------------------------------------------


def test_build_diagnostic_question_prompt_contains_concept():
    """Prompt must mention the concept name."""
    prompt = build_diagnostic_question_prompt("La dérivée", "intermediate")
    assert "La dérivée" in prompt


def test_build_diagnostic_question_prompt_beginner():
    """Beginner prompt must reference basic / simple vocabulary."""
    prompt = build_diagnostic_question_prompt("La photosynthèse", "beginner")
    # The prompt should mention beginner-level wording
    assert "débutant" in prompt.lower() or "simple" in prompt.lower() or "élémentaire" in prompt.lower()


def test_build_diagnostic_question_prompt_advanced():
    """Advanced prompt must reference advanced / technical content."""
    prompt = build_diagnostic_question_prompt("Relativité générale", "advanced")
    assert "avancé" in prompt.lower() or "analyse critique" in prompt.lower()


def test_build_diagnostic_question_prompt_json_format():
    """Prompt must instruct the LLM to return a JSON with correct keys."""
    prompt = build_diagnostic_question_prompt("Test", "intermediate")
    assert "question" in prompt.lower()
    assert "options" in prompt.lower()
    assert "correct" in prompt.lower()


def test_build_diagnostic_question_prompt_french():
    """Prompt must be entirely in French."""
    prompt = build_diagnostic_question_prompt("Test", "beginner")
    # Should contain typical French phrases
    assert "français" in prompt.lower()


# ---------------------------------------------------------------------------
# T021-T023 — TutorService diagnostic integration tests
# ---------------------------------------------------------------------------


class _FakeDiagnosticClient:
    """Scripted LLM client that returns a fixed diagnostic question JSON."""

    def __init__(self) -> None:
        self.call_count = 0
        self._question = {
            "question": "Qu'est-ce qu'une dérivée ?",
            "options": {
                "A": "La pente de la tangente",
                "B": "Une intégrale",
                "C": "Un nombre premier",
                "D": "Une moyenne",
            },
            "correct": "A",
            "explication": "La dérivée mesure la pente de la tangente à la courbe en un point.",
        }

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0]]

    async def chat_stream(self, messages, model, *, think=False, options=None,
                          format=None, tools=None):
        self.call_count += 1
        # On even calls, change the correct answer to B for variety
        data = dict(self._question)
        if self.call_count % 2 == 0:
            data["correct"] = "B"
            data["explication"] = "Explication alternative."
        yield StreamEvent(kind="content", text=json.dumps(data, ensure_ascii=False))
        yield StreamEvent(
            kind="done",
            stats=InferenceStats(
                model=model,
                prompt_tokens=1,
                generated_tokens=1,
                eval_duration=1.0,
            ),
        )


@pytest.fixture
def _diag_service(tmp_path: Path):
    """Create a TutorService with a fake LLM client for diagnostic tests."""
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    config.tutor_enabled = True
    config.tutor_level = "intermediate"
    client = _FakeDiagnosticClient()
    return TutorService(store, client, config), store, client


def _seed_concepts(store: LibraryStore, subject_name: str = "Mathématiques",
                   concept_names: list[str] | None = None) -> str:
    """Create a subject with concepts and return the subject_id."""
    if concept_names is None:
        concept_names = ["Dérivée", "Intégrale", "Limite", "Continuité"]
    subject = store.create_subject(subject_name)
    for i, name in enumerate(concept_names):
        store.upsert_concept(subject.id, name, path_rank=i)
    return subject.id


def test_start_diagnostic_returns_session(_diag_service, tmp_path: Path):
    """start_diagnostic creates a session and returns the first question."""
    service, store, _client = _diag_service
    subject_id = _seed_concepts(store)
    result = service.start_diagnostic(subject_id)

    assert "session_id" in result
    assert result["question_num"] == 1
    assert result["total_questions"] == 4  # 4 concepts
    assert isinstance(result["options"], dict)
    assert len(result["options"]) == 4


def test_start_diagnostic_no_concepts_raises(_diag_service, tmp_path: Path):
    """start_diagnostic raises KeyError when the subject has no concepts."""
    service, store, _client = _diag_service
    subject = store.create_subject("Vide")
    with pytest.raises(KeyError, match="Aucun concept"):
        service.start_diagnostic(subject.id)


def test_submit_diagnostic_answer_correct(_diag_service, tmp_path: Path):
    """submit_diagnostic_answer processes a correct answer and returns next question."""
    service, store, _client = _diag_service
    subject_id = _seed_concepts(store)
    start = service.start_diagnostic(subject_id)
    session_id = start["session_id"]

    # First question has correct = "A" (call_count=1)
    result = service.submit_diagnostic_answer(session_id, "A")

    assert result["session_id"] == session_id
    assert result["correct"] is True
    assert result["explanation"] != ""
    assert result["next_question"] is not None
    assert result["is_finished"] is False
    assert result["next_question"]["question_num"] == 2


def test_submit_diagnostic_answer_incorrect(_diag_service, tmp_path: Path):
    """submit_diagnostic_answer processes an incorrect answer."""
    service, store, _client = _diag_service
    subject_id = _seed_concepts(store)
    start = service.start_diagnostic(subject_id)
    session_id = start["session_id"]

    # Submit a wrong answer
    result = service.submit_diagnostic_answer(session_id, "Z")

    assert result["correct"] is False
    assert result["next_question"] is not None


def test_submit_diagnostic_finished(_diag_service, tmp_path: Path):
    """Last question returns is_finished=True with a result."""
    service, store, _client = _diag_service
    # Only 2 concepts → 2 questions total
    subject_id = _seed_concepts(store, concept_names=["Dérivée", "Intégrale"])
    start = service.start_diagnostic(subject_id)
    session_id = start["session_id"]

    # Answer first question
    service.submit_diagnostic_answer(session_id, "A")
    # Answer second (last) question
    result = service.submit_diagnostic_answer(session_id, "B")

    assert result["is_finished"] is True
    assert result["next_question"] is None
    assert "result" in result
    diag_result = result["result"]
    assert diag_result["total_questions"] == 2
    assert isinstance(diag_result["score_pct"], float)
    assert isinstance(diag_result["strengths"], list)
    assert isinstance(diag_result["weaknesses"], list)
    assert isinstance(diag_result["suggested_path"], str)


def test_get_diagnostic_result(_diag_service, tmp_path: Path):
    """get_diagnostic_result computes scores and identifies strengths/weaknesses."""
    service, store, _client = _diag_service
    subject_id = _seed_concepts(store, concept_names=["A", "B", "C"])
    start = service.start_diagnostic(subject_id)
    session_id = start["session_id"]

    # Answer all 3 questions
    service.submit_diagnostic_answer(session_id, "A")
    service.submit_diagnostic_answer(session_id, "B")
    service.submit_diagnostic_answer(session_id, "C")

    result = service.get_diagnostic_result(session_id)
    assert result["total_questions"] == 3
    assert len(result["per_concept"]) == 3
    assert 0.0 <= result["score_pct"] <= 100.0


def test_get_diagnostic_result_unknown_session(_diag_service):
    """get_diagnostic_result raises KeyError for an unknown session."""
    service, _, _ = _diag_service
    with pytest.raises(KeyError, match="Session introuvable"):
        service.get_diagnostic_result("nonexistent")
