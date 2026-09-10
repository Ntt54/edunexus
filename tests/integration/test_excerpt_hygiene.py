"""Hygiène des extraits avant génération de leçon (parasites prod).

Constat : promos (`Clique ici https://t.me/...`), mentions légales/copyright
(ISBN, « tous droits réservés »), gardes/sommaires deviennent des ITEMS DE
COURS, et les mêmes extraits se répètent 3× dans le rendu.

100 % offline : fonctions pures + `generate_course` (fallback déterministe
et chemin LLM mocké). Le filtre s'applique AVANT prompt ET affichage,
sans toucher à l'extraction/indexation (seule la SÉLECTION pour leçons).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.ollama_tutor.tutor.lesson_discussion import (
    LessonDiscussionService,
    dedupe_excerpts,
    is_junk_excerpt,
    select_lesson_chunks,
)
from src.ollama_tutor.tutor.store import LibraryStore


PROMO = "Clique ici https://t.me/promo_python pour recevoir ton ebook gratuit !"
LEGAL = "ISBN 978-2-12345-678-9. © Éditions Exemple, tous droits réservés."
COVER = "Éditions Exemple — Paris, 2024. Page de garde."
TOC = "Sommaire : 1. Introduction 2. Chapitre un 3. Conclusion"
PEDAGOGIC = "En Python, l'affectation se fait avec le signe = : x = 3."
PEDAGOGIC_CHAPTER = "Un tuple est une séquence immuable, par exemple (1, 2)."


# ---------------------------------------------------------------------------
# is_junk_excerpt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        PROMO,
        "Rejoignez notre canal Telegram t.me/cours_gratuits dès maintenant",
        LEGAL,
        "Copyright © 2023, mentions légales et dépôt légal.",
        COVER,
        TOC,
        "Table des matières : Avant-propos, Chapitre 1, Index",
        "Quatrième de couverture : le meilleur livre de l'année.",
    ],
)
def test_junk_filtered(text: str) -> None:
    assert is_junk_excerpt(text) is True


@pytest.mark.parametrize(
    "text",
    [
        PEDAGOGIC,
        PEDAGOGIC_CHAPTER,
        "x = 3",
        "Définition : une variable associe un nom à une valeur.",
        "Exemple : for i in range(5): print(i) affiche 0 à 4.",
    ],
)
def test_pedagogic_kept(text: str) -> None:
    assert is_junk_excerpt(text) is False


def test_junk_never_raises_on_weird_input() -> None:
    assert is_junk_excerpt("") is False
    assert is_junk_excerpt("   ") is False
    assert is_junk_excerpt(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# dedupe_excerpts
# ---------------------------------------------------------------------------


def test_dedupe_exact_and_case_duplicates() -> None:
    texts = [PEDAGOGIC, PEDAGOGIC, "  " + PEDAGOGIC.upper() + "  ", "Autre contenu."]
    out = dedupe_excerpts(texts)
    assert out == [PEDAGOGIC, "Autre contenu."]


def test_dedupe_near_identical() -> None:
    a = "En Python, l'affectation se fait avec le signe = : x = 3."
    b = "En Python  l'affectation se fait avec le signe = : x = 3 !"
    out = dedupe_excerpts([a, b, "Contenu différent ici."])
    assert len(out) == 2
    assert out[0] == a  # premier gardé, ordre préservé


def test_dedupe_keeps_distinct() -> None:
    texts = ["Les variables stockent des valeurs.", "Les boucles répètent des actions."]
    assert dedupe_excerpts(texts) == texts


def test_dedupe_empty() -> None:
    assert dedupe_excerpts([]) == []


# ---------------------------------------------------------------------------
# select_lesson_chunks
# ---------------------------------------------------------------------------


def _chunk(text: str, chapter: str = "", section: str = "") -> dict:
    return {"text": text, "chapter": chapter, "section": section, "book_id": "b1"}


def test_select_removes_junk_and_dupes() -> None:
    chunks = [
        _chunk(PROMO),
        _chunk(PEDAGOGIC, chapter="Bases"),
        _chunk(PEDAGOGIC, chapter="Bases"),
        _chunk(LEGAL),
    ]
    out = select_lesson_chunks(chunks)
    assert [c["text"] for c in out] == [PEDAGOGIC]


def test_select_never_empty_when_input_exists() -> None:
    out = select_lesson_chunks([_chunk(PROMO), _chunk(LEGAL)])
    assert len(out) == 1, "tout filtré ⇒ garder le moins-pire"


def test_select_empty_in_empty_out() -> None:
    assert select_lesson_chunks([]) == []


def test_select_prefers_chapters_over_cover() -> None:
    chunks = [_chunk(COVER), _chunk(PEDAGOGIC_CHAPTER, chapter="Tuples")]
    out = select_lesson_chunks(chunks)
    assert out[0]["text"] == PEDAGOGIC_CHAPTER


def test_select_never_raises() -> None:
    out = select_lesson_chunks([{}, {"text": None}, {"text": 123}])
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# e2e generate_course (fallback + LLM mocké)
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> LibraryStore:
    return LibraryStore(tmp_path / "config")


def _seed_course(store: LibraryStore, subject_id: str) -> str:
    book_id = uuid.uuid4().hex[:8]
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Python", "python.txt", "txt", hashlib.sha256(b"py").hexdigest(), "indexed", "2026-08-31T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subject_id, book_id)
    )
    store._conn.commit()
    vec = np.zeros(4, dtype=np.float32).tolist()
    texts = [
        {"text": PROMO, "chapter": "", "section": ""},
        {"text": PEDAGOGIC, "chapter": "Bases", "section": "Affectation"},
        {"text": PEDAGOGIC, "chapter": "Bases", "section": "Affectation"},
        {"text": PEDAGOGIC_CHAPTER, "chapter": "Tuples", "section": ""},
        {"text": LEGAL, "chapter": "", "section": ""},
    ]
    store.add_chunks(subject_id, book_id, texts, [vec] * len(texts), model="test-model")
    return book_id


def _discussion(store: LibraryStore, subject_id: str, title: str = "Affectation"):
    path = store.create_learning_path(subject_id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-affect", title, ordinal=0)
    svc = LessonDiscussionService(store)
    return svc, svc.get_or_create_discussion(step.id, "alice")


def test_e2e_fallback_has_no_promo_no_triplication(store: LibraryStore) -> None:
    subj = store.create_subject("Python")
    _seed_course(store, subj.id)
    svc, disc = _discussion(store, subj.id)
    result = svc.generate_course(disc.id, "alice")
    content = result["content"]
    assert "Clique ici" not in content
    assert "t.me" not in content
    assert "tous droits" not in content
    # Pas de triplication : chaque extrait source n'est listé qu'1× dans
    # la section d'ancrage (le padding 800 mots cyclique, contrat à part,
    # vit après "## 2.").
    anchor = content.split("## 2.")[0]
    assert anchor.count("l'affectation se fait avec le signe") == 1
    # L'ancrage pédagogique survit.
    assert "x = 3" in content or "affectation" in content


def test_e2e_mocked_llm_receives_clean_excerpts(store: LibraryStore) -> None:
    subj = store.create_subject("Python")
    _seed_course(store, subj.id)
    seen: dict = {}

    class FakeTutor:
        def generate_lesson_text(self, kind, notion, excerpts, question=None):
            seen["excerpts"] = list(excerpts)
            seen["kind"] = kind
            return " ".join(["mot"] * 850)

    svc, disc = _discussion(store, subj.id)
    svc.tutor_service = FakeTutor()  # type: ignore[assignment]
    result = svc.generate_course(disc.id, "alice")
    assert seen["kind"] == "lesson_course"
    joined = "\n".join(seen["excerpts"])
    assert "Clique ici" not in joined
    assert "tous droits" not in joined
    assert result["fallback"] is False
    assert "Clique ici" not in result["content"]
