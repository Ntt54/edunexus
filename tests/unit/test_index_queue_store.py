from __future__ import annotations

from pathlib import Path

from ollama_tutor.tutor.store import LibraryStore


def test_recover_interrupted_indexing_returns_book_to_pending(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    subject = store.create_subject("Reprise")
    source = tmp_path / "cours.txt"
    source.write_text("contenu partiel", encoding="utf-8")
    book = store.import_document(subject.id, source)

    store.mark_indexing(book.id)
    store.add_chunks(
        subject.id,
        book.id,
        [{"text": "fragment partiel", "page": 1}],
        [[1.0, 0.0]],
        "model-a",
    )

    assert store.recover_interrupted_indexing() == 1
    recovered = store.get_book(book.id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.chunks_done == 0
    assert recovered.chunks_total == 0
    assert [row for row in store.get_subject_chunks(subject.id) if row["book_id"] == book.id] == []
