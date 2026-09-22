"""Integration test parcours-prérequis + gating — 013 Vague 1 Socle (US1, T006).

FR-001→FR-003 via ``TutorService`` (wiring T008) : le parcours exige le
prérequis d'abord, le diagnostic cite un piège classique (taxonomie 5-cats
unifiée), juste-sans-explication = partiel quand ``explain_concept`` est
exigé. 100 % offline : leçons copiées sous tmp, aucun appel réseau/LLM.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.assessment import ERROR_CATEGORIES
from src.ollama_tutor.tutor.store import LibraryStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lessons013"


def _svc(tmp_path: Path):
    from src.ollama_tutor.tutor.service import TutorService

    store = LibraryStore(tmp_path / "store")
    return TutorService(store, None, Config(config_dir=tmp_path / "config"))


def _lessons_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "lessons013"
    dest.mkdir()
    base = json.loads((FIXTURES / "valid-fractions-6e.json").read_text(encoding="utf-8"))
    # A : sans prérequis. B : exige A (parcours-prérequis US1).
    a = dict(base, id="fr-6e-fractions-bases", title="Bases des fractions (6e)",
             prerequisites=[])
    b = dict(base, id="fr-6e-fractions-addition", title="Additionner (6e)",
             prerequisites=["fr-6e-fractions-bases"])
    (dest / "a.json").write_text(json.dumps(a), encoding="utf-8")
    (dest / "b.json").write_text(json.dumps(b), encoding="utf-8")
    shutil.copy(FIXTURES / "invalid-pythagore-4e.json", dest / "c-invalide.json")
    return dest


def test_path_requires_prerequisite_first(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    ordered = svc.build_lesson_path(
        "fr-6e-fractions-addition", lessons_dir=_lessons_dir(tmp_path)
    )
    ids = [step["id"] for step in ordered]
    assert ids.index("fr-6e-fractions-bases") < ids.index("fr-6e-fractions-addition")
    assert ordered[-1]["id"] == "fr-6e-fractions-addition"


def test_explain_concept_gating_partial_without_explanation(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    lessons = _lessons_dir(tmp_path)
    # Juste + expliqué = maîtrisé (mastery.explain_concept=true sur la fixture).
    full = svc.grade_lesson_exercise(
        "fr-6e-fractions-addition", tests_passed=True,
        explanation_provided=True, lessons_dir=lessons,
    )
    assert full["status"] == "mastered"
    # Juste SANS explication = partiel (résoudre seul ≠ expliquer/transférer).
    partial = svc.grade_lesson_exercise(
        "fr-6e-fractions-addition", tests_passed=True,
        explanation_provided=False, lessons_dir=lessons,
    )
    assert partial["status"] == "partial"
    assert "expliqu" in partial["feedback"].lower()
    # Tests échoués = non maîtrisé même avec explication.
    failed = svc.grade_lesson_exercise(
        "fr-6e-fractions-addition", tests_passed=False,
        explanation_provided=True, lessons_dir=lessons,
    )
    assert failed["status"] == "failed"


def test_diagnostic_cites_unified_common_mistake(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    diag = svc.diagnose_lesson_error(
        "fr-6e-fractions-addition",
        question="Calculer 1/3 + 2/3.",
        correct_answer="1/1",
        given_answer="3/6",
        lessons_dir=_lessons_dir(tmp_path),
    )
    assert diag["category"] in ERROR_CATEGORIES
    assert diag["lesson_mistakes"], "le diagnostic doit citer un piège de la leçon"
    cited = " ".join(
        m["description"] for m in diag["lesson_mistakes"]
    )
    assert "dénominateur" in cited
    for mistake in diag["lesson_mistakes"]:
        assert mistake["category"] in ERROR_CATEGORIES
