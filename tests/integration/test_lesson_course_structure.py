"""Structure imposée des cours générés (ancien style pédagogique verrouillé).

Preuves DB : même modèle, deux styles — l'ancien aimé (Objectif,
sections numérotées 1./1.1, blocs code, tableau « Points clés », « Cas
d'usage concrets » Cas 1/2, « Erreurs fréquentes » ❌, Conclusion,
« Mots-clés ») vs le nouveau rejeté (académique, bullets,
« (Mot total : …) »). Le prompt laissait le style au hasard du tirage.

Tests déterministes sur le builder (chaque marqueur exigé dans la
chaîne) + e2e mocké `generate_course` vert. 100 % offline.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import numpy as np

from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.service import TutorService, _build_lesson_prompts
from src.ollama_tutor.tutor.store import LibraryStore


def _course_system() -> str:
    system, _ = _build_lesson_prompts(
        "lesson_course", "Les boucles en Python", ["Les boucles for itèrent."]
    )
    return system


def test_structure_objectif_du_cours() -> None:
    assert "Objectif du cours" in _course_system()


def test_structure_sections_numerotees() -> None:
    system = _course_system()
    assert "numérotées" in system
    assert "1." in system and "1.1" in system


def test_structure_blocs_de_code() -> None:
    system = _course_system()
    assert "```python" in system or "blocs de code" in system


def test_structure_tableau_points_cles() -> None:
    assert "Points clés" in _course_system()


def test_structure_cas_usage_concrets() -> None:
    system = _course_system()
    assert "Cas d'usage concrets" in system
    assert "Cas 1" in system


def test_structure_erreurs_frequentes() -> None:
    system = _course_system()
    assert "Erreurs fréquentes" in system
    assert "❌" in system


def test_structure_conclusion() -> None:
    assert "Conclusion" in _course_system()


def test_structure_mots_cles() -> None:
    assert "Mots-clés" in _course_system()


def test_structure_fourchette_mots() -> None:
    system = _course_system()
    assert "800" in system and "1200" in system


def test_interdiction_meta_remarks() -> None:
    system = _course_system()
    assert "Mot total" in system  # cité dans l'interdiction, jamais à produire
    assert "méta" in system.lower()


def test_interdiction_ids_bruts_corps() -> None:
    system = _course_system()
    assert "identifiants techniques" in system


def test_summary_answer_sans_structure_cours() -> None:
    summary, _ = _build_lesson_prompts("lesson_summary", "X", ["e"])
    answer, _ = _build_lesson_prompts("lesson_answer", "X", ["e"], question="Q")
    for other in (summary, answer):
        assert "Objectif du cours" not in other
        assert "Cas d'usage concrets" not in other


def test_e2e_generate_course_mocked(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Informatique")
    book_id = uuid.uuid4().hex[:8]
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Manuel", "manuel.txt", "txt", hashlib.sha256(b"m").hexdigest(), "indexed", "2026-01-01T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subj.id, book_id)
    )
    store._conn.commit()
    vec = np.zeros(4, dtype=np.float32).tolist()
    store.add_chunks(
        subj.id, book_id, ["Les boucles for en Python itèrent sur des séquences."],
        [vec], model="test-model",
    )
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=0)
    llm_body = " ".join(["Mot de cours LLM authentique sur les boucles."] * 130)
    assert len(llm_body.split()) >= 800

    class FakeTutor:
        def generate_lesson_text(self, kind, notion, excerpts, question=None):
            assert kind == "lesson_course"
            return llm_body

    lesson = LessonDiscussionService(store, tutor_service=FakeTutor())
    disc = lesson.get_or_create_discussion(step.id, "alice")
    course = lesson.generate_course(disc.id)
    assert course["fallback"] is False
    assert "Mot de cours LLM authentique" in course["content"]
