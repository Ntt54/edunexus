"""Notes atomiques liées (012 US3, FR-010 — D8 research).

Une note atomique = UNE idée : ``title`` est une affirmation (pas un
sujet), ``body_own_words`` est rédigé en propres mots (non vide). Les
notes se relient par des liens typés (``précise`` / ``contredit`` /
``mécanisme-de`` / ``exemple-de``) et un plan se **assemble** en
parcourant ces liens depuis une question — en lecture seule.

Le carnet existant (``notebook.py`` : ``notes:string[]``) est inchangé
(compat) : les notes atomiques vivent dans leurs tables dédiées
(``atomic_notes`` / ``atomic_note_links``, migration 012 T004).

UI-framework-free par contrat (aucune dépendance web/TUI) ; SQL brut
via ``store._conn`` comme ``review.py``.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

#: Relations valides entre notes atomiques (data-model 012, 400 sinon).
VALID_RELS = ("précise", "contredit", "mécanisme-de", "exemple-de")

#: Longueur max d'un titre-affirmation (data-model 012, 400 sinon).
TITLE_MAX_LEN = 120

_WORD_RE = re.compile(r"[a-zàâéèêôû0-9]{3,}", re.IGNORECASE)


def _resolve_general_subject(store: Any) -> str:
    """Matière « Général » (find-or-create — même idiome que TutorService)."""
    for s in store.list_subjects():
        if (s.name or "").lower() == "général":
            return s.id
    return store.create_subject("Général").id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> set[str]:
    """Mots significatifs (≥3 lettres) d'un texte, en minuscules."""
    return set(_WORD_RE.findall((text or "").lower()))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_note(
    store: Any,
    title: str,
    body: str,
    *,
    learner_id: str = "",
    subject_id: str = "",
    concept_ids: list[str] | None = None,
    source_refs: list[Any] | None = None,
) -> dict[str, Any]:
    """Crée une note atomique.

    Lève ``ValueError`` si le titre (affirmation) est vide / >120 chars
    ou si le corps (propres mots) est vide — le transport répond 400.
    Lève ``KeyError`` si un ``learner_id``/``subject_id`` explicite est
    inconnu — le transport répond 404.

    Rattachement (FK) : sans ``learner_id`` le premier apprenant (ou
    « Apprenant » créé) est utilisé ; sans ``subject_id`` la matière
    « Général » (find-or-create).
    """
    title = (title or "").strip()
    body = (body or "").strip()
    if not title:
        raise ValueError("Le titre (affirmation) est requis")
    if len(title) > TITLE_MAX_LEN:
        raise ValueError(f"Le titre dépasse {TITLE_MAX_LEN} caractères")
    if not body:
        raise ValueError("Le corps rédigé en propres mots est requis")
    learner_id = (learner_id or "").strip()
    if learner_id:
        if store.get_learner(learner_id) is None:
            raise KeyError(f"Apprenant inconnu : {learner_id}")
    else:
        learners = store.list_learners()
        learner_id = learners[0].id if learners else store.create_learner("Apprenant").id
    subject_id = (subject_id or "").strip()
    if subject_id:
        store.require_subject(subject_id)
    else:
        subject_id = _resolve_general_subject(store)
    note = {
        "id": uuid.uuid4().hex[:12],
        "learner_id": (learner_id or "").strip(),
        "subject_id": (subject_id or "").strip(),
        "title": title,
        "body": body,
        "concept_ids": list(concept_ids or []),
        "source_refs": list(source_refs or []),
        "created_at": _now(),
        "updated_at": _now(),
    }
    store._conn.execute(
        "INSERT INTO atomic_notes (id, learner_id, subject_id, title,"
        " body_own_words, concept_ids, source_refs, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            note["id"],
            note["learner_id"],
            note["subject_id"],
            note["title"],
            note["body"],
            json.dumps(note["concept_ids"], ensure_ascii=False),
            json.dumps(note["source_refs"], ensure_ascii=False),
            note["created_at"],
            note["updated_at"],
        ),
    )
    store._conn.commit()
    return note


def get_note(store: Any, note_id: str) -> dict[str, Any] | None:
    """Retourne une note + ses liens, ou ``None`` si inconnue."""
    row = store._conn.execute(
        "SELECT * FROM atomic_notes WHERE id = ?", (note_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(store, dict(row))


def list_notes(
    store: Any,
    *,
    learner_id: str | None = None,
    subject_id: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les notes (filtres optionnels), avec leurs liens."""
    sql = "SELECT * FROM atomic_notes"
    clauses: list[str] = []
    params: list[Any] = []
    if learner_id:
        clauses.append("learner_id = ?")
        params.append(learner_id)
    if subject_id:
        clauses.append("subject_id = ?")
        params.append(subject_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at ASC"
    rows = store._conn.execute(sql, params).fetchall()
    return [_row_to_dict(store, dict(r)) for r in rows]


def delete_note(store: Any, note_id: str) -> bool:
    """Supprime une note (liens en cascade) ; ``False`` si inconnue."""
    cur = store._conn.execute("DELETE FROM atomic_notes WHERE id = ?", (note_id,))
    store._conn.commit()
    return cur.rowcount > 0


def _row_to_dict(store: Any, row: dict[str, Any]) -> dict[str, Any]:
    try:
        concept_ids = json.loads(row.get("concept_ids") or "[]")
    except (ValueError, TypeError):
        concept_ids = []
    try:
        source_refs = json.loads(row.get("source_refs") or "[]")
    except (ValueError, TypeError):
        source_refs = []
    return {
        "id": row["id"],
        "learner_id": row.get("learner_id") or "",
        "subject_id": row.get("subject_id") or "",
        "title": row.get("title") or "",
        "body": row.get("body_own_words") or "",
        "concept_ids": concept_ids,
        "source_refs": source_refs,
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "links": list_links(store, row["id"]),
    }


# ---------------------------------------------------------------------------
# Liens
# ---------------------------------------------------------------------------


def add_link(store: Any, from_id: str, to_id: str, rel: str) -> dict[str, Any]:
    """Relie deux notes.

    Lève ``ValueError`` si ``rel`` hors enum ou lien réflexif (→ 400),
    ``KeyError`` si une note est inconnue (→ 404).
    """
    rel = (rel or "").strip()
    if rel not in VALID_RELS:
        raise ValueError(
            f"Relation {rel!r} invalide ; attendu l'une de {', '.join(VALID_RELS)}."
        )
    if from_id == to_id:
        raise ValueError("Une note ne peut pas se lier à elle-même")
    for nid in (from_id, to_id):
        row = store._conn.execute(
            "SELECT id FROM atomic_notes WHERE id = ?", (nid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Note inconnue : {nid}")
    store._conn.execute(
        "INSERT OR REPLACE INTO atomic_note_links (from_id, to_id, rel)"
        " VALUES (?, ?, ?)",
        (from_id, to_id, rel),
    )
    store._conn.commit()
    return {"from_id": from_id, "to_id": to_id, "rel": rel}


def list_links(store: Any, note_id: str) -> list[dict[str, Any]]:
    """Liens sortants d'une note."""
    rows = store._conn.execute(
        "SELECT from_id, to_id, rel FROM atomic_note_links WHERE from_id = ?",
        (note_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Assemblage de plan (lecture seule)
# ---------------------------------------------------------------------------


def assemble_plan(
    store: Any,
    question: str,
    *,
    learner_id: str | None = None,
    max_sections: int = 8,
) -> dict[str, Any]:
    """Assemble un plan depuis les liens, à partir d'une question.

    Lecture seule (SELECT uniquement) : la note graine est celle dont le
    titre/corps recouvre le mieux les mots de la question, puis le plan
    suit les liens sortants (parcours en largeur, ``max_sections`` max).
    Lève ``ValueError`` si la question est vide (→ 400).
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("question requise pour assembler un plan")
    notes = list_notes(store, learner_id=learner_id) if learner_id else list_notes(store)
    if not notes:
        return {"question": question, "sections": [], "plan": []}
    qtokens = _tokens(question)
    by_id = {n["id"]: n for n in notes}

    def _score(note: dict[str, Any]) -> tuple[int, int]:
        overlap = len(qtokens & _tokens(note["title"] + " " + note["body"]))
        return (overlap, len(_tokens(note["title"])))

    ranked = sorted(notes, key=_score, reverse=True)
    seed = ranked[0]
    # Parcours en largeur depuis la graine (liens sortants d'abord, puis
    # liens entrants pour ne rien perdre du graphe local).
    outgoing: dict[str, list[dict[str, Any]]] = {}
    incoming: dict[str, list[str]] = {}
    for n in notes:
        outgoing[n["id"]] = list_links(store, n["id"])
        for link in outgoing[n["id"]]:
            incoming.setdefault(link["to_id"], []).append(n["id"])
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    queue = [seed["id"]]
    while queue and len(sections) < max(1, max_sections):
        nid = queue.pop(0)
        if nid in seen or nid not in by_id:
            continue
        seen.add(nid)
        note = by_id[nid]
        link = None
        if nid != seed["id"]:
            # Relation ayant mené ici (sortante de la graine ou entrante).
            for cand in outgoing.get(seed["id"], []):
                if cand["to_id"] == nid:
                    link = cand["rel"]
                    break
        sections.append(
            {
                "id": note["id"],
                "title": note["title"],
                "body": note["body"],
                "rel": link,
            }
        )
        for l in outgoing.get(nid, []):
            if l["to_id"] not in seen:
                queue.append(l["to_id"])
        for pred in incoming.get(nid, []):
            if pred not in seen:
                queue.append(pred)
    plan = [f"{i + 1}. {s['title']}" + (f" ({s['rel']})" if s["rel"] else "")
            for i, s in enumerate(sections)]
    return {"question": question, "sections": sections, "plan": plan}


__all__ = [
    "VALID_RELS",
    "TITLE_MAX_LEN",
    "create_note",
    "get_note",
    "list_notes",
    "delete_note",
    "add_link",
    "list_links",
    "assemble_plan",
]
