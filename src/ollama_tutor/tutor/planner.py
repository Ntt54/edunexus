"""Planning semestre ECTS (012 US3, FR-011 — D9 research).

Charge par UE + dates d'épreuves → créneaux hebdomadaires
(``révision`` / ``simulation`` / ``cours``) :

- 1 ECTS = 25-30 h (guide ECTS) ; une UE peut donner ``heures`` ou
  ``ects`` (convertis au point médian 27,5 h) ;
- **règle 150 %** : aucune semaine ne dépasse 150 % de la moyenne
  hebdomadaire — génération rejetée (``ValueError`` → 400) si aucun
  rééquilibrage n'y parvient ;
- **recompaction plafonnée** après absence : les minutes manquées sont
  réétalées sur les semaines restantes sans jamais dépasser le plafond,
  en priorité sur les notions fragiles ;
- **replanification plafonnée des rappels US1** (follow-up G2) :
  :func:`replan_reminders_capped` étale les révisions dues sur les
  prochains jours sous un plafond journalier — réutilisée par
  :meth:`tutor.review.ReviewScheduler.recompacted_due`.

Pur Python stdlib, UI-framework-free (aucune dépendance web/TUI).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

#: 1 ECTS = 25-30 h (guide ECTS) ; conversion au point médian.
ECTS_HOURS_MIN = 25.0
ECTS_HOURS_MAX = 30.0
ECTS_HOURS_REF = (ECTS_HOURS_MIN + ECTS_HOURS_MAX) / 2.0

#: Règle 150 % : aucune semaine > 1,5 × moyenne hebdomadaire.
MAX_WEEK_FACTOR = 1.5

#: Semestre de repli quand aucune date d'épreuve ne borne l'horizon.
DEFAULT_WEEKS = 14

#: Fenêtre de simulation avant une épreuve (semaines).
SIMULATION_WINDOW_WEEKS = 2

#: Types de créneaux (CHECK plan_slots.kind).
SLOT_KINDS = ("révision", "simulation", "cours")


def ue_hours(ue: dict[str, Any] | Any) -> float:
    """Charge d'une UE en heures (``heures`` direct ou ``ects × 27,5``)."""
    if isinstance(ue, dict):
        raw_hours = ue.get("heures")
        raw_ects = ue.get("ects")
    else:
        raw_hours = getattr(ue, "heures", None)
        raw_ects = getattr(ue, "ects", None)
    if raw_hours is not None:
        return float(raw_hours)
    if raw_ects is not None:
        return float(raw_ects) * ECTS_HOURS_REF
    raise ValueError("UE sans charge : 'heures' ou 'ects' requis")


def _parse_date(raw: Any) -> date:
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError as exc:
        raise ValueError(f"date invalide : {raw!r}") from exc


def _horizon_weeks(
    start: date, epreuves: list[dict[str, Any]], weeks: int | None
) -> int:
    """Nombre de semaines du semestre (explicit > épreuves > repli 14)."""
    if weeks is not None:
        n = int(weeks)
        if n < 1:
            raise ValueError("weeks >= 1 requis")
        return n
    if epreuves:
        last = max(_parse_date(e.get("date")) for e in epreuves)
        n = math.ceil(max(0, (last - start).days) / 7) or 1
        return max(1, n)
    return DEFAULT_WEEKS


def plan_semester(
    ues: list[dict[str, Any]],
    epreuves: list[dict[str, Any]] | None = None,
    *,
    start_date: str | date | None = None,
    weeks: int | None = None,
    max_heures_semaine: float | None = None,
    title: str = "",
) -> dict[str, Any]:
    """Répartit la charge des UE en créneaux hebdomadaires.

    Retourne ``{"title", "semaines": [{"semaine", "minutes_total",
    "creneaux": [{"subject_id", "minutes", "kind"}]}], "heures_totales",
    "moyenne_semaine_min", "plafond_semaine_min"}``.

    Lève ``ValueError`` (→ 400) quand la génération est impossible sans
    dépasser 150 % de la moyenne : UE vides/charge ≤ 0, plafond
    ``max_heures_semaine`` intenable, ou répartition non rééquilibrable.
    """
    ues = list(ues or [])
    epreuves = list(epreuves or [])
    if not ues:
        raise ValueError("au moins une UE est requise")
    start = _parse_date(start_date) if start_date else date.today()
    loads: list[tuple[str, float]] = []
    for ue in ues:
        sid = str((ue.get("subject_id") if isinstance(ue, dict) else getattr(ue, "subject_id", "")) or "").strip()
        if not sid:
            raise ValueError("chaque UE requiert un subject_id")
        hours = ue_hours(ue)
        if hours <= 0:
            raise ValueError(f"charge UE {sid!r} invalide (heures > 0 requises)")
        loads.append((sid, hours * 60.0))
    n_weeks = _horizon_weeks(start, epreuves, weeks)
    total_min = sum(m for _, m in loads)
    mean = total_min / n_weeks
    ceiling = mean * MAX_WEEK_FACTOR
    if max_heures_semaine is not None and float(max_heures_semaine) > 0:
        cap_min = float(max_heures_semaine) * 60.0
        if mean > cap_min:
            raise ValueError(
                "charge moyenne hebdomadaire "
                f"({mean / 60:.1f} h) au-delà du plafond demandé "
                f"({float(max_heures_semaine):.1f} h) : semestre impossible sans surcharge"
            )
        ceiling = min(ceiling, cap_min)
    # Semaines d'épreuves par matière (pour les créneaux simulation).
    exam_week: dict[str, int] = {}
    for e in epreuves:
        sid = str(e.get("subject_id") or "").strip()
        if not sid:
            continue
        delta = (_parse_date(e.get("date")) - start).days
        exam_week[sid] = min(n_weeks - 1, max(0, delta // 7))
    semaines: list[dict[str, Any]] = []
    for w in range(n_weeks):
        creneaux: list[dict[str, Any]] = []
        for sid, minutes in loads:
            share = minutes / n_weeks
            kind = "révision"
            ew = exam_week.get(sid)
            if ew is not None and ew - SIMULATION_WINDOW_WEEKS < w <= ew:
                kind = "simulation"
            creneaux.append({"subject_id": sid, "minutes": round(share), "kind": kind})
        week_total = sum(c["minutes"] for c in creneaux)
        semaines.append({"semaine": w + 1, "minutes_total": week_total, "creneaux": creneaux})
    # Règle 150 % : rééquilibrage (lissage vers la moyenne), sinon 400.
    totals = [w["minutes_total"] for w in semaines]
    actual_mean = sum(totals) / len(totals) if totals else 0.0
    allowed = max(ceiling, actual_mean * MAX_WEEK_FACTOR)
    over = [t for t in totals if t > allowed + 1e-6]
    if over:
        semaines = _level_weeks(semaines, allowed)
        totals = [w["minutes_total"] for w in semaines]
        still_over = [t for t in totals if t > allowed + 1e-6]
        if still_over:
            raise ValueError(
                "répartition impossible sans dépasser 150 % de la moyenne hebdomadaire"
            )
    return {
        "title": title or "Semestre",
        "semaines": semaines,
        "heures_totales": round(total_min / 60.0, 1),
        "moyenne_semaine_min": round(actual_mean, 1),
        "plafond_semaine_min": round(allowed, 1),
        "regle_150_pct": True,
    }


def _level_weeks(
    semaines: list[dict[str, Any]], allowed: float
) -> list[dict[str, Any]]:
    """Lisse les semaines en surcharge vers les semaines creuses.

    Déplace des minutes des créneaux ``révision`` des semaines > plafond
    vers les semaines les plus creuses, sans jamais dépasser le plafond.
    """
    semaines = [
        {"semaine": w["semaine"], "minutes_total": 0,
         "creneaux": [dict(c) for c in w["creneaux"]]}
        for w in semaines
    ]
    for w in semaines:
        w["minutes_total"] = sum(c["minutes"] for c in w["creneaux"])
    for w in semaines:
        while w["minutes_total"] > allowed + 1e-6:
            movable = next(
                (c for c in sorted(w["creneaux"], key=lambda c: -c["minutes"])
                 if c["kind"] == "révision" and c["minutes"] > 0),
                None,
            )
            if movable is None:
                break
            room = [(o, allowed - o["minutes_total"]) for o in semaines if o is not w]
            room = [(o, r) for o, r in room if r > 1e-6]
            if not room:
                break
            target, free = max(room, key=lambda t: t[1])
            move = min(movable["minutes"], math.floor(free))
            if move < 1:
                break
            movable["minutes"] -= move
            w["minutes_total"] -= move
            twin = next(
                (c for c in target["creneaux"]
                 if c["subject_id"] == movable["subject_id"] and c["kind"] == movable["kind"]),
                None,
            )
            if twin is None:
                twin = {"subject_id": movable["subject_id"], "minutes": 0, "kind": movable["kind"]}
                target["creneaux"].append(twin)
            twin["minutes"] += move
            target["minutes_total"] += move
    return semaines


def recompact_plan(
    plan: dict[str, Any],
    missed_weeks: list[int],
    *,
    fragile_subject_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Réétale les semaines manquées (1-based) sur les semaines restantes.

    Plafonné : aucune semaine cible ne dépasse 150 % de la moyenne
    recalculée ; les minutes excédentaires sont reportées, en priorité,
    sur les matières fragiles (``fragile_subject_ids`` d'abord). Les
    semaines manquées passent à 0 minute. Retourne le plan recompacté
    (même enveloppe que :func:`plan_semester`).

    Les minutes qui ne tiennent sous aucun plafond (toutes les semaines
    restantes au plafond) ne sont jamais surchargées : elles sont
    comptabilisées dans ``minutes_non_replanifiees`` (``{subject_id:
    minutes}``) et ``minutes_non_replanifiees_total``, avec
    ``placé + non-replanifié == dû`` par matière.
    """
    semaines = [dict(w, creneaux=[dict(c) for c in w["creneaux"]]) for w in plan.get("semaines", [])]
    if not semaines:
        raise ValueError("plan vide : rien à recompacter")
    missed = {m for m in missed_weeks if 1 <= m <= len(semaines)}
    if not missed:
        return dict(plan, semaines=semaines)
    fragile = [s for s in (fragile_subject_ids or [])]
    # Minutes à réétaler, fragiles d'abord.
    owed: dict[str, float] = {}
    for m in sorted(missed):
        w = semaines[m - 1]
        for c in w["creneaux"]:
            owed[c["subject_id"]] = owed.get(c["subject_id"], 0.0) + c["minutes"]
        w["creneaux"] = []
        w["minutes_total"] = 0
    remaining = [w for i, w in enumerate(semaines, start=1) if i not in missed]
    if not remaining:
        raise ValueError("aucune semaine restante pour recompacter")
    totals = [w["minutes_total"] for w in semaines if w["minutes_total"] > 0]
    mean = (sum(totals) / len(totals)) if totals else 0.0
    allowed = (mean or sum(owed.values())) * MAX_WEEK_FACTOR
    ordered_subjects = fragile + [s for s in owed if s not in fragile]
    placed: dict[str, int] = {}
    for sid in ordered_subjects:
        due = owed.get(sid, 0.0)
        while due > 1e-6:
            targets = sorted(
                (w for w in remaining if w["minutes_total"] < allowed),
                key=lambda w: w["minutes_total"],
            )
            if not targets:
                break  # plafond atteint partout : on ne surcharge jamais
            target = targets[0]
            move = min(due, allowed - target["minutes_total"])
            if move < 1:
                break
            twin = next((c for c in target["creneaux"] if c["subject_id"] == sid), None)
            if twin is None:
                twin = {"subject_id": sid, "minutes": 0, "kind": "révision"}
                target["creneaux"].append(twin)
            added = int(round(move))
            twin["minutes"] += added
            target["minutes_total"] += added
            placed[sid] = placed.get(sid, 0) + added
            due -= move
    unplaced: dict[str, int] = {}
    for sid, owed_min in owed.items():
        left = int(round(owed_min - placed.get(sid, 0)))
        if left > 0:
            unplaced[sid] = left
    out = dict(plan)
    out["semaines"] = semaines
    out["recompacte"] = True
    out["semaines_manquees"] = sorted(missed)
    out["minutes_non_replanifiees"] = unplaced
    out["minutes_non_replanifiees_total"] = sum(unplaced.values())
    return out


# ---------------------------------------------------------------------------
# Replanification plafonnée des rappels US1 (reuse review.py)
# ---------------------------------------------------------------------------


def replan_reminders_capped(
    items: list[dict[str, Any]],
    *,
    cap_factor: float = MAX_WEEK_FACTOR,
    max_days: int = 7,
    daily_capacity: int | None = None,
) -> dict[str, Any]:
    """Étale des révisions dues sur les prochains jours, sous plafond.

    ``items`` : révisions dues (toute dict, triées par ``overdue_days``
    décroissant — les plus en retard d'abord). Aucun jour ne reçoit plus
    de ``daily_cap`` items (plafond = ``cap_factor × moyenne`` borné par
    ``daily_capacity``). Retourne ``{"days": [{"jour", "items"}],
    "daily_cap", "capped": True}`` — jamais d'empilement infini : l'horizon
    s'étend au besoin au-delà de ``max_days``.
    """
    items = list(items or [])
    if not items:
        return {"days": [], "daily_cap": 0, "capped": True}
    ordered = sorted(items, key=lambda d: int(d.get("overdue_days", 0) or 0), reverse=True)
    worst = int(ordered[0].get("overdue_days", 0) or 0)
    span = max(1, min(max(1, max_days), max(1, worst)))
    mean = len(ordered) / span
    cap = max(1, math.ceil(mean * max(1.0, float(cap_factor))))
    if daily_capacity is not None:
        cap = min(cap, max(1, int(daily_capacity)))
    days: list[dict[str, Any]] = []
    idx = 0
    while idx < len(ordered):
        day_items = ordered[idx:idx + cap]
        days.append({"jour": len(days) + 1, "items": day_items})
        idx += cap
    return {"days": days, "daily_cap": cap, "capped": True}


__all__ = [
    "ECTS_HOURS_MIN",
    "ECTS_HOURS_MAX",
    "ECTS_HOURS_REF",
    "MAX_WEEK_FACTOR",
    "DEFAULT_WEEKS",
    "SLOT_KINDS",
    "ue_hours",
    "plan_semester",
    "recompact_plan",
    "replan_reminders_capped",
]
