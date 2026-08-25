"""Unit tests for tutor prompt construction (T026, US3).

Covers the socratic × level system-prompt matrix, the context budget, the
in-conversation override helpers, and the think-flag forwarding into the
``chat_stream`` payload. Fully offline (no daemon, no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.client import InferenceStats, StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.prompts import (
    build_system_prompt,
    build_think_instruction,
    build_user_prompt,
    detect_level_override,
    detect_socratic_override,
    resolve_overrides,
)
from src.ollama_tutor.tutor.retrieval import assemble_context_blocks
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.tutor.vector import ScoredChunk

_LEVELS = ["beginner", "intermediate", "advanced", "expert"]


def _sources() -> list[dict]:
    return [{"book": "Livre A", "chapter": "1", "page": 12, "score": 0.9}]


# ---------------------------------------------------------------------------
# System-prompt matrix (socratic × level)
# ---------------------------------------------------------------------------


def test_socratic_on_has_guiding_question_directive():
    prompt = build_system_prompt("Math", "intermediate", True, _sources())
    # guiding-question directive present when socratic ON
    assert "questions" in prompt
    assert "guide" in prompt
    # direct-answer directive must NOT be present when socratic is on
    assert "réponse directe" not in prompt


def test_socratic_off_has_direct_answer_directive():
    prompt = build_system_prompt("Math", "intermediate", False, _sources())
    # direct-answer directive present when socratic OFF
    assert "direct" in prompt
    assert "réponse claire" in prompt
    # guiding-question directive must NOT be present when socratic is off
    assert "guide l'élève par des questions" not in prompt


def test_level_directives_differ():
    prompts = {
        lvl: build_system_prompt("Math", lvl, True, _sources()) for lvl in _LEVELS
    }
    # every pair of levels yields a distinct system prompt
    for i, a in enumerate(_LEVELS):
        for b in _LEVELS[i + 1:]:
            assert prompts[a] != prompts[b], f"{a} and {b} prompts are identical"
    # each level carries its own depth marker
    assert "vocabulaire simple" in prompts["beginner"]
    assert "accessibilité" in prompts["intermediate"]
    assert "nuance" in prompts["advanced"]
    assert "dense" in prompts["expert"]


def test_system_prompt_within_context_budget():
    # The system prompt itself must stay well under the 6000-char context cap.
    for socratic in (True, False):
        for lvl in _LEVELS:
            prompt = build_system_prompt("Math", lvl, socratic, _sources())
            assert len(prompt) <= 6000


# ---------------------------------------------------------------------------
# Context budget (research D11, FR-015)
# ---------------------------------------------------------------------------


def _big_chunks(n: int = 10) -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk_id=str(i),
            book_id="b",
            book_title="Livre A",
            chapter="1",
            page=i,
            score=0.9,
            text="x" * 2000,
        )
        for i in range(n)
    ]


def test_context_budget_respected():
    # Many oversized chunks must be truncated to <=6000 chars of context.
    ctx = assemble_context_blocks(_big_chunks())
    assert len(ctx) <= 6000


def test_build_user_prompt_respects_context_budget():
    full = build_user_prompt("Question ?", _big_chunks())
    # The grounded-context portion is capped; the whole prompt must not blow up.
    assert len(full) <= 6000 + 200  # small overhead for the question prefix


# ---------------------------------------------------------------------------
# In-conversation override helpers (FR-014)
# ---------------------------------------------------------------------------


def test_detect_level_override():
    assert detect_level_override("explain like I'm a beginner") == "beginner"
    assert detect_level_override("réponds pour un expert") == "expert"
    assert detect_level_override("niveau avancé svp") == "advanced"
    assert detect_level_override("question normale") is None


def test_detect_socratic_override():
    assert detect_socratic_override("réponds directement") is False
    assert detect_socratic_override("guide moi par des questions") is True
    assert detect_socratic_override("question normale") is None


def test_resolve_overrides_wins_over_config():
    # In-conversation override beats the supplied (config) level/socratic.
    lvl, soc = resolve_overrides("explain like I'm a beginner", "expert", True)
    assert lvl == "beginner"
    # no socratic override in the question → keep the supplied value
    assert soc is True


def test_build_think_instruction_defaults_false():
    assert build_think_instruction(False) == ""
    assert build_think_instruction(True) != ""


# ---------------------------------------------------------------------------
# Think flag forwarding into chat_stream (D10)
# ---------------------------------------------------------------------------


class _FakeTutorClient:
    """Scripted Ollama client recording the ``think`` kwarg of chat_stream."""

    def __init__(self) -> None:
        self.think_calls: list[bool] = []

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        # Return a vector matching the seeded chunk so retrieval succeeds.
        return [[1.0, 0.0, 0.0, 0.0]]

    async def chat_stream(self, messages, model, *, think=False, options=None,
                          format=None, tools=None):
        self.think_calls.append(bool(think))
        yield StreamEvent(kind="content", text="réponse fondée")
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
def _service(tmp_path: Path):
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    config.tutor_enabled = True
    client = _FakeTutorClient()
    return TutorService(store, client, config), store, client


async def _seed(store: LibraryStore, tmp_path: Path, name: str = "Math",
               text: str = "contenu propre au sujet"):
    subject = store.create_subject(name)
    p = tmp_path / f"{name}.txt"
    p.write_text(text)
    book = store.import_document(subject.id, p)
    store.add_chunks(subject.id, book.id, [text], [[1.0, 0.0, 0.0, 0.0]],
                     "embeddinggemma")
    store.mark_indexed(book.id, 1)
    return subject


@pytest.mark.asyncio
async def test_think_default_false_forwarded(_service, tmp_path: Path):
    service, store, client = _service
    subject = await _seed(store, tmp_path)
    client.think_calls.clear()
    frames = [f async for f in service.ask(subject.name, "question ?")]
    # think defaults to False (config.tutor_think) and is forwarded as-is.
    assert client.think_calls == [False]
    assert frames[-1]["type"] == "end"


@pytest.mark.asyncio
async def test_think_override_true_forwarded(_service, tmp_path: Path):
    service, store, client = _service
    subject = await _seed(store, tmp_path)
    client.think_calls.clear()
    frames = [f async for f in service.ask(subject.name, "question ?", think=True)]
    # per-request override True is forwarded into chat_stream.
    assert client.think_calls == [True]
    assert frames[-1]["type"] == "end"
