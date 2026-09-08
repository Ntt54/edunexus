"""Mémoire compacte inter-sessions (010 P2-Adaptatif, T035).

Adapté de ``autreprojet/OpenTutor-main``
(``services/memory/pipeline.py`` : classify_memory_type regex,
encode_memory avec skip < 10 car, ``pipeline_stages.py`` : consolidate
overlap mots ≥ 0.5 + cosine ≥ 0.85, decay 90 j, generate_teaching_state).

Version locale : 100 % sync + SQLite via ``store._conn`` (la table est
créée à la première utilisation — ``store.py`` intouché, même pattern
que ``review.py``), stdlib seul (``re`` + ``math`` + ``json`` : ni torch
ni sentence-transformers, aucun import UI). Pas d'embeddings stockés
(machine CPU) : la confirmation cosine ne s'applique que si deux
vecteurs sont présents, sinon repli overlap ≥ 0.7 (fidèle à la source).
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_TYPES = ("profile", "knowledge", "plan")

#: Demi-vie unique d'importance : 90 j pour tous les types (source).
DECAY_HALF_LIFE_DAYS = 90

#: Pré-filtre overlap mots (source : pipeline_stages).
OVERLAP_THRESHOLD = 0.5

#: Confirmation cosine sur embeddings (source : pipeline_stages).
COSINE_THRESHOLD = 0.85

#: Sans vecteurs, un overlap élevé suffit (source : pipeline_stages).
HIGH_OVERLAP_FALLBACK = 0.7

_PROFILE_PATTERNS = re.compile(
    r"(i\s+(like|prefer|want|don.?t\s+like|hate|need|am\s+a|feel)|"
    r"my\s+(style|level|weakness|strength|preference)|"
    r"too\s+(fast|slow|detailed|brief|hard|easy)|"
    r"(visual|auditory|hands.?on)\s+learner|"
    # FR : préférences / profil apprenant
    r"(je\s+(préfère|prefere|aime|veux|suis|déteste|deteste)|"
    r"mon\s+(style|niveau|point\s+faible|point\s+fort|préférence|preference)|"
    r"trop\s+(vite|lent|détaillé|detaille|bref|dur|facile)|"
    r"(visuel|auditif|kinesthésique|kinesthesique)))",
    re.IGNORECASE,
)

_PLAN_PATTERNS = re.compile(
    r"(deadline|exam|schedule|assignment|due\s+date|"
    r"study\s+plan|goal|target|timeline|midterm|final|"
    r"week\s+\d|tomorrow|next\s+week|before\s+the|"
    # FR : échéances / objectifs
    r"examen|échéance|echeance|date\s+limite|devoir|planning|objectif|"
    r"cible|demain|semaine\s+prochaine|avant\s+le)",
    re.IGNORECASE,
)


def classify_memory_type(user_message: str, assistant_response: str = "") -> str:
    """Type de mémoire par règles (remplace la classification LLM)."""
    combined = f"{user_message} {assistant_response}"
    if _PROFILE_PATTERNS.search(combined):
        return "profile"
    if _PLAN_PATTERNS.search(combined):
        return "plan"
    return "knowledge"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosinus stdlib (vecteurs de même dimension)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_moment(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _ensure_memories_table(store: Any) -> None:
    """Crée ``conversation_memories`` si absente (idempotent)."""
    store._conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_memories (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            memory_type TEXT NOT NULL DEFAULT 'knowledge',
            importance REAL NOT NULL DEFAULT 0.5,
            embedding TEXT,
            access_count INTEGER NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            dismissed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memories_subject
            ON conversation_memories(subject_id);
        """
    )
    store._conn.commit()


def _new_id(store: Any) -> str:
    try:
        from .models import _uid  # type: ignore[import]

        return _uid()
    except Exception:  # pragma: no cover - repli sans models
        import uuid

        return uuid.uuid4().hex


def _build_summary(user_message: str, assistant_response: str) -> str:
    """Résumé concis sans LLM (tronque + combine, cf. source)."""
    user_part = user_message.strip()[:300]
    assistant_part = assistant_response.strip()[:300]
    if len(assistant_part) < 20:
        return user_part
    return f"L'élève demande : {user_part}\nLe tuteur répond : {assistant_part}"


def encode_memory(
    store: Any,
    subject_id: str,
    user_message: str,
    assistant_response: str = "",
) -> dict[str, Any] | None:
    """Étape 1 : crée une entrée mémoire depuis un tour de conversation.

    Ignore les messages triviaux (< 10 car). Retourne le snapshot ou None.
    """
    _ensure_memories_table(store)
    if len(str(user_message or "").strip()) < 10:
        return None
    summary = _build_summary(str(user_message), str(assistant_response or ""))
    if not summary or len(summary) < 10:
        return None
    mem_type = classify_memory_type(str(user_message), str(assistant_response or ""))
    now = _now_iso()
    mem_id = _new_id(store)
    store._conn.execute(
        "INSERT INTO conversation_memories (id, subject_id, summary,"
        " memory_type, importance, embedding, access_count, metadata,"
        " dismissed_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 0.5, NULL, 0, ?, NULL, ?, ?)",
        (
            mem_id,
            subject_id,
            summary,
            mem_type,
            json.dumps({"source": "rule_based"}, ensure_ascii=False),
            now,
            now,
        ),
    )
    store._conn.commit()
    logger.info("Mémoire encodée (type=%s)", mem_type)
    row = store._conn.execute(
        "SELECT * FROM conversation_memories WHERE id = ?", (mem_id,)
    ).fetchone()
    return dict(row)


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def consolidate_memories(
    store: Any, subject_id: str, now: datetime | None = None
) -> dict[str, int]:
    """Étape 2 : déduplique (overlap + cosine) et applique le decay.

    Retourne ``{"deduped": n, "decayed": m}``.
    """
    _ensure_memories_table(store)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    rows = store._conn.execute(
        "SELECT * FROM conversation_memories WHERE subject_id = ?"
        " AND dismissed_at IS NULL ORDER BY created_at DESC, rowid DESC",
        (subject_id,),
    ).fetchall()
    memories = [dict(r) for r in rows]
    if len(memories) < 2:
        # Pas de paire : decay seul éventuellement.
        return {"deduped": 0, "decayed": _apply_decay(store, memories, moment)}

    removed: set[str] = set()
    merged: list[tuple[dict, dict]] = []
    for i, mem_a in enumerate(memories):
        if mem_a["id"] in removed:
            continue
        words_a = set(str(mem_a["summary"] or "").lower().split())
        if len(words_a) < 3:
            continue
        for mem_b in memories[i + 1 :]:
            if mem_b["id"] in removed:
                continue
            if mem_a["memory_type"] != mem_b["memory_type"]:
                continue
            words_b = set(str(mem_b["summary"] or "").lower().split())
            if len(words_b) < 3:
                continue
            overlap = _overlap(words_a, words_b)
            if overlap < OVERLAP_THRESHOLD:
                continue
            duplicate = False
            emb_a = json.loads(mem_a["embedding"]) if mem_a["embedding"] else None
            emb_b = json.loads(mem_b["embedding"]) if mem_b["embedding"] else None
            if emb_a and emb_b:
                duplicate = cosine_similarity(emb_a, emb_b) >= COSINE_THRESHOLD
            elif overlap >= HIGH_OVERLAP_FALLBACK:
                duplicate = True
            if duplicate:
                # Conserve la plus importante (fidèle à la source).
                if float(mem_b["importance"]) >= float(mem_a["importance"]):
                    keeper, loser = mem_b, mem_a
                else:
                    keeper, loser = mem_a, mem_b
                merged.append((keeper, loser))
                removed.add(loser["id"])

    for keeper, loser in merged:
        keeper["importance"] = min(
            1.0, float(keeper["importance"]) + float(loser["importance"]) * 0.3
        )
        keeper["access_count"] = int(keeper["access_count"] or 0) + int(
            loser["access_count"] or 0
        )
        try:
            meta = json.loads(keeper["metadata"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        meta["merge_count"] = int(meta.get("merge_count", 1)) + 1
        meta["last_merged_at"] = moment.isoformat()
        store._conn.execute(
            "UPDATE conversation_memories SET importance = ?, access_count = ?,"
            " metadata = ?, updated_at = ? WHERE id = ?",
            (
                keeper["importance"],
                keeper["access_count"],
                json.dumps(meta, ensure_ascii=False),
                moment.isoformat(),
                keeper["id"],
            ),
        )
    for loser_id in removed:
        store._conn.execute(
            "DELETE FROM conversation_memories WHERE id = ?", (loser_id,)
        )
    store._conn.commit()

    survivors = [m for m in memories if m["id"] not in removed]
    # Recharge l'importance à jour pour le decay (keeper boosté).
    fresh = store._conn.execute(
        "SELECT * FROM conversation_memories WHERE subject_id = ?"
        " AND dismissed_at IS NULL",
        (subject_id,),
    ).fetchall()
    decayed = _apply_decay(store, [dict(r) for r in fresh], moment)
    _ = survivors
    return {"deduped": len(removed), "decayed": decayed}


def _apply_decay(
    store: Any, memories: list[dict[str, Any]], moment: datetime
) -> int:
    """Decay exponentiel : importance × 0.5^(âge_jours / 90)."""
    decayed = 0
    for mem in memories:
        created = _parse_moment(mem.get("created_at"))
        if created is None:
            continue
        age_days = max((moment - created).total_seconds() / 86400.0, 0.0)
        if age_days <= 0.0:
            continue
        new_importance = float(mem["importance"]) * pow(
            0.5, age_days / DECAY_HALF_LIFE_DAYS
        )
        if new_importance < float(mem["importance"]) - 1e-12:
            store._conn.execute(
                "UPDATE conversation_memories SET importance = ?,"
                " updated_at = ? WHERE id = ?",
                (new_importance, moment.isoformat(), mem["id"]),
            )
            decayed += 1
    if decayed:
        store._conn.commit()
    return decayed


def teaching_state(store: Any, subject_id: str) -> dict[str, Any]:
    """État pédagogique pour la continuité inter-sessions (adapté de
    generate_teaching_state source : forces, faiblesses, prochain sujet,
    activité, maîtrise, urgence de révision — sans graphe loom)."""
    _ensure_memories_table(store)
    pairs = store.get_progress(subject_id)
    scored = [
        (concept.name, float(score) if score is not None else 0.0)
        for concept, score in pairs
    ]
    masteries = [s for _, s in scored]
    avg = sum(masteries) / len(masteries) if masteries else 0.0
    strengths = [name for name, s in sorted(scored, key=lambda x: x[1], reverse=True)[:3] if s >= 60.0]
    weaknesses = [name for name, s in scored if s < 50.0][:5]
    next_topic = weaknesses[0] if weaknesses else None
    days_since: int | None = None
    try:
        row = store._conn.execute(
            "SELECT MAX(last_active_at) AS m FROM tutoring_sessions"
            " WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()
        last = _parse_moment(row["m"]) if row and row["m"] else None
        if last is not None:
            days_since = (datetime.now(timezone.utc) - last).days
    except Exception:  # noqa: BLE001 - sessions absentes ou illisibles
        days_since = None
    today = date.today().isoformat()
    try:
        due_row = store._conn.execute(
            "SELECT COUNT(*) AS c FROM review_schedule rs"
            " JOIN flashcards f ON f.id = rs.flashcard_id"
            " WHERE f.subject_id = ? AND rs.next_due <= ?",
            (subject_id, today),
        ).fetchone()
        urgency = int(due_row["c"]) if due_row else 0
    except Exception:  # noqa: BLE001 - table absente (vieux profil)
        urgency = 0
    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "next_topic": next_topic,
        "days_since_last_session": days_since,
        "avg_mastery": round(avg, 3),
        "mastered_count": sum(1 for s in masteries if s >= 80.0),
        "total_concepts": len(scored),
        "review_urgency": urgency,
    }


def format_resumption_prompt(state: dict[str, Any]) -> str:
    """État → phrase de reprise naturelle (adapté de la source, en FR)."""
    parts: list[str] = []
    days = state.get("days_since_last_session")
    if days is not None and days >= 1:
        parts.append(f"L'élève a étudié pour la dernière fois il y a {days} jour(s).")
    avg = float(state.get("avg_mastery", 0.0) or 0.0)
    mastered = int(state.get("mastered_count", 0) or 0)
    total = int(state.get("total_concepts", 0) or 0)
    if total > 0:
        parts.append(
            f"Maîtrise globale : {avg:.0f}/100 ({mastered}/{total} concepts maîtrisés)."
        )
    strengths = state.get("strengths", []) or []
    if strengths:
        parts.append(f"Forces : {', '.join(str(s) for s in strengths)}.")
    weaknesses = state.get("weaknesses", []) or []
    if weaknesses:
        parts.append(f"À retravailler : {', '.join(str(w) for w in weaknesses)}.")
    urgency = int(state.get("review_urgency", 0) or 0)
    if urgency >= 3:
        parts.append(
            f"{urgency} concepts risquent l'oubli — prioriser la révision."
        )
    next_topic = state.get("next_topic")
    if next_topic:
        parts.append(f"Sujet suivant recommandé : {next_topic}.")
    return " ".join(parts)


__all__ = [
    "COSINE_THRESHOLD",
    "DECAY_HALF_LIFE_DAYS",
    "HIGH_OVERLAP_FALLBACK",
    "MEMORY_TYPES",
    "OVERLAP_THRESHOLD",
    "classify_memory_type",
    "consolidate_memories",
    "cosine_similarity",
    "encode_memory",
    "format_resumption_prompt",
    "teaching_state",
]
