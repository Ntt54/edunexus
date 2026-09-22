"""Chargeur tolérant des leçons curriculum JSON (013 Vague 1, US1, FR-001).

Research D1 : schéma strict ``id/title/prérequis/concepts/exercice
{prompt, starter, visible, hidden}/mastery{pass_hidden_tests +
explain_concept}`` + ``common_mistakes[]``, validé en stdlib (``json``)
sous ``tutor/data/lessons/*.json`` versionnés ; fichier invalide = log
``errors.log`` + skip, doublon d'id = warning + premier gagnant.

Research D2 (gate G1) : les ``common_mistakes`` sont unifiées à la
taxonomie du diagnostic 5-catégories existant — ``LESSON_CATEGORIES``
est un ALIAS de ``assessment.ERROR_CATEGORIES``, jamais une seconde
taxonomie.

Source canonique : le package ``tutor/data/lessons/`` (plan.md) — JAMAIS
``EDUNEXUS_DATA_DIR`` (réservé aux données utilisateur).

UI-framework-free by contract (ni fastapi ni textual) ; stdlib uniquement.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .assessment import ERROR_CATEGORIES, diagnose_error

logger = logging.getLogger(__name__)

#: Taxonomie unifiée (D2) : alias, pas de duplication.
LESSON_CATEGORIES = ERROR_CATEGORIES

#: Source canonique des leçons livrées (versionnées, lecture seule).
CANONICAL_LESSONS_DIR = Path(__file__).resolve().parent / "data" / "lessons"

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_SCHEMA_VERSION = "1.0"

_TOP_REQUIRED = (
    "schemaVersion",
    "id",
    "title",
    "prerequisites",
    "concepts",
    "exercise",
    "mastery",
    "common_mistakes",
)


@dataclass
class LessonLoadError:
    """Un fichier ignoré + toutes ses causes (le log les cite toutes)."""

    file: str
    causes: list[str] = field(default_factory=list)


@dataclass
class LessonLoadReport:
    """Bilan d'un chargement : servis, skippés, doublons, erreurs."""

    loaded: list[str] = field(default_factory=list)
    skipped: int = 0
    duplicates: list[str] = field(default_factory=list)
    errors: list[LessonLoadError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "loaded": list(self.loaded),
            "skipped": self.skipped,
            "duplicates": list(self.duplicates),
            "errors": [
                {"file": e.file, "causes": list(e.causes)} for e in self.errors
            ],
        }


def _is_slug(value: Any) -> bool:
    return isinstance(value, str) and bool(_SLUG_RE.match(value))


def validate_lesson(data: Any) -> list[str]:
    """Valide une leçon (miroir stdlib de ``lesson.schema.json``).

    Retourne la liste des causes (vide = valide). Chaque cause cite le
    chemin du champ fautif pour être assertable dans ``errors.log``.
    """
    causes: list[str] = []
    if not isinstance(data, dict):
        return ["lesson: objet JSON attendu (dict), pas de leçon servie"]
    for key in data:
        if key not in _TOP_REQUIRED:
            causes.append(f"{key}: propriété inconnue (schéma strict 1.0)")
    for key in _TOP_REQUIRED:
        if key not in data:
            causes.append(f"{key}: champ requis manquant")
    if causes and any("requis manquant" in c for c in causes):
        # Enveloppe incomplète : on continue pour citer TOUTES les causes.
        pass
    if data.get("schemaVersion") != _SCHEMA_VERSION:
        causes.append(
            f"schemaVersion: {data.get('schemaVersion')!r} invalide "
            f"(attendu {_SCHEMA_VERSION!r})"
        )
    lesson_id = data.get("id")
    if not _is_slug(lesson_id):
        causes.append(
            f"id: {lesson_id!r} invalide (slug requis "
            "'^[a-z0-9]+(-[a-z0-9]+)*$', ex. fr-6e-fractions-addition)"
        )
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        causes.append("title: titre non vide requis")
    prereqs = data.get("prerequisites")
    if not isinstance(prereqs, list):
        causes.append("prerequisites: liste d'ids slug requise (vide = aucune)")
    elif any(not _is_slug(p) for p in prereqs):
        bad = [p for p in prereqs if not _is_slug(p)]
        causes.append(
            f"prerequisites: ids slug invalides {bad!r} "
            "(même format que `id`, parcours-prérequis US1)"
        )
    concepts = data.get("concepts")
    if (
        not isinstance(concepts, list)
        or not concepts
        or any(not isinstance(c, str) or not c.strip() for c in concepts)
    ):
        causes.append("concepts: liste d'au moins 1 concept non vide requise")
    causes.extend(_validate_exercise(data.get("exercise")))
    causes.extend(_validate_mastery(data.get("mastery")))
    causes.extend(_validate_mistakes(data.get("common_mistakes")))
    return causes


def _validate_exercise(exercise: Any) -> list[str]:
    causes: list[str] = []
    if not isinstance(exercise, dict):
        return ["exercise: objet {prompt, starter, visible, hidden} requis"]
    for key in ("prompt", "starter", "visible", "hidden"):
        if key not in exercise:
            causes.append(f"exercise.{key}: champ requis manquant")
    for key in exercise:
        if key not in ("prompt", "starter", "visible", "hidden"):
            causes.append(f"exercise.{key}: propriété inconnue (schéma strict)")
    prompt = exercise.get("prompt")
    if "prompt" in exercise and (not isinstance(prompt, str) or not prompt.strip()):
        causes.append("exercise.prompt: énoncé non vide requis")
    if "starter" in exercise and not isinstance(exercise.get("starter"), str):
        causes.append("exercise.starter: chaîne requise")
    visible = exercise.get("visible")
    if "visible" in exercise:
        if not isinstance(visible, list) or not (1 <= len(visible) <= 3):
            causes.append(
                f"exercise.visible: 1 à 3 tests visibles requis "
                f"(reçu {len(visible) if isinstance(visible, list) else visible!r})"
            )
        else:
            for i, case in enumerate(visible):
                causes.extend(_validate_case(f"exercise.visible[{i}]", case, hidden=False))
    hidden = exercise.get("hidden")
    if "hidden" in exercise:
        if not isinstance(hidden, list) or len(hidden) < 1:
            causes.append(
                "exercise.hidden: au moins 1 test caché requis "
                "(avec failure_hint hint-first, jamais la solution)"
            )
        else:
            for i, case in enumerate(hidden):
                causes.extend(_validate_case(f"exercise.hidden[{i}]", case, hidden=True))
    return causes


def _validate_case(path: str, case: Any, hidden: bool) -> list[str]:
    if not isinstance(case, dict):
        return [f"{path}: objet {{id, input, expected}} requis"]
    causes: list[str] = []
    required = ("id", "input", "expected", "failure_hint") if hidden else ("id", "input", "expected")
    for key in required:
        if key not in case:
            causes.append(f"{path}.{key}: champ requis manquant")
    for key in case:
        if key not in required:
            causes.append(f"{path}.{key}: propriété inconnue (schéma strict)")
    for key in ("id", "expected"):
        if key in case and (not isinstance(case[key], str) or not case[key].strip()):
            causes.append(f"{path}.{key}: chaîne non vide requise")
    if hidden and "failure_hint" in case and (
        not isinstance(case["failure_hint"], str) or not case["failure_hint"].strip()
    ):
        causes.append(f"{path}.failure_hint: raison d'échec / indice requis")
    return causes


def _validate_mastery(mastery: Any) -> list[str]:
    if not isinstance(mastery, dict):
        return ["mastery: objet {pass_hidden_tests, explain_concept} requis"]
    causes: list[str] = []
    for key in ("pass_hidden_tests", "explain_concept"):
        if key not in mastery:
            causes.append(
                f"mastery.{key}: champ requis manquant "
                "(explain_concept=true ⇒ juste-sans-explication = partiel)"
            )
        elif not isinstance(mastery[key], bool):
            causes.append(f"mastery.{key}: booléen requis")
    for key in mastery:
        if key not in ("pass_hidden_tests", "explain_concept"):
            causes.append(f"mastery.{key}: propriété inconnue (schéma strict)")
    return causes


def _validate_mistakes(mistakes: Any) -> list[str]:
    if not isinstance(mistakes, list) or not mistakes:
        return ["common_mistakes: au moins 1 piège classique rédigé requis"]
    causes: list[str] = []
    for i, mistake in enumerate(mistakes):
        path = f"common_mistakes[{i}]"
        if not isinstance(mistake, dict):
            causes.append(f"{path}: objet {{id, description, category}} requis")
            continue
        for key in ("id", "description", "category"):
            if key not in mistake:
                causes.append(f"{path}.{key}: champ requis manquant")
        for key in mistake:
            if key not in ("id", "description", "category"):
                causes.append(f"{path}.{key}: propriété inconnue (schéma strict)")
        for key in ("id", "description"):
            if key in mistake and (
                not isinstance(mistake[key], str) or not mistake[key].strip()
            ):
                causes.append(f"{path}.{key}: chaîne non vide requise")
        category = mistake.get("category")
        if "category" in mistake and category not in LESSON_CATEGORIES:
            causes.append(
                f"{path}.category: {category!r} n'est pas une catégorie valide "
                f"(taxonomie unifiée 5-cats : {', '.join(LESSON_CATEGORIES)})"
            )
    return causes


def _append_errors_log(config: Any, message: str) -> None:
    """Log best-effort vers ``errors.log`` (principe VI, jamais de crash)."""
    try:
        line = f"[{datetime.now().astimezone().isoformat()}] [curriculum] {message}"
        log_file = Path(config.config_dir) / "errors.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _log(config: Any, message: str) -> None:
    logger.warning("curriculum: %s", message)
    if config is not None:
        _append_errors_log(config, message)


def load_lessons(
    directory: str | Path, config: Any = None
) -> tuple[dict[str, dict[str, Any]], LessonLoadReport]:
    """Charge les leçons ``*.json`` d'un répertoire (log-and-skip).

    - Fichier illisible / JSON invalide / leçon invalide → skip + log
      ``errors.log`` citant TOUTES les causes (jamais de crash).
    - Doublon d'id → warning + premier gagnant (ordre alphabétique stable).
    Retourne ``(leçons_par_id, rapport)``.
    """
    lessons: dict[str, dict[str, Any]] = {}
    report = LessonLoadReport()
    directory = Path(directory)
    # `*.schema.json` = documents de schéma (Phase A), pas des LessonFile.
    files = (
        sorted(
            p
            for p in directory.glob("*.json")
            if not p.name.endswith(".schema.json")
        )
        if directory.is_dir()
        else []
    )
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.skipped += 1
            err = LessonLoadError(file=path.name, causes=[f"lecture: {exc}"])
            report.errors.append(err)
            _log(config, f"{path.name} ignoré — lecture impossible : {exc}")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            report.skipped += 1
            err = LessonLoadError(file=path.name, causes=[f"JSON invalide: {exc}"])
            report.errors.append(err)
            _log(config, f"{path.name} ignoré — JSON invalide : {exc}")
            continue
        causes = validate_lesson(data)
        if causes:
            report.skipped += 1
            report.errors.append(LessonLoadError(file=path.name, causes=causes))
            _log(
                config,
                f"{path.name} ignoré — leçon invalide "
                f"({len(causes)} cause(s)) : {' ; '.join(causes)}",
            )
            continue
        lesson_id = data["id"]
        if lesson_id in lessons:
            report.duplicates.append(lesson_id)
            _log(
                config,
                f"{path.name} ignoré — doublon d'id {lesson_id!r} "
                "(premier fichier gagnant, jamais de doublon silencieux)",
            )
            continue
        lessons[lesson_id] = data
        report.loaded.append(lesson_id)
    return lessons, report


def load_canonical_lessons(
    config: Any = None,
) -> tuple[dict[str, dict[str, Any]], LessonLoadReport]:
    """Charge les leçons livrées du package (JAMAIS ``EDUNEXUS_DATA_DIR``)."""
    return load_lessons(CANONICAL_LESSONS_DIR, config)


def get_lesson(
    lessons: dict[str, dict[str, Any]], lesson_id: str
) -> dict[str, Any]:
    """Retourne une leçon chargée (``KeyError`` si inconnue)."""
    try:
        return lessons[lesson_id]
    except KeyError:
        raise KeyError(f"Leçon inconnue : {lesson_id}") from None


def order_lessons_for_path(
    lessons: dict[str, dict[str, Any]], target_id: str
) -> list[dict[str, Any]]:
    """Ordonne une leçon cible après ses prérequis (US1, parcours-prérequis).

    DFS sur ``prerequisites[]`` : les prérequis viennent d'abord, la cible
    en dernier. Prérequis absents du lot = ignorés (pas de crash) ;
    cycles tolérés (nœud déjà vu = sauté). ``KeyError`` si cible inconnue.
    """
    get_lesson(lessons, target_id)
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _visit(lesson_id: str) -> None:
        if lesson_id in seen or lesson_id not in lessons:
            return
        seen.add(lesson_id)
        for prereq in lessons[lesson_id].get("prerequisites", []):
            _visit(prereq)
        ordered.append(lessons[lesson_id])

    _visit(target_id)
    return ordered


def evaluate_mastery(
    lesson: dict[str, Any], tests_passed: bool, explanation_provided: bool
) -> dict[str, Any]:
    """Gating de maîtrise FR-003 (pur, sans LLM ni store).

    - tests échoués → ``failed`` (même avec explication).
    - ``mastery.explain_concept=true`` et juste-sans-explication → ``partial``
      (résoudre seul ≠ expliquer/transférer).
    - sinon → ``mastered``.
    """
    mastery = lesson.get("mastery", {}) or {}
    requires_explain = bool(mastery.get("explain_concept", False))
    lesson_id = lesson.get("id", "?")
    if not tests_passed:
        return {
            "lesson_id": lesson_id,
            "status": "failed",
            "feedback": (
                "Les tests cachés ne passent pas encore : reprends les indices "
                "hint-first de la leçon avant de viser la maîtrise."
            ),
            "requires_explanation": requires_explain,
        }
    if requires_explain and not explanation_provided:
        return {
            "lesson_id": lesson_id,
            "status": "partial",
            "feedback": (
                "Tests réussis, mais la maîtrise exige d'expliquer la notion "
                "avec tes mots (explain_concept) : que se passe-t-il et pourquoi ?"
            ),
            "requires_explanation": True,
        }
    return {
        "lesson_id": lesson_id,
        "status": "mastered",
        "feedback": "Tests réussis et notion expliquée : maîtrise complète.",
        "requires_explanation": requires_explain,
    }


def diagnose_with_lesson_mistakes(
    lesson: dict[str, Any],
    question: str,
    correct_answer: str,
    given_answer: str,
    related_concept: str = "",
) -> dict[str, Any]:
    """Diagnostique une erreur via le moteur 5-cats + pièges de la leçon (D2).

    Réutilise :func:`assessment.diagnose_error` (aucune seconde taxonomie) ;
    joint les ``common_mistakes`` de la leçon comme evidence, même catégorie
    d'abord, pour que le feedback cite le piège classique pertinent.
    """
    concept = related_concept or (lesson.get("concepts") or ["unknown"])[0]
    diag = diagnose_error(question, correct_answer, given_answer, concept)
    mistakes = lesson.get("common_mistakes", []) or []
    same = [m for m in mistakes if m.get("category") == diag["category"]]
    others = [m for m in mistakes if m.get("category") != diag["category"]]
    diag["lesson_id"] = lesson.get("id", "?")
    diag["lesson_mistakes"] = same + others
    return diag


__all__ = [
    "CANONICAL_LESSONS_DIR",
    "LESSON_CATEGORIES",
    "LessonLoadError",
    "LessonLoadReport",
    "diagnose_with_lesson_mistakes",
    "evaluate_mastery",
    "get_lesson",
    "load_canonical_lessons",
    "load_lessons",
    "order_lessons_for_path",
    "validate_lesson",
]
