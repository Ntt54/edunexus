"""FSRS (Free Spaced Repetition Scheduler) — portage US5 P2-Adaptatif.

Adapté de ``autreprojet/OpenTutor-main``
(``apps/api/services/spaced_repetition/fsrs.py`` : DEFAULT_W 21 params
FSRS-5/6, FSRSCard, ReviewLog, stabilité/difficulté initiales et suivantes,
retrievability, revues intra-journalières, ``review_card``,
``estimate_forgetting_cost``).

Version locale : stdlib seul (``math`` + ``dataclasses`` + ``datetime``) —
aucune dépendance (ni torch ni requête), aucun import UI (ni fastapi ni
textual). La seule adaptation est le helper horaire ``_as_utc`` (remplace
``libs.datetime_utils.as_utc``, borne non portée).

Références : FSRS-4.5 (fsrs4.5) et FSRS-5/6 (fsrs-rs) — open-spaced-repetition.

Échelle de notation : 1 = Again (oubli), 2 = Hard, 3 = Good, 4 = Easy.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Paramètres par défaut FSRS-5/6 (21 params, cf. source).
DEFAULT_W = [
    0.4, 0.6, 2.4, 5.8,          # w0-w3 : stabilité initiale par note
    4.93, 0.94, 0.86, 0.01,      # w4-w7 : paramètres de difficulté
    1.49, 0.14, 0.94,            # w8-w10 : paramètres de stabilité
    2.18, 0.05, 0.34, 1.26,      # w11-w14 : paramètres de récupérabilité
    0.29, 2.61,                   # w15-w16 : pénalité Hard / bonus Easy
    0.0, 0.0, 0.0,               # w17-w19 : revues intra-journalières (FSRS-5)
    1.0,                          # w20 : decay courbe d'oubli (1.0 = FSRS-4.5)
]

_VALID_RATINGS = (1, 2, 3, 4)


def _as_utc(moment: datetime | None) -> datetime:
    """Normalise en UTC aware (remplace ``libs.datetime_utils.as_utc``)."""
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


@dataclass
class FSRSCard:
    """Carte FSRS : difficulté 1-10, stabilité en jours, historique."""

    difficulty: float = 5.0
    stability: float = 0.0  # jours jusqu'à R = 90 %
    reps: int = 0
    lapses: int = 0
    last_review: datetime | None = None
    due: datetime | None = None
    state: str = "new"  # new | learning | review | relearning


@dataclass
class ReviewLog:
    """Résultat d'une révision."""

    rating: int  # 1-4
    scheduled_days: int
    elapsed_days: int
    state: str  # état AVANT la révision
    review_time: datetime


def _initial_stability(rating: int, w: list[float] = DEFAULT_W) -> float:
    return max(w[rating - 1], 0.1)


def _initial_difficulty(rating: int, w: list[float] = DEFAULT_W) -> float:
    """D0 = w4 - exp(w5·(G-1)) + 1 (FSRS-5/6, exponentiel)."""
    d = w[4] - math.exp(w[5] * (rating - 1)) + 1
    return min(max(d, 1.0), 10.0)


def _next_difficulty(d: float, rating: int, w: list[float] = DEFAULT_W) -> float:
    """Amortissement linéaire + retour à la moyenne vers D0(4)."""
    delta_d = -w[6] * (rating - 3)
    d_new = d + delta_d * (10 - d) / 9
    d_new = w[7] * _initial_difficulty(4, w) + (1 - w[7]) * d_new
    return min(max(d_new, 1.0), 10.0)


def _next_stability(
    d: float,
    s: float,
    r: float,
    rating: int,
    w: list[float] = DEFAULT_W,
) -> float:
    """Stabilité suivante après révision (oubli = formule lapse)."""
    if rating == 1:
        return max(
            w[11] * pow(d, -w[12]) * (pow(s + 1, w[13]) - 1) * math.exp((1 - r) * w[14]),
            0.1,
        )
    hard_penalty = w[15] if rating == 2 else 1.0
    easy_bonus = w[16] if rating == 4 else 1.0
    new_s = s * (
        1 + math.exp(w[8])
        * (11 - d)
        * pow(s, -w[9])
        * (math.exp((1 - r) * w[10]) - 1)
        * hard_penalty
        * easy_bonus
    )
    return max(new_s, 0.1)


def _same_day_stability(
    s: float,
    rating: int,
    w: list[float] = DEFAULT_W,
) -> float:
    """Stabilité intra-journalière FSRS-5 (anti-gonflement)."""
    if len(w) <= 19 or (w[17] == 0 and w[18] == 0 and w[19] == 0):
        return s
    new_s = s * math.exp(w[17] * (rating - 3 + w[18])) * pow(s, -w[19])
    return max(new_s, 0.1)


def retrievability(
    elapsed_days: float, stability: float, w: list[float] = DEFAULT_W
) -> float:
    """Probabilité de rappel R(t) = (1 + t/(9·decay·S))^(-decay)."""
    if stability <= 0:
        return 0.0
    decay = w[20] if len(w) > 20 else 1.0
    factor = 9 * decay
    return pow(1 + elapsed_days / (factor * stability), -decay)


def estimate_forgetting_cost(
    cards: list[FSRSCard],
    now: datetime | None = None,
) -> float:
    """Nombre attendu de cartes oubliées si on ne révise pas (≥ 2 = urgent)."""
    now = _as_utc(now)
    total = 0.0
    for card in cards:
        if card.due is None or card.stability <= 0:
            continue
        due = _as_utc(card.due)
        if due > now:
            continue
        elapsed = max((now - due).total_seconds() / 86400, 0)
        total_elapsed = elapsed + max(card.stability, 1.0)
        total += 1.0 - retrievability(total_elapsed, card.stability)
    return round(total, 2)


def estimate_session_urgency(
    cards: list[FSRSCard],
    now: datetime | None = None,
) -> dict:
    """Urgence de session : none | low | normal | high | critical."""
    now = _as_utc(now)
    due_cards = [c for c in cards if c.due and _as_utc(c.due) <= now]
    cost = estimate_forgetting_cost(cards, now)
    if cost >= 5.0:
        urgency, recommendation = (
            "critical",
            "Révision immédiate — risque de perte significatif",
        )
    elif cost >= 2.0:
        urgency, recommendation = (
            "high",
            "Révisez bientôt — plusieurs éléments risquent l'oubli",
        )
    elif len(due_cards) >= 10:
        urgency, recommendation = (
            "normal",
            "Bon moment pour une révision complète",
        )
    elif len(due_cards) >= 3:
        urgency, recommendation = (
            "low",
            "Quelques éléments dus — une révision rapide suffit",
        )
    else:
        urgency, recommendation = "none", "Rien à réviser pour l'instant"
    return {
        "forgetting_cost": cost,
        "urgency": urgency,
        "due_count": len(due_cards),
        "total_cards": len(cards),
        "recommendation": recommendation,
    }


def review_card(
    card: FSRSCard,
    rating: int,
    now: datetime | None = None,
) -> tuple[FSRSCard, ReviewLog]:
    """Applique une révision (1=Again, 2=Hard, 3=Good, 4=Easy).

    Retourne la carte mise à jour + le log. Première révision :
    stabilité/difficulté initiales ; ``rating < 3`` ⇒ ``learning``.
    """
    if rating not in _VALID_RATINGS:
        raise ValueError(f"rating {rating!r} invalide ; attendu 1-4.")
    now = _as_utc(now)

    if card.last_review:
        elapsed_days = max((now - _as_utc(card.last_review)).total_seconds() / 86400, 0)
    else:
        elapsed_days = 0

    old_state = card.state

    if card.state == "new" or card.reps == 0:
        card.difficulty = _initial_difficulty(rating)
        card.stability = _initial_stability(rating)
        card.state = "learning" if rating < 3 else "review"
    else:
        r = retrievability(elapsed_days, card.stability)
        new_d = _next_difficulty(card.difficulty, rating)
        if elapsed_days < 1.0 and card.reps > 0:
            new_s = _same_day_stability(card.stability, rating)
        else:
            new_s = _next_stability(card.difficulty, card.stability, r, rating)
        card.difficulty = new_d
        card.stability = new_s
        if rating == 1:
            card.lapses += 1
            card.state = "relearning"
        else:
            card.state = "review"

    card.reps += 1
    card.last_review = now

    if rating == 1:
        scheduled_days = 1
    elif card.state == "learning":
        scheduled_days = 1
    else:
        scheduled_days = max(1, round(card.stability))
    card.due = now + timedelta(days=scheduled_days)

    log = ReviewLog(
        rating=rating,
        scheduled_days=scheduled_days,
        elapsed_days=round(elapsed_days),
        state=old_state,
        review_time=now,
    )
    return card, log


__all__ = [
    "DEFAULT_W",
    "FSRSCard",
    "ReviewLog",
    "estimate_forgetting_cost",
    "estimate_session_urgency",
    "retrievability",
    "review_card",
]
