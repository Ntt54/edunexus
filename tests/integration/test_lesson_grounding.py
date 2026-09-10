"""Ancrage anti-hallucination des prompts de leçon (petit LLM local).

Constat prod : termes inventés (« langage multiparamétrique »), faussetés
(`:=` seule façon d'assigner, tuple = « liste imboute », `object` non
initialisé), exemples génériques non ancrés.

100 % offline, sans appel réseau : asserts structurels sur le texte des
3 prompts (`lesson_course`/`lesson_summary`/`lesson_answer`) + e2e mocké
via `lesson_http_transport` injectable (on inspecte les consignes
envoyées, jamais un jugement LLM).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import TutorService, _build_lesson_prompts
from src.ollama_tutor.tutor.store import LibraryStore


KINDS = ("lesson_course", "lesson_summary", "lesson_answer")

EXCERPTS = [
    "En Python, l'affectation se fait avec le signe = : x = 3.",
    "Un tuple est une séquence immuable, par exemple (1, 2).",
]

# Termes hallucinés vus en prod : ne doivent figurer dans AUCUNE consigne.
INVENTED_TERMS = ("multiparamétrique", "imboute")


def _prompts(kind: str, question: str | None = None):
    system, user = _build_lesson_prompts(
        kind, "affectation en Python", EXCERPTS, question=question
    )
    return system, user


@pytest.mark.parametrize("kind", KINDS)
def test_grounding_rule_affirm_only_excerpts(kind: str) -> None:
    system, user = _prompts(kind, question="Comment assigner ?")
    combined = system + "\n" + user
    assert "que ce qui figure" in combined and "extraits" in combined


@pytest.mark.parametrize("kind", KINDS)
def test_grounding_rule_code_examples_only_from_excerpts(kind: str) -> None:
    system, _ = _prompts(kind, question="Un exemple ?")
    low = system.lower()
    assert "exemple" in low and "extrait" in low
    assert "invent" in low  # jamais d'API/fonction inventée


@pytest.mark.parametrize("kind", KINDS)
def test_grounding_rule_definitions_reuse_terminology(kind: str) -> None:
    system, _ = _prompts(kind)
    low = system.lower()
    assert "terminologie" in low
    assert "terme technique" in low or "termes techniques" in low


@pytest.mark.parametrize("kind", KINDS)
def test_grounding_rule_hedge_uncertainty(kind: str) -> None:
    system, _ = _prompts(kind)
    assert "selon les extraits" in system.lower()


@pytest.mark.parametrize("kind", KINDS)
def test_grounding_rule_strict_french(kind: str) -> None:
    system, _ = _prompts(kind)
    assert "français" in system.lower()


@pytest.mark.parametrize("kind", KINDS)
def test_no_invented_terms_in_instructions(kind: str) -> None:
    system, _ = _prompts(kind, question="Question ?")
    low = system.lower()
    for term in INVENTED_TERMS:
        assert term not in low


@pytest.mark.parametrize("kind", KINDS)
def test_excerpts_reproduced_verbatim_in_user_prompt(kind: str) -> None:
    _, user = _prompts(kind, question="Question ?")
    for excerpt in EXCERPTS:
        assert excerpt in user


def _service(tmp_path: Path) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    return TutorService(store, None, config)


def test_e2e_mocked_sends_grounded_instructions(tmp_path: Path) -> None:
    """Le payload HTTP envoyé au LLM porte les 5 contraintes d'ancrage."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": "Cours généré honnête."}},
            request=request,
        )

    svc = _service(tmp_path)
    svc.lesson_http_transport = httpx.MockTransport(handler)
    text = svc.generate_lesson_text("lesson_course", "affectation", EXCERPTS)
    assert text == "Cours généré honnête."
    system = seen["body"]["messages"][0]["content"].lower()
    user = seen["body"]["messages"][1]["content"]
    assert "que ce qui figure" in system
    assert "invent" in system
    assert "terminologie" in system
    assert "selon les extraits" in system
    assert "français" in system
    for excerpt in EXCERPTS:
        assert excerpt in user
    for term in INVENTED_TERMS:
        assert term not in system
