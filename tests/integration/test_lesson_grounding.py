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
from src.ollama_tutor.tutor.service import (
    TutorService,
    _build_lesson_prompts,
    _notion_wants_code,
)
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


# ---------------------------------------------------------------------------
# RAG off (excerpts == []) : le modèle ENSEIGNE, jamais de contenu creux.
# Constat prod : avec le wording ancré (« fidèle aux extraits fournis » +
# _LESSON_GROUNDING_RULES) mais sans extraits, le modèle écrivait « Selon
# les extraits, aucune définition n'est fournie… » au lieu d'enseigner.
# ---------------------------------------------------------------------------


def _prompts_empty(kind: str):
    return _build_lesson_prompts(kind, "les boucles", [], question="Comment ?")


@pytest.mark.parametrize("kind", KINDS)
def test_no_excerpt_never_mentions_excerpts_or_sources(kind: str) -> None:
    system, user = _prompts_empty(kind)
    combined = (system + "\n" + user).lower()
    assert "extrait" not in combined
    assert "source" not in combined


@pytest.mark.parametrize("kind", KINDS)
def test_no_excerpt_teaches_from_own_knowledge_in_french(kind: str) -> None:
    system, user = _prompts_empty(kind)
    combined = (system + "\n" + user).lower()
    assert "connaissances" in combined  # directive d'enseignement présente
    assert "enseigne" in combined
    assert "français" in combined


@pytest.mark.parametrize("kind", KINDS)
def test_no_excerpt_no_grounding_rules_but_keeps_id_ban(kind: str) -> None:
    system, _ = _prompts_empty(kind)
    low = system.lower()
    assert "selon les extraits" not in low
    assert "que ce qui figure" not in system
    assert "identifiants techniques" in system  # interdiction conservée


@pytest.mark.parametrize("kind", KINDS)
def test_with_excerpts_grounding_wording_unchanged(kind: str) -> None:
    system, user = _prompts(kind, question="Comment assigner ?")
    assert "extraits fournis" in system + "\n" + user
    assert "selon les extraits" in system.lower()


def test_no_excerpt_whitespace_only_treated_as_empty() -> None:
    system, user = _build_lesson_prompts("lesson_summary", "X", ["   ", ""])
    combined = (system + "\n" + user).lower()
    assert "extrait" not in combined
    assert "source" not in combined


def test_no_excerpt_computing_course_keeps_code_section() -> None:
    system, user = _build_lesson_prompts(
        "lesson_course", "les boucles en Python", []
    )
    for marker in (
        "Objectif du cours", "numérotées", "1.", "1.1", "```python",
        "bibliothèques standard", "Points clés", "Cas d'usage concrets",
        "Cas 1", "Erreurs fréquentes", "❌", "Conclusion", "Mots-clés",
        "800", "1200", "Mot total", "méta",
    ):
        assert marker in system, marker
    assert "800" in user and "1200" in user


def test_no_excerpt_literary_course_has_no_code_section() -> None:
    system, user = _build_lesson_prompts(
        "lesson_course", "la Révolution française", []
    )
    assert "```" not in system
    assert "```" not in user
    assert "sans code" in system  # exemples rédigés pas-à-pas
    for marker in (
        "Objectif du cours", "numérotées", "Points clés",
        "Cas d'usage concrets", "Erreurs fréquentes", "❌",
        "Conclusion", "Mots-clés", "800", "1200",
    ):
        assert marker in system, marker


def test_with_excerpts_literary_course_has_no_code_section() -> None:
    excerpts = [
        "En 1789, les états généraux s'ouvrent à Versailles.",
        "La prise de la Bastille a lieu le 14 juillet 1789.",
    ]
    system, user = _build_lesson_prompts(
        "lesson_course", "la Révolution française", excerpts
    )
    assert "```" not in system
    assert "```" not in user
    assert "sans code" in system


def test_with_excerpts_computing_course_keeps_code_section() -> None:
    system, user = _build_lesson_prompts(
        "lesson_course", "l'affectation en Python", EXCERPTS
    )
    assert "```python" in system


@pytest.mark.parametrize(
    "notion",
    [
        "l'informatique au collège",
        "la programmation orientée objet",
        "les boucles en Python",
        "les algorithmes de tri",
        "les réseaux informatiques",
        "l'administration système",
        "les bases de données relationnelles",
        "le développement logiciel",
        "LES RESEAUX",  # insensible à la casse
    ],
)
def test_notion_wants_code_computing_notions(notion: str) -> None:
    assert _notion_wants_code(notion, []) is True


@pytest.mark.parametrize(
    "notion",
    [
        "la Révolution française",
        "le romantisme en poésie",
        "la photosynthèse",
        "les boucles",  # ambigu sans marqueur info : pas de code par défaut
        "",
    ],
)
def test_notion_wants_code_literary_or_unknown_notions(notion: str) -> None:
    assert _notion_wants_code(notion, []) is False


def test_notion_wants_code_fences_in_excerpts_force_code() -> None:
    assert (
        _notion_wants_code(
            "la Révolution française", ["voir ```python\nx = 1\n```"]
        )
        is True
    )


def test_notion_wants_code_keywords_in_excerpts_force_code() -> None:
    assert (
        _notion_wants_code("les boucles", ["En Python, x = 3."]) is True
    )
    assert (
        _notion_wants_code("les boucles", ["exemple : def f(): ..."])
        is True
    )


@pytest.mark.parametrize("excerpts", [[], ["La prise de la Bastille en 1789."]])
def test_code_examples_override(excerpts: list) -> None:
    forced, _ = _build_lesson_prompts(
        "lesson_course", "la Révolution française", excerpts,
        code_examples=True,
    )
    assert "```python" in forced
    suppressed, _ = _build_lesson_prompts(
        "lesson_course", "les boucles en Python", excerpts or EXCERPTS,
        code_examples=False,
    )
    assert "```" not in suppressed
    assert "sans code" in suppressed


def test_no_excerpt_summary_keeps_length() -> None:
    system, user = _build_lesson_prompts("lesson_summary", "les boucles", [])
    assert "150" in system and "250" in system
    assert "150" in user and "250" in user


def test_no_excerpt_answer_keeps_question_and_length() -> None:
    system, user = _build_lesson_prompts(
        "lesson_answer", "les boucles", [], question="Comment itérer ?"
    )
    assert "100" in system and "200" in system
    assert "Comment itérer ?" in user
    assert "les boucles" in user
    assert "100" in user and "200" in user
