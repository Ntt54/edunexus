"""Unit tests : provenance d'embedding par chunk (005-suite).

Chaque chunk enregistre le modèle qui a produit son vecteur ; la retrieval
ne charge que les chunks du modèle courant ; ``stale_books`` signale les
livres à ré-indexer ; ``update_chunks_embedding`` bascule la provenance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def _seed_two_books(store: LibraryStore, tmp_path: Path):
    subject = store.create_subject("Informatique")
    pa = tmp_path / "a.txt"
    pa.write_text("java contenu " * 20, encoding="utf-8")
    pb = tmp_path / "b.txt"
    pb.write_text("reseau contenu " * 20, encoding="utf-8")
    book_a = store.import_document(subject.id, pa)
    book_b = store.import_document(subject.id, pb)
    store.add_chunks(subject.id, book_a.id,
                     ["chunk a1", "chunk a2"], [[1.0, 0.0], [0.5, 0.5]],
                     "ancien-modele")
    store.add_chunks(subject.id, book_b.id,
                     ["chunk b1"], [[0.0, 1.0]], "nouveau-modele")
    return subject.id, book_a.id, book_b.id


def test_add_chunks_records_model(store: LibraryStore, tmp_path: Path):
    sid, a, _ = _seed_two_books(store, tmp_path)
    rows = store.get_indexed_chunks(sid, model="ancien-modele")
    assert {r["book_id"] for r in rows} == {a}
    assert all(r["embedding_model"] == "ancien-modele" for r in rows)


def test_filter_excludes_other_model_chunks(store: LibraryStore, tmp_path: Path):
    sid, a, b = _seed_two_books(store, tmp_path)
    rows_new = store.get_indexed_chunks(sid, model="nouveau-modele")
    assert {r["book_id"] for r in rows_new} == {b}
    # Comportement historique conservé (None = tout)
    assert len(store.get_indexed_chunks(sid)) == 3


def test_stale_books_flags_mismatch(store: LibraryStore, tmp_path: Path):
    sid, a, b = _seed_two_books(store, tmp_path)
    stale = {s["id"]: s for s in store.stale_books(sid, "nouveau-modele")}
    assert stale[a]["ok_count"] == 0 and stale[a]["total"] == 2
    assert b not in stale  # b est déjà à jour

    stale_old = {s["id"]: s for s in store.stale_books(sid, "ancien-modele")}
    assert a not in stale_old
    assert stale_old[b]["ok_count"] == 0


def test_update_chunks_embedding_switches_provenance(
    store: LibraryStore, tmp_path: Path
):
    sid, a, b = _seed_two_books(store, tmp_path)
    rows = [r for r in store.get_subject_chunks(sid) if r["book_id"] == a]
    vecs = [[0.9, 0.1], [0.8, 0.2]]
    n = store.update_chunks_embedding(a, vecs, "nouveau-modele")
    assert n == 2
    assert {r["book_id"] for r in
            store.get_indexed_chunks(sid, model="nouveau-modele")} == {a, b}
    assert store.stale_books(sid, "nouveau-modele") == []
