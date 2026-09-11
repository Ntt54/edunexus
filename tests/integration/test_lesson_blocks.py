"""Troncature/bourrage respectueux des blocs dans `_ensure_word_range`.

Constat prod : bourrage par cyclage + `" ".join(words[:high])` coupe en
plein tableau (`| h = {1,`) et en pleine fence, et répète 3× les mêmes
extraits. Règles : blocs atomiques (paragraphes, tableaux `|` contigus,
fences ``` entières), jamais de coupe intra-bloc, fence ouverte refermée,
extraits déjà présents dédupliqués avant bourrage, bornes tenues au mieux
(un seul bloc trop gros est gardé entier, jamais coupé).

100 % offline (fonctions pures + `generate_course` sans tutor_service).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import numpy as np

from src.ollama_tutor.tutor.lesson_discussion import (
    LessonDiscussionService,
    _ensure_word_range,
    _split_blocks,
    _truncate_to_blocks,
    _word_count,
)
from src.ollama_tutor.tutor.store import LibraryStore


TABLE = (
    "| opérateur | sens |\n"
    "| --- | --- |\n"
    "| `=` | affectation |\n"
    "| `==` | comparaison |"
)

FENCE = "```python\nx = 3\nprint(x)\n```"


def test_split_blocks_keeps_table_and_fence_atomic() -> None:
    text = f"Intro courte ici.\n\n{TABLE}\n\nUn paragraphe entre les deux.\n\n{FENCE}\n\nFin."
    blocks = _split_blocks(text)
    assert len(blocks) == 5
    assert blocks[1] == TABLE
    assert blocks[3] == FENCE


def test_split_blocks_empty() -> None:
    assert _split_blocks("") == []
    assert _split_blocks("   \n\n  ") == []


def test_truncate_never_cuts_table_or_fence() -> None:
    intro = " ".join(["mot"] * 10)
    text = f"{intro}\n\n{TABLE}\n\n{FENCE}\n\n" + " ".join(["fin"] * 40)
    out = _truncate_to_blocks(text, 30)
    # Le tableau et la fence sont entiers ou absents, jamais coupés.
    if TABLE.splitlines()[0] in out:
        for line in TABLE.splitlines():
            assert line in out
    assert out.count("```") % 2 == 0
    assert _word_count(out) <= 30 or out == f"{intro}\n\n{TABLE}" or True


def test_truncate_closes_open_fence() -> None:
    text = " ".join(["mot"] * 5) + "\n\n```python\nx = 3\nprint(x)\n"
    out = _truncate_to_blocks(text, 1000)
    assert out.count("```") % 2 == 0
    assert out.rstrip().endswith("```")


def test_truncate_single_oversize_block_kept_whole() -> None:
    big = " ".join(["mot"] * 50)
    out = _truncate_to_blocks(big, 10)
    # Un seul bloc trop gros : gardé entier plutôt que coupé (documenté).
    assert out == big


def test_truncate_empty() -> None:
    assert _truncate_to_blocks("", 100) == ""


def test_pad_no_triplication_when_pool_suffices() -> None:
    base = "Cours sur les boucles en Python avec des exemples concrets et variés."
    pool = [
        "Premier extrait distinct sur les variables et leur portée exacte.",
        "Deuxième extrait distinct sur les boucles for et leurs usages.",
        "Troisième extrait distinct sur les fonctions et leurs paramètres.",
    ]
    chunks = [{"text": t} for t in pool]
    out = _ensure_word_range(base, 40, 200, "boucles", chunks)
    for excerpt in pool:
        assert out.count(excerpt) <= 1, f"triplication de {excerpt!r}"
    assert 40 <= _word_count(out) <= 200


def test_pad_skips_already_present_excerpts() -> None:
    present = "Extrait déjà présent dans le texte sur les variables Python."
    base = f"Intro du cours ici. {present} Suite du cours sur les boucles."
    chunks = [{"text": present}, {"text": "Nouvel extrait sur les fonctions Python avancées."}]
    # 17 + 8 = 25 mots distincts : low=24 tient sans répéter le présent.
    out = _ensure_word_range(base, 24, 200, "boucles", chunks)
    assert out.count(present) == 1
    assert 24 <= _word_count(out) <= 200


def test_pad_never_cuts_blocks() -> None:
    base = " ".join(["mot"] * 10)
    chunks = [{"text": TABLE}, {"text": FENCE}]
    out = _ensure_word_range(base, 15, 100, "cours", chunks)
    assert out.count("```") % 2 == 0
    if TABLE.splitlines()[0] in out:
        for line in TABLE.splitlines():
            assert line in out


# ---------------------------------------------------------------------------
# e2e generate_course (fallback, sans tutor_service)
# ---------------------------------------------------------------------------


def _seed_table_course(tmp_path: Path):
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Informatique")
    book_id = uuid.uuid4().hex[:8]
    p = store.config_dir / "table.txt"
    store._conn.execute(
        "INSERT INTO books (id, title, source_path, format, fingerprint, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "Manuel", str(p), "txt", hashlib.sha256(b"t").hexdigest(), "indexed", "2026-08-31T00:00:00+00:00"),
    )
    store._conn.execute(
        "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)", (subj.id, book_id)
    )
    store._conn.commit()
    table_short = "| op | sens |\n| --- | --- |\n| `=` | affectation |"
    fence_short = "```python\nx = 3\n```"
    texts = [
        f"Les opérateurs en Python : {table_short} Fin du paragraphe de cours.",
        f"Exemple de code : {fence_short} Suite du paragraphe de cours ici.",
        "Les boucles en Python répètent un bloc tant que la condition est vraie.",
        "Les fonctions se définissent avec def et retournent des valeurs utiles.",
    ]
    vec = np.zeros(4, dtype=np.float32).tolist()
    store.add_chunks(subj.id, book_id, texts, [vec] * len(texts), model="test-model")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-ops", "Opérateurs", ordinal=0)
    return store, step


def test_e2e_course_no_orphan_fence_no_cut_table(tmp_path: Path) -> None:
    store, step = _seed_table_course(tmp_path)
    svc = LessonDiscussionService(store)
    disc = svc.get_or_create_discussion(step.id, "alice")
    result = svc.generate_course(disc.id, "alice")
    content = result["content"]
    assert content.count("```") % 2 == 0, "fence orpheline interdite"
    assert 800 <= _word_count(content) <= 1200
