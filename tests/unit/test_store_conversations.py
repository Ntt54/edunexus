"""Unit tests : persistance des conversations (005-platform-ui-library).

Couvre la migration idempotente ``sessions.title/updated_at``, la table
``conversation_sources`` (déduplication, cascades) et le cycle de vie
conversation (créer/lister/renommer/supprimer).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def store(tmp_path: Path):
    return LibraryStore(tmp_path)


def _seed_book(store: LibraryStore, tmp_path: Path, title: str):
    subject = store.create_subject("Réseaux")
    p = tmp_path / f"{title}.txt"
    p.write_text("contenu de test " * 20, encoding="utf-8")
    book = store.import_document(subject.id, p)
    return subject, book


def test_migration_idempotent_on_reopen(store: LibraryStore, tmp_path: Path):
    conv_id = "conv-1"
    store.create_tutoring_session(
        store.create_subject("Réseaux").id, title="Ma conversation",
        session_id=conv_id,
    )
    # Réouverture sur le même config_dir : la migration ne doit ni échouer
    # ni perdre les données (style PRAGMA-table_info idempotent).
    reopened = LibraryStore(tmp_path)
    convs = reopened.list_conversations()
    assert len(convs) == 1
    assert convs[0]["title"] == "Ma conversation"


def test_create_with_title_and_list_order(store: LibraryStore):
    sid = store.create_subject("Réseaux").id
    old = store.create_tutoring_session(sid, title="Ancienne")
    new = store.create_tutoring_session(sid, title="Récente")
    store.touch_conversation(new.id)
    convs = store.list_conversations()
    assert [c["title"] for c in convs] == ["Récente", "Ancienne"]
    assert convs[0]["message_count"] >= 0  # clé présente
    assert convs[0]["subject_name"] == "Réseaux"


def test_rename_conversation(store: LibraryStore):
    sid = store.create_subject("Réseaux").id
    conv = store.create_tutoring_session(sid, title="Brouillon")
    assert store.rename_conversation(conv.id, "Apprendre Java") is True
    assert store.list_conversations()[0]["title"] == "Apprendre Java"
    # Identifiant inconnu : False, pas d'exception.
    assert store.rename_conversation("inconnu", "x") is False


def test_sources_dedup_and_unknown_ignored(store: LibraryStore, tmp_path: Path):
    subject, book = _seed_book(store, tmp_path, "Livre A")
    conv = store.create_tutoring_session(subject.id)
    n = store.set_conversation_sources(conv.id, [book.id, book.id, "inconnu"])
    assert n == 1
    assert store.get_conversation_source_ids(conv.id) == [book.id]


def test_delete_conversation_cascades_sources(
    store: LibraryStore, tmp_path: Path
):
    subject, book = _seed_book(store, tmp_path, "Livre A")
    conv = store.create_tutoring_session(subject.id)
    store.set_conversation_sources(conv.id, [book.id])
    assert store.delete_conversation(conv.id) is True
    assert store.get_conversation_source_ids(conv.id) == []
    assert store.delete_conversation(conv.id) is False


def test_concurrent_transcript_appends_are_atomic(store: LibraryStore):
    sid = store.create_subject("Réseaux").id
    conv = store.create_tutoring_session(sid)
    barrier = threading.Barrier(2)

    def append_many(role: str) -> None:
        barrier.wait()
        for i in range(20):
            store.append_conversation_message(conv.id, role, f"{role}-{i}")

    threads = [
        threading.Thread(target=append_many, args=("user",)),
        threading.Thread(target=append_many, args=("assistant",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    transcript = store.get_session_transcript(conv.id)
    assert len(transcript) == 40
    assert {row["role"] for row in transcript} == {"user", "assistant"}


def test_book_deletion_cleans_sources_via_fk(
    store: LibraryStore, tmp_path: Path
):
    subject, book = _seed_book(store, tmp_path, "Livre A")
    conv = store.create_tutoring_session(subject.id)
    store.set_conversation_sources(conv.id, [book.id])
    # Suppression directe du livre : la FK ON DELETE CASCADE doit nettoyer
    # conversation_sources (invariant data-model.md §2).
    store._conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
    store._conn.commit()
    assert store.get_conversation_source_ids(conv.id) == []
