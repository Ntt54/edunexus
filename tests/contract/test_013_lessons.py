"""Contract test lesson load — 013 Vague 1 Socle (US1, T005).

FR-001 : leçons JSON strictes (schéma ``id/prérequis/concepts/exercice
visible+hidden/mastery``), validées en stdlib ; invalide = log-and-skip,
doublon = warning + premier gagnant. FR-002 : pièges classiques unifiés à
la taxonomie diagnostic 5-catégories existante (pas de taxonomie parallèle).

100 % offline : fixtures ``tests/fixtures/lessons013/`` + répertoire
canonique ``tutor/data/lessons/`` (Phase A), ``errors.log`` sous tmp
``config_dir``. Aucun appel réseau/LLM.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.ollama_tutor.tutor.assessment import ERROR_CATEGORIES
from src.ollama_tutor.tutor.curriculum import (
    LESSON_CATEGORIES,
    load_canonical_lessons,
    load_lessons,
    validate_lesson,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lessons013"
VALID = FIXTURES / "valid-fractions-6e.json"
INVALID = FIXTURES / "invalid-pythagore-4e.json"
DUPLICATE = FIXTURES / "duplicate-fractions-6e.json"

# L'invalide Phase A cumule exactement 3 causes (schéma lesson.schema.json) :
# 1. exercise.hidden vide (minItems 1)  2. mastery.explain_concept manquant
# 3. common_mistakes[0].category "orthograf" hors enum 5-cats.
EXPECTED_CAUSES = ("hidden", "explain_concept", "orthograf")

UNIFIED_5_CATS = ("conceptual", "procedural", "computational", "reading", "careless")


def _copy(tmp_path: Path, *names: str) -> Path:
    dest = tmp_path / "lessons"
    dest.mkdir()
    for i, name in enumerate(names):
        # Préfixe numérique : ordre de chargement déterministe (premier gagnant).
        shutil.copy(FIXTURES / name, dest / f"{i:02d}-{name}")
    return dest


class _Cfg:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir


def test_valid_lesson_loads_with_full_envelope(tmp_path: Path) -> None:
    lessons, report = load_lessons(
        _copy(tmp_path, "valid-fractions-6e.json"), config=_Cfg(tmp_path)
    )
    assert report.skipped == 0
    assert len(lessons) == 1
    lesson = lessons["fr-6e-fractions-addition"]
    assert lesson["title"].startswith("Additionner deux fractions")
    assert lesson["prerequisites"] == []
    assert len(lesson["concepts"]) >= 1
    assert 1 <= len(lesson["exercise"]["visible"]) <= 3
    assert len(lesson["exercise"]["hidden"]) >= 1
    assert set(lesson["mastery"]) == {"pass_hidden_tests", "explain_concept"}
    assert len(lesson["common_mistakes"]) >= 1


def test_invalid_lesson_skipped_and_log_cites_3_causes(tmp_path: Path) -> None:
    dest = _copy(tmp_path, "valid-fractions-6e.json", "invalid-pythagore-4e.json")
    lessons, report = load_lessons(dest, config=_Cfg(tmp_path))
    # Seule la valide est servie ; l'invalide est skippée avec 3 causes.
    assert list(lessons) == ["fr-6e-fractions-addition"]
    assert report.skipped == 1
    assert len(report.errors) == 1
    causes = " ".join(report.errors[0].causes)
    for expected in EXPECTED_CAUSES:
        assert expected in causes, f"cause {expected!r} absente de {causes!r}"
    log = (tmp_path / "errors.log").read_text(encoding="utf-8")
    for expected in EXPECTED_CAUSES:
        assert expected in log, f"cause {expected!r} absente de errors.log"


def test_validate_lesson_reports_3_causes_for_invalid_fixture() -> None:
    data = json.loads(INVALID.read_text(encoding="utf-8"))
    causes = validate_lesson(data)
    assert len(causes) >= 3
    joined = " ".join(causes)
    for expected in EXPECTED_CAUSES:
        assert expected in joined


def test_duplicate_id_warns_and_first_wins(tmp_path: Path) -> None:
    dest = _copy(
        tmp_path, "valid-fractions-6e.json", "duplicate-fractions-6e.json"
    )
    lessons, report = load_lessons(dest, config=_Cfg(tmp_path))
    assert list(lessons) == ["fr-6e-fractions-addition"]
    # La copie divergente ne doit jamais être servie (premier gagnant).
    assert "COPIE" not in lessons["fr-6e-fractions-addition"]["title"]
    assert report.duplicates == ["fr-6e-fractions-addition"]
    log = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "doublon" in log.lower() or "duplicate" in log.lower()


def test_common_mistakes_use_unified_5cat_taxonomy(tmp_path: Path) -> None:
    # Pas de taxonomie parallèle : les catégories leçons == diagnostic existant.
    assert tuple(LESSON_CATEGORIES) == tuple(ERROR_CATEGORIES) == UNIFIED_5_CATS
    lessons, _ = load_lessons(
        _copy(tmp_path, "valid-fractions-6e.json"), config=_Cfg(tmp_path)
    )
    for mistake in lessons["fr-6e-fractions-addition"]["common_mistakes"]:
        assert mistake["category"] in ERROR_CATEGORIES


def test_canonical_package_dir_loads_without_data_dir(tmp_path: Path, monkeypatch) -> None:
    # Source canonique = package tutor/data/lessons (JAMAIS EDUNEXUS_DATA_DIR).
    monkeypatch.delenv("EDUNEXUS_DATA_DIR", raising=False)
    lessons, report = load_canonical_lessons(config=_Cfg(tmp_path))
    assert "fr-6e-fractions-addition" in lessons
    assert report.skipped == 0
