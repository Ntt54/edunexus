"""Partage parent à consentement explicite (feature 012, T017, research D6).

Parents seuls en v1 (clarification spec) : aucun modèle de rôles, un simple
``share_token`` (32+ chars) porté par la table ``parent_shares``
(``consent`` ∈ accordé/révoqué). La vue agrégée est en lecture seule :
temps d'apprentissage, maîtrise, erreurs fréquentes, prochain jalon.
Sans ``consent=accordé`` (révocation, token inconnu/absent) → 403 côté
transport (``PermissionError`` ici).

Données de mineur : partage explicite accordé par l'apprenant, révocable
à tout moment (token mort immédiat). V1 : agrégation du foyer local
(mono-apprenant) — le token porte le consentement, pas un périmètre.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

CONSENT_GRANTED = "accordé"
CONSENT_REVOKED = "révoqué"

#: Fenêtre de la vue parent : 7 derniers jours.
OVERVIEW_WINDOW_DAYS = 7

#: Erreurs fréquentes exposées (plafond de lecture seule).
TOP_ERRORS_LIMIT = 5


class SharePermissionError(PermissionError):
    """Partage absent, révoqué ou sans consentement (→ HTTP 403)."""


def grant_share(store: Any, learner_id: str) -> str:
    """Accorde le partage parent : génère un ``share_token`` (43 chars).

    Lève ``KeyError`` si l'apprenant est inconnu (→ 404). Chaque accord
    régénère le token (data-model.md).
    """
    if store.get_learner(learner_id) is None:
        raise KeyError(f"Unknown learner: {learner_id}")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    store._conn.execute(
        "INSERT INTO parent_shares (learner_id, consent, share_token, revoked_at) "
        "VALUES (?, ?, ?, NULL) "
        "ON CONFLICT(learner_id) DO UPDATE SET "
        "consent = excluded.consent, share_token = excluded.share_token, "
        "revoked_at = NULL",
        (learner_id, CONSENT_GRANTED, token),
    )
    store._conn.commit()
    _touch_learner(store, learner_id, now)
    return token


def revoke_share(store: Any, learner_id: str) -> dict[str, bool]:
    """Révoque le partage : token mort immédiat (→ 403).

    Lève ``KeyError`` si l'apprenant est inconnu (→ 404).
    """
    if store.get_learner(learner_id) is None:
        raise KeyError(f"Unknown learner: {learner_id}")
    now = datetime.now(timezone.utc).isoformat()
    store._conn.execute(
        "INSERT INTO parent_shares (learner_id, consent, share_token, revoked_at) "
        "VALUES (?, ?, NULL, ?) "
        "ON CONFLICT(learner_id) DO UPDATE SET "
        "consent = excluded.consent, share_token = NULL, "
        "revoked_at = excluded.revoked_at",
        (learner_id, CONSENT_REVOKED, now),
    )
    store._conn.commit()
    _touch_learner(store, learner_id, now)
    return {"revoked": True}


def _touch_learner(store: Any, learner_id: str, now: str) -> None:
    try:
        store._conn.execute(
            "UPDATE learner_profiles SET updated_at = ? WHERE id = ?",
            (now, learner_id),
        )
        store._conn.commit()
    except Exception:
        pass


def resolve_learner_by_token(store: Any, token: str) -> str:
    """Id de l'apprenant consenti pour ce token (``SharePermissionError`` → 403)."""
    if not token or not str(token).strip():
        raise SharePermissionError("token parent requis")
    row = store._conn.execute(
        "SELECT learner_id, consent FROM parent_shares WHERE share_token = ?",
        (str(token).strip(),),
    ).fetchone()
    if row is None or dict(row).get("consent") != CONSENT_GRANTED:
        raise SharePermissionError("partage révoqué ou sans consentement")
    return str(dict(row)["learner_id"])


def parent_overview(store: Any, token: str) -> dict[str, Any]:
    """Vue parent agrégée, lecture seule : temps, maîtrise, erreurs, jalon.

    Lève ``SharePermissionError`` (→ 403) si le token est absent, inconnu
    ou révoqué. N'écrit jamais (hors lecture).
    """
    learner_id = resolve_learner_by_token(store, token)
    return {
        "learner_id": learner_id,
        "temps_semaine_min": _weekly_minutes(store),
        "maitrise": _mastery_by_subject(store),
        "erreurs_frequentes": _frequent_errors(store),
        "prochain_jalon": _next_milestone(store),
    }


def _weekly_minutes(store: Any) -> int:
    """Minutes de séances de tutorat sur les 7 derniers jours."""
    try:
        rows = store._conn.execute(
            "SELECT started_at, last_active_at FROM tutoring_sessions"
        ).fetchall()
    except Exception:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=OVERVIEW_WINDOW_DAYS)
    total = 0.0
    for row in rows:
        d = dict(row)
        try:
            started = datetime.fromisoformat(str(d.get("started_at", "")))
            active = datetime.fromisoformat(
                str(d.get("last_active_at") or d.get("started_at", ""))
            )
        except (TypeError, ValueError):
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if active.tzinfo is None:
            active = active.replace(tzinfo=timezone.utc)
        if started < cutoff:
            continue
        total += max(0.0, (active - started).total_seconds())
    return int(total // 60)


def _mastery_by_subject(store: Any) -> dict[str, float]:
    """Maîtrise moyenne (0-100) par matière, depuis ``progress``."""
    try:
        subjects = {s.id: s.name for s in store.list_subjects()}
    except Exception:
        return {}
    out: dict[str, float] = {}
    for subject_id, name in subjects.items():
        try:
            rows = store._conn.execute(
                "SELECT score FROM progress WHERE subject_id = ?", (subject_id,)
            ).fetchall()
        except Exception:
            continue
        scores = [float(r["score"]) for r in rows]
        if scores:
            out[name] = round(sum(scores) / len(scores), 2)
    return out


def _frequent_errors(store: Any) -> list[dict[str, Any]]:
    """Erreurs les plus fréquentes (concept + occurrences)."""
    try:
        rows = store._conn.execute(
            "SELECT concept_name, COUNT(*) AS n FROM error_history "
            "GROUP BY concept_name ORDER BY n DESC, MAX(created_at) DESC "
            "LIMIT ?",
            (TOP_ERRORS_LIMIT,),
        ).fetchall()
    except Exception:
        return []
    return [
        {"concept": str(r["concept_name"] or ""), "occurrences": int(r["n"])}
        for r in rows
    ]


def _next_milestone(store: Any) -> str:
    """Prochain jalon : première étape non terminée du parcours le plus récent."""
    try:
        paths = store._conn.execute(
            "SELECT id, title FROM learning_paths ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
    except Exception:
        return ""
    for prow in paths:
        path = dict(prow)
        try:
            steps = store._conn.execute(
                "SELECT title, status FROM path_steps WHERE path_id = ? "
                "ORDER BY ordinal",
                (path["id"],),
            ).fetchall()
        except Exception:
            continue
        for srow in steps:
            step = dict(srow)
            if str(step.get("status") or "") not in ("completed", "done", "terminé"):
                title = str(step.get("title") or "").strip()
                if title:
                    return f"{path['title']} — {title}"
                return str(path["title"])
        if not steps:
            return str(path["title"])
    return ""
