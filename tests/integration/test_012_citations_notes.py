"""T021 Integration: citations inline + assemblage de plan (012 US3).

012-real-learning-packs, FR-009/FR-010 :
- citations inline : chaque affirmation factuelle d'une réponse de leçon
  porte sa source cliquable → le paragraphe exact du chunk (excerpt
  identique au texte indexé, book_id/chapitre/page rattachés) ;
- assemblage de plan : 5 notes atomiques liées → plan ordonné depuis une
  question, lecture seule (aucun effet de bord) ;
- replanification plafonnée des rappels US1 réutilisée par review.py.

100 % offline : LibraryStore tmp, chunks indexés en dur, LLM jamais appelé
(repli déterministe de LessonDiscussionService).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta
from pathlib import Path

from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.models import Flashcard
from src.ollama_tutor.tutor.review import ReviewScheduler
from src.ollama_tutor.tutor.store import LibraryStore


CHUNK_TEXTS = [
    "La photosynthèse convertit la lumière en glucose dans les chloroplastes des plantes vertes.",
    "La chlorophylle capte les photons et libère de l'oxygène à partir de l'eau absorbée.",
]

LEARNER_ID = "learner_test"


def _seed_lesson(tmp_path: Path) -> tuple[LibraryStore, str, str]:
    """Matière + livre + chunks indexés + parcours/étape + discussion."""
    store = LibraryStore(tmp_path / "config")
    try:
        learner = store.create_learner("Awa")
        learner_id = learner.id
    except Exception:
        store._conn.execute(
            "INSERT OR IGNORE INTO learner_profiles (id, name, avatar, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (LEARNER_ID, "Awa", "", "2026-08-31T00:00:00+00:00", "2026-08-31T00:00:00+00:00"),
        )
        store._conn.commit()
        learner_id = LEARNER_ID
    subject = store.create_subject("SVT", learner_id=learner_id)
    book_id = uuid.uuid4().hex[:8]
    book_path = tmp_path / "photosynthese.txt"
    book_path.write_text(CHUNK_TEXTS[0], encoding="utf-8")
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Manuel SVT", str(book_path), "txt",
         hashlib.sha256(b"svt").hexdigest(), "indexed", "2026-08-31T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subject.id, book_id)
    )
    store._conn.commit()
    vec = [0.25, 0.5, 0.75, 1.0]
    store.add_chunks(
        subject.id,
        book_id,
        [{"text": CHUNK_TEXTS[0], "chapter": "La photosynthèse", "page": 12},
         {"text": CHUNK_TEXTS[1], "chapter": "La photosynthèse", "page": 13}],
        [vec, vec],
        model="test-model",
    )
    path = store.create_learning_path(subject.id, "Parcours SVT")
    step = store.add_path_step(path.id, "concept", "notion-photosynthese",
                               "Photosynthèse", ordinal=0)
    disc = store.get_or_create_lesson_discussion(step.id, learner_id)
    return store, disc.id, learner_id


def test_answer_sources_point_to_exact_indexed_paragraph(tmp_path: Path):
    store, disc_id, learner_id = _seed_lesson(tmp_path)
    svc = LessonDiscussionService(store)
    result = svc.ask_notion(disc_id, "comment la photosynthèse fabrique le glucose ?", learner_id)
    sources = result["sources"]
    assert len(sources) >= 1
    for src in sources:
        # Source cliquable : identifiants + localisation + extrait exact.
        assert src["book_id"]
        assert src["chapter"] == "La photosynthèse"
        assert src["excerpt"] in (CHUNK_TEXTS[0][:200], CHUNK_TEXTS[1][:200])
    assert sources[0]["excerpt"] in (CHUNK_TEXTS[0][:200], CHUNK_TEXTS[1][:200])
    # La réponse cite sa source (titre lisible, jamais d'id brut).
    assert "Manuel SVT" in result["answer"]


def test_answer_without_chunks_carries_no_sources(tmp_path: Path):
    store = LibraryStore(tmp_path / "config2")
    try:
        learner = store.create_learner("Ibrahim")
        learner_id = learner.id
    except Exception:
        learner_id = LEARNER_ID
    subject = store.create_subject("Histoire", learner_id=learner_id)
    path = store.create_learning_path(subject.id, "Parcours Histoire")
    step = store.add_path_step(path.id, "concept", "notion-empires", "Empires", ordinal=0)
    disc = store.get_or_create_lesson_discussion(step.id, learner_id)
    svc = LessonDiscussionService(store)
    result = svc.ask_notion(disc.id, "quels empires ont existé ?", learner_id)
    assert result["sources"] == []


# ---------------------------------------------------------------------------
# Assemblage de plan depuis les liens (lecture seule)
# ---------------------------------------------------------------------------


def test_assemble_plan_from_linked_notes_read_only(tmp_path: Path):
    from src.ollama_tutor.tutor import notes as notes_mod

    store = LibraryStore(tmp_path / "config3")
    seed = notes_mod.create_note(
        store, "La photosynthèse produit du glucose",
        "les plantes fabriquent leur sucre avec la lumière")
    linked = []
    for title, body, rel in (
        ("La chlorophylle capte la lumière", "le pigment vert absorbe les photons", "mécanisme-de"),
        ("Le CO2 entre par les stomates", "les pores des feuilles laissent passer le gaz", "précise"),
        ("L'eau monte par la sève brute", "les racines pompent et la tige conduit", "précise"),
        ("Le glucose stocké donne de l'amidon", "la plante met son sucre en réserve", "exemple-de"),
    ):
        note = notes_mod.create_note(store, title, body)
        notes_mod.add_link(store, seed["id"], note["id"], rel)
        linked.append(note)
    count_before = len(notes_mod.list_notes(store))
    plan = notes_mod.assemble_plan(store, "Comment la photosynthèse produit du glucose ?")
    assert plan["question"]
    assert len(plan["sections"]) >= 3
    titles = [s["title"] for s in plan["sections"]]
    assert seed["title"] in titles
    assert any(n["title"] in titles for n in linked)
    # Lecture seule : l'assemblage ne crée ni note ni lien.
    assert len(notes_mod.list_notes(store)) == count_before


# ---------------------------------------------------------------------------
# Replanification plafonnée des rappels US1 (reuse review.py ← planner.py)
# ---------------------------------------------------------------------------


def test_review_reuses_capped_reminder_replan(tmp_path: Path):
    store = LibraryStore(tmp_path / "config4")
    subject = store.create_subject("Maths")
    concept = store.create_concept(subject.id, "Addition")
    for i in range(6):
        card = Flashcard(
            id=f"card-{i}", subject_id=subject.id, concept_id=concept.id,
            level="beginner", question=f"{i} + {i} = ?", answer=str(2 * i),
            source_hash=f"h{i}",
        )
        store.add_flashcard(card)
    past = (date.today() - timedelta(days=9)).isoformat()
    for i in range(6):
        store._conn.execute(
            "UPDATE review_schedule SET next_due = ? WHERE flashcard_id = ?",
            (past, f"card-{i}"),
        )
    store._conn.commit()
    sched = ReviewScheduler(store)
    replan = sched.recompacted_due(subject.id)
    assert replan["capped"] is True
    total = sum(len(day["items"]) for day in replan["days"])
    assert total == 6
    cap = replan["daily_cap"]
    for day in replan["days"]:
        assert len(day["items"]) <= cap
