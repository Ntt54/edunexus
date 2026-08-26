"""Tests for US1 — document summary prompt."""
from src.ollama_tutor.tutor.prompts import build_summary_prompt


def test_build_summary_prompt_contains_book_title():
    prompt = build_summary_prompt(["chunk1"], "Mon Livre")
    assert "Mon Livre" in prompt


def test_build_summary_prompt_with_chapter():
    prompt = build_summary_prompt(["chunk1"], "Mon Livre", chapter="Chapitre 3")
    assert "Chapitre 3" in prompt
    assert "chapitre" in prompt.lower()


def test_build_summary_prompt_without_chapter():
    prompt = build_summary_prompt(["chunk1", "chunk2"], "Mon Livre")
    assert "Extrait 1" in prompt
    assert "Extrait 2" in prompt
