"""Titres d'import sans suffixe de déduplication (correctif bug prod).

Bug : quand le fichier existe déjà dans `uploads/`, le serveur renomme en
`{stem}-{8hex}{suffix}` (web/server.py `tutor_import`, branche
`dest.exists()`) et le TITRE affiché hérite de ce suffixe
(« reseaux_test-88d7b016 »). Le titre dérive de `p.stem` dans
`LibraryStore.import_document` — le fichier disque doit garder son nom
unique, seul le titre est nettoyé via `strip_dedup_suffix`.

100 % offline : `register_import` seul (aucun embed/LLM, client None).
"""

from __future__ import annotations

from pathlib import Path

from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore, strip_dedup_suffix


def _service(tmp_path: Path) -> TutorService:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    return TutorService(store, None, config)


def _doc(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_strip_dedup_suffix_removes_8hex(tmp_path: Path) -> None:
    assert strip_dedup_suffix("reseaux_test-88d7b016") == "reseaux_test"
    assert strip_dedup_suffix("reseaux_test-88D7B016") == "reseaux_test"  # casse
    assert strip_dedup_suffix("cours-abcdef12") == "cours"


def test_strip_dedup_suffix_leaves_explicit_title_unchanged(tmp_path: Path) -> None:
    assert strip_dedup_suffix("reseaux_test") == "reseaux_test"
    assert strip_dedup_suffix("mon-cours-2024") == "mon-cours-2024"
    assert strip_dedup_suffix("reseaux-test") == "reseaux-test"
    assert strip_dedup_suffix("rapport_final") == "rapport_final"
    # Pas 8 hex → inchangé (7 et 9 chars).
    assert strip_dedup_suffix("cours-abc1234") == "cours-abc1234"
    assert strip_dedup_suffix("cours-abc123456") == "cours-abc123456"
    # Suffixe non-hex → inchangé.
    assert strip_dedup_suffix("cours-zzzzzzzz") == "cours-zzzzzzzz"


def test_second_import_same_name_has_clean_title_and_distinct_path(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    # Simule la branche `dest.exists()` du serveur : 2e upload du même nom
    # renommé `{stem}-{8hex}{suffix}` avec un contenu différent.
    p1 = _doc(tmp_path, "reseaux_test.txt", "contenu pedagogique A " * 30)
    p2 = _doc(tmp_path, "reseaux_test-88d7b016.txt", "contenu pedagogique B " * 30)
    _, book1 = svc.register_import("Reseaux", p1)
    _, book2 = svc.register_import("Reseaux", p2)
    assert book1.source_path != book2.source_path
    assert book1.title == "reseaux_test"
    assert book2.title == "reseaux_test", f"suffixe hex hérité : {book2.title!r}"


def test_presuffixed_upload_name_is_cleaned(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    p = _doc(tmp_path, "cours_python-abcdef12.txt", "contenu pedagogique C " * 30)
    _, book = svc.register_import("Python", p)
    assert book.title == "cours_python"


def test_normal_title_unchanged(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    p = _doc(tmp_path, "thermodynamique.txt", "contenu pedagogique D " * 30)
    _, book = svc.register_import("Physique", p)
    assert book.title == "thermodynamique"
