"""Unit tests for auto-generated learning paths (US13 / T074-T078).

Covers:
- build_learning_path_prompt formatting (T074)
- TutorService.auto_generate_path LLM interaction and path creation (T075)
- _parse_learning_path_response fallback and filtering (T075 helper)

Fully offline (no daemon, no network). Async tests use explicit
``@pytest.mark.asyncio`` per project conventions.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ollama_tutor.client import InferenceStats, StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.prompts import build_learning_path_prompt
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# Fixtures & fakes
# ---------------------------------------------------------------------------


class _FakeAutoPathClient:
    """Scripted LLM client returning a fixed ordered concept list."""

    def __init__(self, ordered: list[str] | None = None) -> None:
        self.ordered = ordered or ["Géométrie", "Algèbre", "Analyse"]

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        return [[0.0] * 8]

    async def chat_stream(self, messages, model, *, think=False, options=None,
                          format=None, tools=None):
        yield StreamEvent(
            kind="content",
            text=json.dumps(self.ordered, ensure_ascii=False),
        )
        yield StreamEvent(
            kind="done",
            stats=InferenceStats(
                model=model,
                prompt_tokens=10,
                generated_tokens=5,
                eval_duration=1.0,
            ),
        )

    async def close(self):
        pass


class _FakeBadLLMClient:
    """LLM client returning unparsable text (tests fallback behaviour)."""

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        return [[0.0] * 8]

    async def chat_stream(self, messages, model, *, think=False, options=None,
                          format=None, tools=None):
        yield StreamEvent(kind="content", text="je ne peux pas ordonner")
        yield StreamEvent(
            kind="done",
            stats=InferenceStats(
                model=model,
                prompt_tokens=10,
                generated_tokens=5,
                eval_duration=1.0,
            ),
        )

    async def close(self):
        pass


@pytest.fixture
def store(tmp_path: Path):
    s = LibraryStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def subject(store):
    return store.create_subject("Mathématiques")


def _config(tmp_path: Path) -> Config:
    """Build a minimal Config pointing at the test directory."""
    c = Config(config_dir=tmp_path)
    c.tutor_enabled = True
    c.tutor_level = "intermediate"
    c.tutor_model = "gemma3:1b"
    return c


# ---------------------------------------------------------------------------
# T074: build_learning_path_prompt
# ---------------------------------------------------------------------------


class TestBuildLearningPathPrompt:
    def test_contains_all_concepts(self):
        concepts = ["Algèbre", "Géométrie", "Analyse"]
        prompt = build_learning_path_prompt(concepts, [], "beginner")
        for c in concepts:
            assert c in prompt

    def test_contains_gap_names(self):
        gaps = ["Algèbre"]
        prompt = build_learning_path_prompt(["Algèbre", "Géométrie"], gaps)
        assert "Algèbre" in prompt
        assert "points faibles" in prompt.lower() or "prioriser" in prompt.lower()

    def test_json_output_instruction(self):
        prompt = build_learning_path_prompt(["A", "B"], [], "advanced")
        assert "JSON" in prompt
        assert '["Concept 1"' in prompt

    def test_empty_concepts(self):
        prompt = build_learning_path_prompt([], [])
        assert "aucun concept" in prompt.lower()

    def test_empty_gaps(self):
        prompt = build_learning_path_prompt(["A"], [])
        assert "aucun point faible identifié" in prompt.lower()

    def test_level_included(self):
        prompt = build_learning_path_prompt(["A"], [], "expert")
        assert "expert" in prompt.lower()

    def test_french_language(self):
        prompt = build_learning_path_prompt(["A"], [])
        assert "français" in prompt.lower()

    def test_exact_count_instruction(self):
        concepts = ["X", "Y", "Z"]
        prompt = build_learning_path_prompt(concepts, [])
        assert "3 éléments" in prompt

    def test_within_context_budget(self):
        """Prompt should stay well under 6000 chars even with many concepts."""
        concepts = [f"Concept_{i}" for i in range(20)]
        gaps = [f"Concept_{i}" for i in range(5)]
        prompt = build_learning_path_prompt(concepts, gaps, "advanced")
        assert len(prompt) <= 6000


# ---------------------------------------------------------------------------
# T075: TutorService._parse_learning_path_response
# ---------------------------------------------------------------------------


class TestParseLearningPathResponse:
    def test_valid_json_array(self):
        result = TutorService._parse_learning_path_response(
            '["A", "B", "C"]', ["A", "B", "C"]
        )
        assert result == ["A", "B", "C"]

    def test_code_fence_stripped(self):
        raw = '```json\n["B", "A"]\n```'
        result = TutorService._parse_learning_path_response(raw, ["A", "B"])
        assert result == ["B", "A"]

    def test_filters_unknown_names(self):
        result = TutorService._parse_learning_path_response(
            '["A", "Unknown", "B"]', ["A", "B"]
        )
        assert result == ["A", "B"]

    def test_appends_missing_concepts(self):
        result = TutorService._parse_learning_path_response(
            '["A"]', ["A", "B", "C"]
        )
        assert result == ["A", "B", "C"]

    def test_deduplicates(self):
        result = TutorService._parse_learning_path_response(
            '["A", "A", "B"]', ["A", "B"]
        )
        assert result == ["A", "B"]

    def test_fallback_on_invalid_json(self):
        result = TutorService._parse_learning_path_response(
            "not json at all", ["A", "B"]
        )
        assert result == ["A", "B"]

    def test_fallback_on_empty_string(self):
        result = TutorService._parse_learning_path_response("", ["A"])
        assert result == ["A"]

    def test_fallback_on_non_list(self):
        result = TutorService._parse_learning_path_response(
            '{"order": ["A"]}', ["A"]
        )
        assert result == ["A"]

    def test_case_insensitive_match(self):
        result = TutorService._parse_learning_path_response(
            '["algebre", "GEOMETRIE"]', ["Algèbre", "Géométrie"]
        )
        assert len(result) == 2


# ---------------------------------------------------------------------------
# T075: auto_generate_path
# ---------------------------------------------------------------------------


class TestAutoGeneratePath:
    @pytest.mark.asyncio
    async def test_creates_path_with_steps(self, store, subject, tmp_path):
        """Happy path: concepts exist, LLM returns ordered list."""
        c1 = store.upsert_concept(subject.id, "Algèbre")
        c2 = store.upsert_concept(subject.id, "Géométrie")
        c3 = store.upsert_concept(subject.id, "Analyse")

        config = _config(tmp_path)
        client = _FakeAutoPathClient(["Géométrie", "Algèbre", "Analyse"])
        svc = TutorService(store, client, config)

        result = await svc.auto_generate_path(subject.id)

        assert result["subject_id"] == subject.id
        assert "Parcours auto-généré" in result["title"]
        assert result["status"] == "draft"
        steps = result["steps"]
        assert len(steps) == 3
        # Check ordering matches LLM output.
        step_names = [s["title"] for s in steps]
        assert step_names == ["Géométrie", "Algèbre", "Analyse"]
        # Check ordinal sequence.
        assert [s["ordinal"] for s in steps] == [0, 1, 2]
        # All steps are concept type.
        assert all(s["activity_type"] == "concept" for s in steps)

    @pytest.mark.asyncio
    async def test_no_concepts_raises(self, store, subject, tmp_path):
        """Empty concept list should raise KeyError."""
        config = _config(tmp_path)
        client = _FakeAutoPathClient()
        svc = TutorService(store, client, config)

        with pytest.raises(KeyError, match="Aucun concept"):
            await svc.auto_generate_path(subject.id)

    @pytest.mark.asyncio
    async def test_gap_names_prioritized(self, store, subject, tmp_path):
        """Gap concepts appear earlier in the LLM-ordered response."""
        c1 = store.upsert_concept(subject.id, "Algèbre")
        c2 = store.upsert_concept(subject.id, "Géométrie")

        # Ensure a progress row exists before setting gap flag.
        store.record_progress(c1.id, 0.0)
        store.set_gap_flag(c1.id, True)

        config = _config(tmp_path)
        # LLM puts gap concept first.
        client = _FakeAutoPathClient(["Algèbre", "Géométrie"])
        svc = TutorService(store, client, config)

        result = await svc.auto_generate_path(subject.id)
        step_names = [s["title"] for s in result["steps"]]
        assert step_names[0] == "Algèbre"

    @pytest.mark.asyncio
    async def test_fallback_order_on_bad_llm(self, store, subject, tmp_path):
        """When LLM returns unparseable output, original order is used."""
        store.upsert_concept(subject.id, "Alpha")
        store.upsert_concept(subject.id, "Bêta")

        config = _config(tmp_path)
        client = _FakeBadLLMClient()
        svc = TutorService(store, client, config)

        result = await svc.auto_generate_path(subject.id)
        step_names = [s["title"] for s in result["steps"]]
        # Original concept order preserved as fallback.
        assert step_names == ["Alpha", "Bêta"]

    @pytest.mark.asyncio
    async def test_description_includes_gap_info(self, store, subject, tmp_path):
        """Description mentions gap concepts when gaps exist."""
        c1 = store.upsert_concept(subject.id, "Topologie")
        store.record_progress(c1.id, 0.0)
        store.set_gap_flag(c1.id, True)

        config = _config(tmp_path)
        client = _FakeAutoPathClient(["Topologie"])
        svc = TutorService(store, client, config)

        result = await svc.auto_generate_path(subject.id)
        assert "Topologie" in result["description"]

    @pytest.mark.asyncio
    async def test_path_is_persisted(self, store, subject, tmp_path):
        """Created path is persisted in the store and retrievable."""
        store.upsert_concept(subject.id, "Algèbre")
        store.upsert_concept(subject.id, "Géométrie")

        config = _config(tmp_path)
        client = _FakeAutoPathClient(["Algèbre", "Géométrie"])
        svc = TutorService(store, client, config)

        result = await svc.auto_generate_path(subject.id)
        path_id = result["id"]

        # Verify it's retrievable from the store.
        fetched = store.get_learning_path(path_id)
        assert fetched is not None
        assert fetched.title == result["title"]
        steps = store.list_path_steps(path_id)
        assert len(steps) == 2
