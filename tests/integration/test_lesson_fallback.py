"""Leçon : notion assainie, repli honnête, sources lisibles (correctif bug prod).

Bug prod : notion = NOM DE FICHIER (« Programmer-en-...-d010dd1a »),
contenu générique fourre-tout, extraits sans rapport (facture, mentions
légales), sources en ids bruts, même rendu pour chaque leçon.

100 % offline, Python uniquement (aucun fastapi/textual) :
- notion JAMAIS un nom de fichier (strip extension + suffixe dedup via
  `strip_dedup_suffix`) ; repli sur le titre de l'étape quand la notion
  égale un titre de livre (insensible casse) ;
- fallback LLM indisponible/en échec marqué `fallback: True` (+ mention
  « généré hors-ligne depuis les extraits » en en-tête) ;
- aucun id brut dans le contenu rendu (titres de livres requis via store).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import numpy as np

from src.ollama_tutor.tutor.lesson_discussion import LessonDiscussionService
from src.ollama_tutor.tutor.store import LibraryStore

OFFLINE_MENTION = "généré hors-ligne depuis les extraits"

RELEVANT_CHUNKS = [
    "En Python, programmer une variable consiste à associer un nom à une valeur "
    "référencée en mémoire : x = 3 crée une référence entière nommée x dans le module.",
    "Pour programmer une boucle en Python, on écrit for i in range(5) afin "
    "d'itérer sur une séquence et d'exécuter un bloc d'instructions à chaque passage.",
    "Les fonctions Python se définissent avec def puis se programment en combinant "
    "paramètres, valeur de retour et portée locale des variables déclarées.",
    "Le typage dynamique de Python permet de réaffecter une variable sans déclarer "
    "son type : programmer ainsi reste lisible si les noms choisis sont explicites.",
]

IRRELEVANT_CHUNKS = [
    "Facture numéro 2024-118 : montant total de 1 200 euros, TVA incluse, "
    "payable sous trente jours par virement bancaire au fournisseur.",
    "Mentions légales : ce document est la propriété exclusive de la société, "
    "toute reproduction sans autorisation écrite est strictement interdite.",
]


def _seed_book(
    store: LibraryStore,
    subject_id: str,
    title: str,
    texts: list[str],
) -> str:
    book_id = uuid.uuid4().hex[:8]
    p = store.config_dir / f"{title}.txt"
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            book_id,
            title,
            str(p),
            "txt",
            hashlib.sha256(title.encode()).hexdigest(),
            "indexed",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)",
        (subject_id, book_id),
    )
    store._conn.commit()
    vec = np.random.randn(4).astype(np.float32).tolist()
    store.add_chunks(subject_id, book_id, texts, [vec] * len(texts), model="test-model")
    return book_id


def _setup(tmp_path: Path, *, step_title: str, activity_id: str):
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Informatique")
    book_id = _seed_book(store, subj.id, "Manuel Python", RELEVANT_CHUNKS + IRRELEVANT_CHUNKS)
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", activity_id, step_title, ordinal=0)
    return store, subj, book_id, step


def _word_count(t: str) -> int:
    return len(t.split())


def test_notion_filename_sanitized(tmp_path: Path) -> None:
    store, _subj, book_id, step = _setup(
        tmp_path,
        step_title="Programmer en Python",
        activity_id="Programmer-en-python-d010dd1a",
    )
    svc = LessonDiscussionService(store)
    disc = svc.get_or_create_discussion(step.id, "alice")
    assert "d010dd1a" not in disc.notion_id, f"notion = nom de fichier : {disc.notion_id!r}"
    assert disc.notion_id != "Programmer-en-python-d010dd1a"
    course = svc.generate_course(disc.id)
    assert "d010dd1a" not in course["content"]
    assert book_id not in course["content"], "id brut dans le rendu"
    assert 800 <= _word_count(course["content"]) <= 1200


def test_notion_matching_book_title_falls_back_to_step_title(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Informatique")
    _seed_book(store, subj.id, "Variables", RELEVANT_CHUNKS)
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "Variables", "Les variables en Python", ordinal=0)
    svc = LessonDiscussionService(store)
    disc = svc.get_or_create_discussion(step.id, "alice")
    assert disc.notion_id == "Les variables en Python", f"notion = titre de livre : {disc.notion_id!r}"


def test_fallback_marked_with_relevant_excerpts_and_no_raw_ids(tmp_path: Path) -> None:
    store, _subj, book_id, step = _setup(
        tmp_path,
        step_title="Programmer en Python",
        activity_id="Programmer-en-python-d010dd1a",
    )
    svc = LessonDiscussionService(store)  # pas de LLM → repli honnête
    course = svc.generate_course(disc_id := svc.get_or_create_discussion(step.id, "alice").id)
    assert course["fallback"] is True
    assert OFFLINE_MENTION in course["content"], "mention hors-ligne manquante en en-tête"
    assert course["content"].lstrip().startswith(">"), "mention attendue en en-tête"
    # Extraits pertinents (mots-clés notion), pas le fourre-tout sans rapport.
    assert "python" in course["content"].lower()
    assert "facture" not in course["content"].lower()
    assert "mentions légales" not in course["content"].lower()
    assert book_id not in course["content"]
    for src in course["sources"]:
        assert src["book_id"] == book_id  # sources structurées : id OK hors contenu
    # La synthèse suit le même marquage.
    summary = svc.generate_summary(disc_id)
    assert summary["fallback"] is True
    assert OFFLINE_MENTION in summary["content"]
    assert 150 <= _word_count(summary["content"]) <= 250
    assert book_id not in summary["content"]
    # ask_notion : source lisible (titre), jamais l'id brut.
    answer = svc.ask_notion(disc_id, "c'est quoi programmer ?", "alice")
    assert book_id not in answer["answer"]
    assert "Manuel Python" in answer["answer"]


class _FailingLLM:
    def generate_lesson_text(self, kind: str, notion: str, excerpts: list[str]) -> str:
        raise RuntimeError("LLM down")


class _WorkingLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate_lesson_text(self, kind: str, notion: str, excerpts: list[str]) -> str:
        self.calls.append((kind, notion))
        body = " ".join(
            [f"Contenu-LLM-vérifié sur {notion} paragraphe pédagogique structuré"] * 150
        )
        assert len(body.split()) >= 800
        return body


def test_failing_llm_falls_back_honestly(tmp_path: Path) -> None:
    store, _subj, _book_id, step = _setup(
        tmp_path,
        step_title="Programmer en Python",
        activity_id="Programmer-en-python-d010dd1a",
    )
    svc = LessonDiscussionService(store, tutor_service=_FailingLLM())
    disc = svc.get_or_create_discussion(step.id, "alice")
    course = svc.generate_course(disc.id)
    assert course["fallback"] is True
    assert OFFLINE_MENTION in course["content"]
    assert "Contenu-LLM-vérifié" not in course["content"]


def test_working_llm_returns_llm_content_not_fallback(tmp_path: Path) -> None:
    store, _subj, _book_id, step = _setup(
        tmp_path,
        step_title="Programmer en Python",
        activity_id="Programmer-en-python-d010dd1a",
    )
    llm = _WorkingLLM()
    svc = LessonDiscussionService(store, tutor_service=llm)
    disc = svc.get_or_create_discussion(step.id, "alice")
    course = svc.generate_course(disc.id)
    assert course["fallback"] is False
    assert "Contenu-LLM-vérifié" in course["content"]
    assert OFFLINE_MENTION not in course["content"]
    assert "d010dd1a" not in course["content"]


def test_notion_filename_without_suffix_matches_legacy_book_title(tmp_path: Path) -> None:
    # Ligne livre legacy portant encore le suffixe dedup (importée avant le
    # correctif de strip) : la comparaison normalisée des DEUX côtés doit
    # matcher et retomber sur le titre de l'étape.
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Informatique")
    _seed_book(
        store,
        subj.id,
        "Programmer-en-samusant-avec-Python-pour-les-Nuls-d010dd1a",
        RELEVANT_CHUNKS,
    )
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(
        path.id,
        "concept",
        "Programmer-en-samusant-avec-Python-pour-les-Nuls",
        "S'amuser avec Python",
        ordinal=0,
    )
    svc = LessonDiscussionService(store)
    disc = svc.get_or_create_discussion(step.id, "alice")
    assert disc.notion_id == "S'amuser avec Python", f"notion-fichier v2 : {disc.notion_id!r}"
    # Notion multi-mots issue d'un nom de fichier (underscores, sans suffixe).
    _seed_book(store, subj.id, "Cours Python Avance", RELEVANT_CHUNKS)
    step2 = store.add_path_step(
        path.id, "concept", "cours_python_avance", "Python avancé", ordinal=1
    )
    disc2 = svc.get_or_create_discussion(step2.id, "alice")
    assert disc2.notion_id == "Python avancé", f"multi-mots : {disc2.notion_id!r}"


def test_no_raw_ids_in_course_and_summary_render(tmp_path: Path) -> None:
    store, _subj, book_id, step = _setup(
        tmp_path,
        step_title="Programmer en Python",
        activity_id="notion-python",
    )
    svc = LessonDiscussionService(store)
    disc = svc.get_or_create_discussion(step.id, "alice")
    course = svc.generate_course(disc.id)
    assert book_id not in course["content"], "id brut dans le cours"
    assert "Sources :" in course["content"]
    assert "Manuel Python" in course["content"], "titre de livre attendu dans le pied Sources"
    assert 800 <= _word_count(course["content"]) <= 1200
    summary = svc.generate_summary(disc.id)
    assert book_id not in summary["content"], "id brut dans la synthèse"
    assert "Manuel Python" in summary["content"]
    assert 150 <= _word_count(summary["content"]) <= 250
