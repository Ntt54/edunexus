"""Tests for US2 — revision sheet prompt."""
from src.ollama_tutor.tutor.prompts import build_revision_sheet_prompt


def test_build_revision_sheet_prompt_contains_subject():
    prompt = build_revision_sheet_prompt(["chunk1"], "Mathématiques")
    assert "Mathématiques" in prompt


def test_build_revision_sheet_prompt_beginner():
    prompt = build_revision_sheet_prompt(["chunk1"], "Physique", level="beginner")
    assert "simple" in prompt.lower() or "accessible" in prompt.lower()


def test_build_revision_sheet_prompt_advanced():
    prompt = build_revision_sheet_prompt(["chunk1"], "Physique", level="advanced")
    assert "détaillé" in prompt.lower() or "technique" in prompt.lower()
