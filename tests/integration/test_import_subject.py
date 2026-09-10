"""Inférence de domaine à l'import (correction bug prod).

Bug : importer sans domaine créait un NOUVEAU domaine nommé d'après le
fichier au lieu de classer. 100 % offline : `register_import` seul
(aucun embed/LLM, client None).

Contrat de `resolve_import_subject` :
- nom explicite non vide → comportement actuel (match insensible à la
  casse, sinon création) ;
- vide/None → (a) recouvrement lexical stem ↔ domaines existants
  (≥1 token significatif de ≥4 lettres) → rattache ; (b) sinon bac
  UNIQUE et stable « Non classé » (créé une fois, réutilisé — JAMAIS
  un domaine par fichier, JAMAIS de nom issu du nom de fichier).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _mock_subject_llm(svc: TutorService, payload=None, exc=None, calls=None):
    """Mocke le choix LLM du domaine (transport injectable, 100 % offline)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(json.loads(request.content))
        if exc is not None:
            raise exc
        return httpx.Response(
            200, json={"message": {"content": payload}}, request=request
        )

    svc.lesson_http_transport = httpx.MockTransport(handler)


def _service(tmp_path: Path) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    return TutorService(store, None, config)


def _doc(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_text("contenu pedagogique " * 30, encoding="utf-8")
    return str(p)


def test_two_files_without_subject_share_single_unclassified(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    sid1, book1 = svc.register_import(None, _doc(tmp_path, "rapport_stage_dut.txt"))
    sid2, book2 = svc.register_import("", _doc(tmp_path, "compte_rendu_reunion.txt"))
    assert sid1 == sid2, "un seul bac « Non classé » partagé"
    subjects = svc.store.list_subjects()
    assert len(subjects) == 1
    assert subjects[0].name == "Non classé"
    assert subjects[0].id == sid1
    # Aucun domaine nommé d'après un fichier.
    names = [s.name.lower() for s in subjects]
    assert not any("rapport" in n or "compte" in n for n in names)
    assert not any(n == "général" for n in names)
    # Les deux livres sont rattachés au bac.
    assert svc.store.get_book_subject_id(book1.id) == sid1
    assert svc.store.get_book_subject_id(book2.id) == sid2


def test_unclassified_bin_reused_not_recreated(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    sid1, _ = svc.register_import(None, _doc(tmp_path, "fichier_a.txt"))
    sid2, _ = svc.register_import(None, _doc(tmp_path, "fichier_b.txt"))
    sid3, _ = svc.register_import("   ", _doc(tmp_path, "fichier_c.txt"))
    assert sid1 == sid2 == sid3
    assert [s.name for s in svc.store.list_subjects()] == ["Non classé"]


def test_lexical_overlap_joins_existing_subject(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    python = svc.store.create_subject("Python")
    svc.store.create_subject("Histoire")
    sid, book = svc.register_import(None, _doc(tmp_path, "cours_python_avance.txt"))
    assert sid == python.id, "« python » dans le fichier ⇒ domaine « Python »"
    assert svc.store.get_book_subject_id(book.id) == python.id
    assert len(svc.store.list_subjects()) == 2, "aucun domaine créé"


def test_lexical_overlap_case_insensitive(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    maths = svc.store.create_subject("Mathématiques")
    sid, _ = svc.register_import(None, _doc(tmp_path, "exercices_MATHEMATIQUES_chap2.txt"))
    assert sid == maths.id


def test_short_tokens_do_not_match(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.store.create_subject("Art")
    # Mock null : le LLM ne tranche rien, reste 100 % offline.
    _mock_subject_llm(svc, payload='{"subject": null}')
    sid, _ = svc.register_import(None, _doc(tmp_path, "art_modeme.txt"))
    # « art » (< 4 lettres) ne suffit pas ⇒ bac « Non classé ».
    subj = svc.store.get_subject(sid)
    assert subj is not None and subj.name == "Non classé"


def test_explicit_subject_unchanged(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    sid, book = svc.register_import("Physique", _doc(tmp_path, "quelconque.txt"))
    assert svc.store.get_subject(sid) is not None
    assert svc.store.get_subject(sid).name == "Physique"
    assert svc.store.get_book_subject_id(book.id) == sid
    # Match insensible à la casse sur domaine existant.
    sid2, _ = svc.register_import("physique", _doc(tmp_path, "autre.txt"))
    assert sid2 == sid  # même domaine réutilisé


def test_explicit_subject_case_insensitive_reuse(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    sid1, _ = svc.register_import("Chimie", _doc(tmp_path, "doc_a.txt"))
    sid2, _ = svc.register_import("CHIMIE", _doc(tmp_path, "doc_b.txt"))
    assert sid1 == sid2
    assert len(svc.store.list_subjects()) == 1


def test_resolve_import_subject_direct(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    # Vide/None sans domaines ⇒ bac stable.
    first = svc.resolve_import_subject(None, _doc(tmp_path, "x.txt"))
    second = svc.resolve_import_subject("", _doc(tmp_path, "y.txt"))
    assert first == second
    assert svc.store.get_subject(first).name == "Non classé"
    # Explicite ⇒ création/match classique.
    third = svc.resolve_import_subject("Biologie", None)
    assert svc.store.get_subject(third).name == "Biologie"


# ---------------------------------------------------------------------------
# Choix LLM parmi les matières existantes (dernier recours, jamais de création)
# ---------------------------------------------------------------------------


def test_llm_picks_python_among_existing(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    python = svc.store.create_subject("Python")
    svc.store.create_subject("Réseaux")
    _mock_subject_llm(svc, payload='{"subject": "Python"}')
    # Aucun token commun : ni "notes" ni "cours" ne recoupe python/réseaux.
    sid, book = svc.register_import(None, _doc(tmp_path, "notes_de_cours.txt"))
    assert sid == python.id
    assert svc.store.get_book_subject_id(book.id) == python.id
    assert len(svc.store.list_subjects()) == 2, "aucune matière créée par l'import"


def test_llm_null_falls_back_to_unclassified(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.store.create_subject("Python")
    svc.store.create_subject("Réseaux")
    _mock_subject_llm(svc, payload='{"subject": null}')
    sid, _ = svc.register_import(None, _doc(tmp_path, "notes_de_cours.txt"))
    subj = svc.store.get_subject(sid)
    assert subj is not None and subj.name == "Non classé"


def test_llm_invalid_or_unknown_falls_back(tmp_path: Path) -> None:
    for idx, payload in enumerate(
        (
            "pas du json",
            '{"autre": 1}',
            '{"subject": "Inexistant"}',
            '{"subject": ""}',
            '{"subject": "  "}',
            "",
        )
    ):
        case_dir = tmp_path / f"cas_{idx}"
        case_dir.mkdir()
        svc = _service(case_dir)
        svc.store.create_subject("Python")
        _mock_subject_llm(svc, payload=payload)
        sid, _ = svc.register_import(None, _doc(case_dir, "notes_de_cours.txt"))
        subj = svc.store.get_subject(sid)
        assert subj is not None and subj.name == "Non classé", payload
        assert len(svc.store.list_subjects()) == 2, payload


def test_llm_failure_falls_back_silently(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.store.create_subject("Python")
    _mock_subject_llm(svc, exc=httpx.ConnectError("panne réseau"))
    sid, _ = svc.register_import(None, _doc(tmp_path, "notes_de_cours.txt"))
    subj = svc.store.get_subject(sid)
    assert subj is not None and subj.name == "Non classé"


def test_lexical_first_no_llm_call(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    python = svc.store.create_subject("Python")
    svc.store.create_subject("Histoire")
    calls: list = []
    # Le mock répondrait "Histoire" : s'il est appelé, le rattachement suit.
    _mock_subject_llm(svc, payload='{"subject": "Histoire"}', calls=calls)
    sid, _ = svc.register_import(None, _doc(tmp_path, "cours_python_avance.txt"))
    assert sid == python.id, "recouvrement lexical gratuit d'abord"
    assert calls == [], "LLM seulement en dernier recours (zéro appel réseau)"


def test_explicit_never_calls_llm(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.store.create_subject("Python")
    calls: list = []
    _mock_subject_llm(svc, payload='{"subject": "Python"}', calls=calls)
    sid, _ = svc.register_import("Physique", _doc(tmp_path, "quelconque.txt"))
    assert svc.store.get_subject(sid).name == "Physique"
    assert calls == []


def test_no_subject_created_by_llm_pick(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.store.create_subject("Python")
    svc.store.create_subject("Réseaux")
    before = {s.id for s in svc.store.list_subjects()}
    _mock_subject_llm(svc, payload='{"subject": "Rust"}')
    sid, _ = svc.register_import(None, _doc(tmp_path, "notes_de_cours.txt"))
    after = {s.id for s in svc.store.list_subjects()}
    # "Rust" inconnu ⇒ Non classé (créé, c'est le bac), rien d'autre.
    assert after - before == {sid}
    assert svc.store.get_subject(sid).name == "Non classé"


def test_llm_skipped_without_candidates(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    calls: list = []
    _mock_subject_llm(svc, payload='{"subject": "Python"}', calls=calls)
    sid, _ = svc.register_import(None, _doc(tmp_path, "notes_de_cours.txt"))
    assert svc.store.get_subject(sid).name == "Non classé"
    assert calls == [], "aucun candidat ⇒ aucun appel LLM"
