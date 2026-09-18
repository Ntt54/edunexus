"""Épreuves blanches BEPC / probatoire / bac (feature 012, T016, research D5).

Blueprints Cameroun (``cm/bepc``, ``cm/premiere-a/c/d``,
``cm/terminale-a/c/d``) chargés depuis :mod:`tutor.packs` — squelettes
titres/objectifs/durées/prérequis UNIQUEMENT, jamais de textes d'épreuve
sous droit. Barème /20 (moyenne pondérée par les coefficients, ``null``
tolérés comme poids unitaires — jamais inventés), mention Passable ≥ 10,
verrouillage au temps imparti, reprise avec temps restant recalculé.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import packs as packs_mod

#: Kinds utilisées aux épreuves blanches : objectifs + ouvertes, hors
#: ``recall_written`` (réservé aux quiz de mémorisation US1) et hors ``code``.
EXAM_KINDS = ["mcq", "true_false", "matching", "open"]

#: Taille par défaut d'une épreuve blanche (comme les examens existants).
DEFAULT_EXAM_SIZE = 10

#: Statuts d'épreuve blanche (colonne ``quizzes.status``) au-delà du
#: cycle quiz (created/in_progress/completed).
EXAM_STATUS_LOCKED = "verrouillée"
EXAM_STATUS_INTERRUPTED = "interrompue"


def list_blueprints() -> list[str]:
    """Clés des blueprints d'épreuve (packs ``cm/*`` embarqués)."""
    return [k for k in packs_mod.list_pack_keys() if k.startswith("cm/")]


def get_blueprint(key: str) -> dict[str, Any]:
    """Charge un blueprint (squelette pack) ou lève ``ValueError`` (→ 400)."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("blueprint requis")
    try:
        return packs_mod.load_pack(key.strip())
    except (KeyError, ValueError) as exc:
        raise ValueError(f"blueprint inconnu : {key}") from exc


def validate_duree(duree_min: Any) -> int:
    """Valide une durée d'épreuve en minutes (``ValueError`` → 400)."""
    if isinstance(duree_min, bool) or not isinstance(duree_min, int):
        raise ValueError("duree_min doit être un entier de minutes > 0")
    if duree_min <= 0:
        raise ValueError("duree_min doit être un entier de minutes > 0")
    return duree_min


def weights_for_pack(pack: dict[str, Any]) -> dict[str, float]:
    """Poids par code matière : coefficient du pack, ``1.0`` si ``null``.

    Les coefficients BEPC/probatoire non confirmés (``null`` + source
    ``OBC/MINESEC à confirmer``) sont tolérés comme poids unitaires —
    jamais inventés (research D4).
    """
    weights: dict[str, float] = {}
    for matiere in pack.get("matieres", []) or []:
        if not isinstance(matiere, dict):
            continue
        code = str(
            matiere.get("code", matiere.get("titre", matiere.get("title", "")))
        ).strip().lower()
        coeff = matiere.get("coefficient")
        if (
            isinstance(coeff, (int, float))
            and not isinstance(coeff, bool)
            and coeff > 0
        ):
            weights[code] = float(coeff)
        else:
            weights[code] = 1.0
    return weights


def concept_weight(concept_name: str, pack: dict[str, Any]) -> float:
    """Poids d'un concept : coefficient de sa matière, ``1.0`` par défaut."""
    weights = weights_for_pack(pack)
    name = (concept_name or "").strip().lower()
    for matiere in pack.get("matieres", []) or []:
        if not isinstance(matiere, dict):
            continue
        code = str(
            matiere.get("code", matiere.get("titre", ""))
        ).strip().lower()
        titre = str(matiere.get("titre", matiere.get("title", ""))).strip().lower()
        if name == code or name == titre or name.startswith(titre + " —"):
            return weights.get(code, 1.0)
        for chapitre in matiere.get("chapitres", []) or []:
            if not isinstance(chapitre, dict):
                continue
            ctitre = str(
                chapitre.get("titre", chapitre.get("title", ""))
            ).strip().lower()
            if name == ctitre:
                return weights.get(code, 1.0)
    return 1.0


def seed_concepts_from_pack(store: Any, subject_id: str, pack: dict[str, Any]) -> list[Any]:
    """Crée (get-or-create) un concept par chapitre du blueprint.

    Retourne les concepts dans l'ordre du pack (matières puis chapitres).
    Les noms sont les titres des chapitres — squelettes uniquement.
    """
    concepts: list[Any] = []
    for matiere in pack.get("matieres", []) or []:
        if not isinstance(matiere, dict):
            continue
        for chapitre in matiere.get("chapitres", []) or []:
            if not isinstance(chapitre, dict):
                continue
            title = str(
                chapitre.get("titre", chapitre.get("title", ""))
            ).strip()
            if not title:
                continue
            existing = store.get_concept_by_name(subject_id, title)
            if existing is not None:
                concepts.append(existing)
            else:
                concepts.append(store.create_concept(subject_id, title))
    if not concepts:
        raise KeyError("blueprint sans chapitres exploitables")
    return concepts


def scale_to_20(earned: float, total: float) -> float:
    """Moyenne pondérée ramenée sur 20 (``0.0`` si aucun point)."""
    if total <= 0:
        return 0.0
    return round(float(earned) / float(total) * 20.0, 2)


def mention(score_20: float) -> str:
    """Mention française : Passable dès 10/20 (FR-006)."""
    s = float(score_20)
    if s >= 16:
        return "Très bien"
    if s >= 14:
        return "Bien"
    if s >= 12:
        return "Assez bien"
    if s >= 10:
        return "Passable"
    return "Insuffisant"


def grade_from_entries(
    entries: list[dict[str, Any]],
) -> tuple[float, list[dict[str, Any]], str]:
    """Corrige une épreuve : ``(score_20, detail_competences, mention)``.

    ``entries`` : ``{question_id, competence, points, awarded}`` par
    question — ``points`` porte déjà le coefficient (moyenne pondérée).
    Le détail est donné /20 par compétence (nom du concept).
    """
    by_comp: dict[str, dict[str, float]] = {}
    earned_total = 0.0
    points_total = 0.0
    for entry in entries:
        comp = str(entry.get("competence") or "Sans notion")
        points = float(entry.get("points") or 0.0)
        awarded = float(entry.get("awarded") or 0.0)
        slot = by_comp.setdefault(comp, {"earned": 0.0, "total": 0.0})
        slot["earned"] += awarded
        slot["total"] += points
        earned_total += awarded
        points_total += points
    score = scale_to_20(earned_total, points_total)
    detail = [
        {
            "competence": comp,
            "score_20": scale_to_20(slot["earned"], slot["total"]),
            "mention": mention(scale_to_20(slot["earned"], slot["total"])),
        }
        for comp, slot in sorted(by_comp.items())
    ]
    return score, detail, mention(score)


def corrective_targets(
    per_question: list[dict[str, Any]],
) -> list[str]:
    """Ids des concepts ratés (verdict ≠ correct) pour le correctif ciblé.

    La séance suivante fait refaire surface ces mêmes compétences
    (échantillonneur entremêlé, FR-003/FR-007).
    """
    failed: list[str] = []
    for entry in per_question or []:
        if entry.get("verdict") != "correct" and entry.get("concept_id"):
            cid = str(entry["concept_id"])
            if cid not in failed:
                failed.append(cid)
    return failed


def _parse_started(started_at: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(started_at))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def elapsed_s(started_at: Any, now: datetime | None = None) -> float:
    """Secondes écoulées depuis le début de l'épreuve (0 si illisible)."""
    started = _parse_started(started_at)
    if started is None:
        return 0.0
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0.0, (ref - started).total_seconds())


def is_locked(started_at: Any, time_limit_s: Any) -> bool:
    """Vrai quand le temps imparti est écoulé (verrouillage, FR-006)."""
    try:
        limit = float(time_limit_s or 0)
    except (TypeError, ValueError):
        return False
    if limit <= 0:
        return False
    return elapsed_s(started_at) >= limit


def remaining_s(started_at: Any, time_limit_s: Any) -> int:
    """Temps restant recalculé en secondes (0 si verrouillée)."""
    try:
        limit = float(time_limit_s or 0)
    except (TypeError, ValueError):
        return 0
    if limit <= 0:
        return 0
    return max(0, int(limit - elapsed_s(started_at)))
