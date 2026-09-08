"""Blocs adaptatifs simplifiés (010 P2-Adaptatif, T036).

Adapté de ``autreprojet/OpenTutor-main``
(``services/block_decision/rules.py`` + ``engine.py`` : 12 règles,
``MAX_OPS_PER_TURN = 2``, tri par urgence décroissante).

Simplification volontaire : 4 règles SEULEMENT
(deadline/forgetting/weak/prereq) — charge cognitive, frustration,
inactivité, LECTOR et maintenance-mode sont hors périmètre (décision
T036 : pilotage des espaces, pas du layout fin).

Signaux : ``list[dict]`` avec ``signal_type`` (``deadline`` |
``forgetting_risk`` | ``weak_area`` | ``prerequisite_gap``), ``urgency``
optionnelle (0-100), ``concept`` / ``detail`` optionnels.

Stdlib seul (``dataclasses``), aucun import UI (ni fastapi ni textual).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: Plafond d'opérations par tour (engine source : MAX_OPS_PER_TURN).
MAX_OPS_PER_TURN = 2


@dataclass
class BlockOperation:
    """Opération sur un bloc d'espace : ajout / retrait / reconfiguration."""

    action: Literal["add", "remove", "update_config"]
    block_type: str
    reason: str
    signal_source: str
    urgency: float  # 0-100
    config: dict[str, Any] = field(default_factory=dict)


def _clamp_urgency(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def rule_deadline(
    signals: list[dict[str, Any]], current_blocks: list[str]
) -> BlockOperation | None:
    """Échéance urgente (≥ 80) → ajoute le bloc plan (source)."""
    deadlines = [s for s in signals if s.get("signal_type") == "deadline"]
    urgent = [d for d in deadlines if _clamp_urgency(d.get("urgency")) >= 80]
    if urgent and "plan" not in current_blocks:
        detail = urgent[0].get("detail", {}) or {}
        title = detail.get("title", "un devoir")
        return BlockOperation(
            action="add",
            block_type="plan",
            reason=f"Échéance proche pour {title}. Ajout du plan d'étude.",
            signal_source="deadline",
            urgency=80,
        )
    return None


def rule_forgetting(
    signals: list[dict[str, Any]], current_blocks: list[str]
) -> BlockOperation | None:
    """≥ 3 risques d'oubli urgents (≥ 70) → ajoute le bloc révision (source)."""
    forgetting = [s for s in signals if s.get("signal_type") == "forgetting_risk"]
    urgent_count = sum(1 for s in forgetting if _clamp_urgency(s.get("urgency")) >= 70)
    if urgent_count >= 3 and "review" not in current_blocks:
        return BlockOperation(
            action="add",
            block_type="review",
            reason=(
                f"{urgent_count} concepts risquent l'oubli. "
                "Ajout de la révision."
            ),
            signal_source="forgetting_risk",
            urgency=85,
        )
    return None


def rule_weak(
    signals: list[dict[str, Any]], current_blocks: list[str]
) -> BlockOperation | None:
    """≥ 3 zones faibles → ajoute le bloc d'analyse d'erreurs (source)."""
    weak = [s for s in signals if s.get("signal_type") == "weak_area"]
    if len(weak) >= 3 and "wrong_answers" not in current_blocks:
        return BlockOperation(
            action="add",
            block_type="wrong_answers",
            reason=(
                f"{len(weak)} zones faibles détectées. "
                "Ajout de l'analyse d'erreurs."
            ),
            signal_source="weak_area",
            urgency=70,
        )
    return None


def rule_prereq(
    signals: list[dict[str, Any]], current_blocks: list[str]
) -> BlockOperation | None:
    """≥ 1 prérequis manquant → ajoute le graphe de connaissances (source)."""
    gaps = [s for s in signals if s.get("signal_type") == "prerequisite_gap"]
    if gaps and "knowledge_graph" not in current_blocks:
        concepts = [str(s.get("concept", "inconnu")) for s in gaps[:3]]
        return BlockOperation(
            action="add",
            block_type="knowledge_graph",
            reason=(
                "Prérequis manquants : " + ", ".join(concepts) + ". "
                "Ajout du graphe pour visualiser les dépendances."
            ),
            signal_source="prerequisite_gap",
            urgency=75,
        )
    return None


_RULES = (rule_deadline, rule_forgetting, rule_weak, rule_prereq)


def decide_blocks(
    signals: list[dict[str, Any]],
    current_blocks: list[str],
    max_ops: int = MAX_OPS_PER_TURN,
) -> list[BlockOperation]:
    """Évalue les 4 règles, trie par urgence décroissante, plafonne.

    ``max_ops`` borne le nombre d'opérations retournées (défaut 2, engine
    source). Signaux inconnus ignorés ; jamais d'exception sur entrées
    malformées (garde-fous best-effort).
    """
    ops: list[BlockOperation] = []
    try:
        for rule in _RULES:
            try:
                op = rule(signals or [], current_blocks or [])
            except Exception:  # noqa: BLE001 - une règle ne bloque jamais
                logger.debug("Règle de blocs en échec", exc_info=True)
                continue
            if op is not None:
                ops.append(op)
    except Exception:  # noqa: BLE001 - entrées malformées → aucune op
        return []
    ops.sort(key=lambda o: o.urgency, reverse=True)
    return ops[: max(0, int(max_ops))]


__all__ = [
    "MAX_OPS_PER_TURN",
    "BlockOperation",
    "decide_blocks",
    "rule_deadline",
    "rule_forgetting",
    "rule_prereq",
    "rule_weak",
]
