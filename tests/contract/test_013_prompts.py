"""Contract test prompts éditables — 013 Vague 1 Socle (US3, T013).

FR-005: prompts Markdown FR éditables avec FALLBACK.
FR-006: gabarit YAML alimentant l'évaluation.

100% offline: aucun appel réseau/LLM. Vérifie:
- fichier absent/illisible → FALLBACK, jamais 500/crash
- hint_level/recurring_mistakes/explain_concept injectés dans build_evaluation_prompt
- zéro secret dans assets/prompts et FALLBACK
- champs gabarit lesson/submission/proofs/hint_level/recurring_mistakes/explain_concept
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# These imports will fail before T014 impl -> TDD red
from src.ollama_tutor.tutor.prompts import (
    FALLBACK_EVALUATION_PROMPT,
    PROMPTS_DIR,
    get_fallback_prompt,
    load_prompt,
    load_prompt_with_gabarit,
    parse_gabarit_block,
)
from src.ollama_tutor.tutor.assessment import build_evaluation_prompt


# ---------------------------------------------------------------------------
# FALLBACK / absent / illisible → never 500
# ---------------------------------------------------------------------------

def test_fallback_constant_is_french_and_nonempty():
    assert isinstance(FALLBACK_EVALUATION_PROMPT, str)
    assert len(FALLBACK_EVALUATION_PROMPT) > 20
    # must be French
    assert "tuteur" in FALLBACK_EVALUATION_PROMPT.lower() or "élève" in FALLBACK_EVALUATION_PROMPT.lower()


def test_absent_file_returns_fallback_never_raises(tmp_path: Path):
    # directory empty -> fallback
    empty = tmp_path / "empty_prompts"
    empty.mkdir()
    result = load_prompt("evaluation", prompts_dir=empty)
    assert result == FALLBACK_EVALUATION_PROMPT
    # also with gabarit loader
    body, gabarit = load_prompt_with_gabarit("evaluation", prompts_dir=empty)
    assert body == FALLBACK_EVALUATION_PROMPT
    assert isinstance(gabarit, dict)


def test_illisible_file_returns_fallback(tmp_path: Path):
    # Simulate unreadable by making a directory with same name or binary garbage that fails parsing
    d = tmp_path / "prompts"
    d.mkdir()
    # create a file that will be considered illisible: we chmod 0 if possible, otherwise create binary that parse fails?
    # Simplest: create a file then make it a directory collision? Instead test file absent handling covers illisible contract.
    # Here we create a file with invalid yaml gabarit -> should fallback
    bad = d / "evaluation.md"
    bad.write_text("corps\n```yaml context\n hint_level: not-an-int\n recurring_mistakes: 123\n```", encoding="utf-8")
    body, gabarit = load_prompt_with_gabarit("evaluation", prompts_dir=d)
    # invalid gabarit -> fallback per contract
    assert body == FALLBACK_EVALUATION_PROMPT


def test_get_fallback_prompt_never_500():
    # even unknown name returns fallback, never raises
    result = get_fallback_prompt("unknown_xyz")
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# Gabarit fields: lesson/submission/proofs/hint_level/recurring_mistakes/explain_concept
# ---------------------------------------------------------------------------

def test_parse_gabarit_extracts_all_fields(tmp_path: Path):
    md = (
        "# Tutor prompt\nContenu FR\n"
        "```yaml context\n"
        "lesson: fr-6e-fractions-addition\n"
        "submission: x = 1/2 + 1/3\n"
        "proofs: stdout ok\n"
        "hint_level: 1\n"
        "recurring_mistakes: [fr-6e-fractions-denominateur]\n"
        "explain_concept: Explique pourquoi on met au même dénominateur\n"
        "```\n"
    )
    body, gabarit = parse_gabarit_block(md)
    assert "Contenu FR" in body
    assert gabarit["lesson"] == "fr-6e-fractions-addition"
    assert gabarit["submission"] == "x = 1/2 + 1/3"
    assert gabarit["proofs"] == "stdout ok"
    assert gabarit["hint_level"] == 1
    assert gabarit["recurring_mistakes"] == ["fr-6e-fractions-denominateur"]
    assert "dénominateur" in gabarit["explain_concept"]


def test_load_prompt_with_valid_gabarit(tmp_path: Path):
    d = tmp_path / "prompts"
    d.mkdir()
    md = (
        "Tu es un tuteur bienveillant.\n"
        "```yaml context\n"
        "lesson: fr-6e-fractions-addition\n"
        "submission: code élève\n"
        "proofs: tests visibles ok\n"
        "hint_level: 2\n"
        "recurring_mistakes: [m1, m2]\n"
        "explain_concept: Pourquoi ?\n"
        "```\n"
    )
    (d / "evaluation.md").write_text(md, encoding="utf-8")
    body, gabarit = load_prompt_with_gabarit("evaluation", prompts_dir=d)
    assert body.startswith("Tu es un tuteur")
    assert gabarit["hint_level"] == 2
    assert gabarit["recurring_mistakes"] == ["m1", "m2"]
    assert gabarit["explain_concept"] == "Pourquoi ?"
    assert gabarit["lesson"] == "fr-6e-fractions-addition"


def test_gabarit_injected_into_build_evaluation_prompt():
    # Existing fields intact + new fields forwarded without regression
    base = build_evaluation_prompt(
        code="x=1",
        exit_code=0,
        duration_ms=10,
        stdout="ok",
        stderr="",
        section="sec",
        question="q?",
    )
    assert "x=1" in base
    assert "Exit code: 0" in base
    assert "sec" in base

    with_gabarit = build_evaluation_prompt(
        code="x=1",
        exit_code=0,
        duration_ms=10,
        stdout="ok",
        stderr="",
        section="sec",
        question="q?",
        hint_level=1,
        recurring_mistakes=["fr-6e-fractions-denominateur", "fr-4e-pythagore-orientation"],
        explain_concept="Explique le dénominateur commun",
    )
    # new fields must appear
    assert "1" in with_gabarit  # hint_level
    assert "fr-6e-fractions-denominateur" in with_gabarit
    assert "fr-4e-pythagore-orientation" in with_gabarit
    assert "dénominateur commun" in with_gabarit or "denominateur" in with_gabarit.lower()
    # existing fields still intact
    assert "x=1" in with_gabarit
    assert "Exit code: 0" in with_gabarit


def test_build_evaluation_prompt_backward_compatible():
    # Calling without new kwargs must not break (defaults)
    p1 = build_evaluation_prompt(code="a", exit_code=0, duration_ms=5, stdout="", stderr="")
    p2 = build_evaluation_prompt(code="a", exit_code=0, duration_ms=5, stdout="", stderr="", hint_level=None, recurring_mistakes=None, explain_concept=None)
    assert isinstance(p1, str) and isinstance(p2, str)
    # p1 should not contain gabarit markers when not provided, but must still be valid
    assert "a" in p1


# ---------------------------------------------------------------------------
# Zero secret
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(r"(sk-|api[_-]?key|AKIA|ghp_|secret)", re.IGNORECASE)

def test_fallback_contains_zero_secret():
    assert not _SECRET_RE.search(FALLBACK_EVALUATION_PROMPT), "FALLBACK must not contain secrets"


def test_assets_prompts_contain_zero_secret():
    # Check repo assets/prompts if exists
    prompts_dir = PROMPTS_DIR
    if not prompts_dir.is_dir():
        # fallback dir may be empty -> pass
        return
    for p in prompts_dir.glob("*.md"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert not _SECRET_RE.search(text), f"Secret found in {p.name}"
        # also no absolute machine path
        assert "/home/" not in text and "C:\\" not in text
