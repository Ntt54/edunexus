"""Unit tests for LibraryStore Phase 4: categories, corpora, temp-doc lifecycle.

All tests run offline against a ``tmp_path`` config dir; no network, no daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.store import LibraryStore


def _make_tmp_file(tmp_path: Path, name: str, content: str = "") -> Path:
    p = tmp_path / name
    p.write_text(content or f"Content of {name} for embedding.", encoding="utf-8")
    return p


def _make_store_with_book(tmp_path: Path, title: str = "newton.txt"):
    """Store + subject + one imported book; returns (store, subject, book)."""
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Physics")
    book = store.import_document(subject.id, _make_tmp_file(tmp_path, title))
    return store, subject, book


# ---------------------------------------------------------------------------
# 1. Category CRUD incl. duplicate-name behavior.
# ---------------------------------------------------------------------------


def test_category_crud_round_trip(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)

    cat = store.create_category("Quantum Physics")
    assert cat["id"] > 0
    assert cat["name"] == "Quantum Physics"
    assert [c["name"] for c in store.list_categories()] == ["Quantum Physics"]

    renamed = store.rename_category(cat["id"], "Quantum Mechanics")
    assert renamed == {"id": cat["id"], "name": "Quantum Mechanics"}
    assert store.get_category(cat["id"])["name"] == "Quantum Mechanics"

    assert store.delete_category(cat["id"]) is True
    assert store.list_categories() == []
    assert store.get_category(cat["id"]) is None
    store.close()


def test_category_duplicate_name_rejected(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    store.create_category("Reference")
    # Convention chosen: duplicates raise ValueError (mirrors create_subject),
    # case-insensitive and whitespace-normalized.
    with pytest.raises(ValueError):
        store.create_category("reference")
    with pytest.raises(ValueError):
        store.create_category("  REFERENCE  ")
    assert len(store.list_categories()) == 1
    store.close()


def test_category_validation_and_unknown_ids(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_category("   ")
    cat = store.create_category("Solo")
    other = store.create_category("Other")

    # Cross-row case-insensitive clash (self-rename to own name in another
    # case is allowed, mirroring rename_subject's id != guard).
    with pytest.raises(ValueError):
        store.rename_category(other["id"], "SOLO")
    with pytest.raises(KeyError):
        store.rename_category(9999, "Whatever")
    with pytest.raises(KeyError):
        store.delete_category(9999)
    store.close()


def test_rename_category_to_unique_name_keeps_memberships(tmp_path: Path) -> None:
    store, _subject, book = _make_store_with_book(tmp_path)
    cat = store.create_category("Old Name")
    assert store.add_book_to_category(book.id, cat["id"]) is True

    store.rename_category(cat["id"], "New Name")
    assert [c["name"] for c in store.list_categories_for_book(book.id)] == [
        "New Name"
    ]
    store.close()


# ---------------------------------------------------------------------------
# 2. Corpus CRUD (same conventions).
# ---------------------------------------------------------------------------


def test_corpus_crud_and_duplicates(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)

    corpus = store.create_corpus("Exam Prep")
    assert corpus["name"] == "Exam Prep"
    with pytest.raises(ValueError):
        store.create_corpus("exam prep")
    with pytest.raises(ValueError):
        store.create_corpus("   ")

    renamed = store.rename_corpus(corpus["id"], "Final Exam Prep")
    assert renamed["name"] == "Final Exam Prep"
    assert store.get_corpus(corpus["id"])["name"] == "Final Exam Prep"

    assert store.delete_corpus(corpus["id"]) is True
    assert store.list_corpora() == []

    with pytest.raises(KeyError):
        store.rename_corpus(4242, "Ghost")
    store.close()


# ---------------------------------------------------------------------------
# 3. Membership add/remove/idempotence (categories AND corpora).
# ---------------------------------------------------------------------------


def test_membership_add_remove_idempotence(tmp_path: Path) -> None:
    store, _subject, book = _make_store_with_book(tmp_path)
    cat = store.create_category("C1")
    corpus = store.create_corpus("R1")

    assert store.add_book_to_category(book.id, cat["id"]) is True
    assert store.add_book_to_category(book.id, cat["id"]) is False  # idempotent
    assert store.remove_book_from_category(book.id, cat["id"]) is True
    assert store.remove_book_from_category(book.id, cat["id"]) is False

    assert store.add_book_to_corpus(book.id, corpus["id"]) is True
    assert store.add_book_to_corpus(book.id, corpus["id"]) is False
    assert store.remove_book_from_corpus(book.id, corpus["id"]) is True
    assert store.remove_book_from_corpus(book.id, corpus["id"]) is False
    store.close()


def test_membership_unknown_ids_raise(tmp_path: Path) -> None:
    store, _subject, book = _make_store_with_book(tmp_path)
    with pytest.raises(KeyError):
        store.add_book_to_category(book.id, 9999)
    with pytest.raises(KeyError):
        store.add_book_to_category("no-such-book", 1)
    with pytest.raises(KeyError):
        store.add_book_to_corpus(book.id, 9999)
    with pytest.raises(KeyError):
        store.list_categories_for_book("no-such-book")
    store.close()


# ---------------------------------------------------------------------------
# 4. Cascades in both directions.
# ---------------------------------------------------------------------------


def test_deleting_book_cascades_memberships(tmp_path: Path) -> None:
    store, subject, book = _make_store_with_book(tmp_path)
    cat = store.create_category("Cats")
    corpus = store.create_corpus("Corps")
    store.add_book_to_category(book.id, cat["id"])
    store.add_book_to_corpus(book.id, corpus["id"])

    store.delete_book(book.id)

    # Join rows are gone (FK cascade via PRAGMA foreign_keys=ON)...
    joins = store._conn.execute(
        "SELECT COUNT(*) FROM book_categories WHERE book_id = ?", (book.id,)
    ).fetchone()[0]
    cjoins = store._conn.execute(
        "SELECT COUNT(*) FROM book_corpora WHERE book_id = ?", (book.id,)
    ).fetchone()[0]
    assert joins == 0 and cjoins == 0
    # ...but the labels themselves survive.
    assert [c["name"] for c in store.list_categories()] == ["Cats"]
    assert [r["name"] for r in store.list_corpora()] == ["Corps"]
    store.close()


def test_deleting_label_cascades_memberships_but_keeps_books(tmp_path: Path) -> None:
    store, subject, book = _make_store_with_book(tmp_path)
    other = store.import_document(subject.id, _make_tmp_file(tmp_path, "other.txt"))
    cat = store.create_category("Temp Tag")
    corpus = store.create_corpus("Temp Set")
    store.add_book_to_category(book.id, cat["id"])
    store.add_book_to_corpus(book.id, corpus["id"])
    store.add_book_to_category(other.id, cat["id"])

    assert store.delete_category(cat["id"]) is True
    assert store.list_categories_for_book(book.id) == []
    assert store.list_categories_for_book(other.id) == []
    # Books themselves survive the label deletion.
    assert {b.id for b in store.list_books(subject.id)} == {book.id, other.id}

    assert store.delete_corpus(corpus["id"]) is True
    assert store.list_corpora_for_book(book.id) == []
    assert len(store.list_books(subject.id)) == 2
    store.close()


# ---------------------------------------------------------------------------
# 5. Round-trips through the join tables.
# ---------------------------------------------------------------------------


def test_membership_listing_round_trips(tmp_path: Path) -> None:
    store, subject, book_a = _make_store_with_book(tmp_path, "a.txt")
    book_b = store.import_document(subject.id, _make_tmp_file(tmp_path, "b.txt"))
    cat1 = store.create_category("Alpha")
    cat2 = store.create_category("Beta")
    corpus = store.create_corpus("Set X")

    store.add_book_to_category(book_a.id, cat1["id"])
    store.add_book_to_category(book_a.id, cat2["id"])
    store.add_book_to_category(book_b.id, cat2["id"])
    store.add_book_to_corpus(book_a.id, corpus["id"])

    assert [b.id for b in store.list_books_by_category(cat1["id"])] == [book_a.id]
    assert {b.id for b in store.list_books_by_category(cat2["id"])} == {
        book_a.id,
        book_b.id,
    }
    assert [b.id for b in store.list_books_by_corpus(corpus["id"])] == [book_a.id]

    cats_for_a = store.list_categories_for_book(book_a.id)
    assert [c["name"] for c in cats_for_a] == ["Alpha", "Beta"]
    assert [c["name"] for c in store.list_corpora_for_book(book_a.id)] == ["Set X"]
    assert store.list_corpora_for_book(book_b.id) == []
    store.close()


# ---------------------------------------------------------------------------
# 6. Temp-doc lifecycle.
# ---------------------------------------------------------------------------


def test_temp_book_filtered_from_list_books_by_default(tmp_path: Path) -> None:
    store, subject, book = _make_store_with_book(tmp_path)

    store.mark_book_temporary(book.id, ttl_s=3600.0, now=1000.0)

    assert store.list_books(subject.id) == []  # filtered out by default
    listed = store.list_books(subject.id, include_temp=True)
    assert [b.id for b in listed] == [book.id]
    store.close()


def test_new_books_default_permanent(tmp_path: Path) -> None:
    store, subject, book = _make_store_with_book(tmp_path)
    row = store._conn.execute(
        "SELECT is_temp, expires_at FROM books WHERE id = ?", (book.id,)
    ).fetchone()
    assert row["is_temp"] == 0
    assert row["expires_at"] is None
    # Permanent books show up regardless of the flag.
    assert len(store.list_books(subject.id)) == 1
    store.close()


def test_purge_expired_removes_only_expired_and_returns_count(tmp_path: Path) -> None:
    store, subject, expired_book = _make_store_with_book(tmp_path, "expired.txt")
    live_book = store.import_document(
        subject.id, _make_tmp_file(tmp_path, "live.txt")
    )
    permanent_book = store.import_document(
        subject.id, _make_tmp_file(tmp_path, "perm.txt")
    )

    # Give the expired book chunk data to prove data goes with it.
    store.mark_indexed(expired_book.id, 2)
    store.add_chunks(
        subject.id, expired_book.id, ["chunk one", "chunk two"],
        [[0.1], [0.2]], "test-model",
    )

    store.mark_book_temporary(expired_book.id, ttl_s=100.0, now=1000.0)  # exp 1100
    store.mark_book_temporary(live_book.id, ttl_s=50.0, now=2000.0)  # exp 2050
    store.mark_book_temporary(permanent_book.id, ttl_s=10.0, now=0.0)
    store.make_book_permanent(permanent_book.id)  # cancels expiry

    purged = store.purge_expired_temp_books(now=2025.0)

    assert purged == 1  # only the expired one
    assert store.get_book(expired_book.id) is None
    # Its chunk data went with it (same path as delete_book).
    remaining_chunks = store._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE book_id = ?", (expired_book.id,)
    ).fetchone()[0]
    assert remaining_chunks == 0
    # Live temp and permanent books survive.
    survivors = {
        b.id for b in store.list_books(subject.id, include_temp=True)
    }
    assert survivors == {live_book.id, permanent_book.id}
    store.close()


def test_make_book_permanent_cancels_expiry(tmp_path: Path) -> None:
    store, subject, book = _make_store_with_book(tmp_path)

    store.mark_book_temporary(book.id, ttl_s=10.0, now=500.0)  # expires 510
    permanent = store.make_book_permanent(book.id)
    row = store._conn.execute(
        "SELECT is_temp, expires_at FROM books WHERE id = ?", (book.id,)
    ).fetchone()
    assert row["is_temp"] == 0
    assert row["expires_at"] is None

    # Long past the would-be expiry: still present because it's permanent.
    assert store.purge_expired_temp_books(now=99999.0) == 0
    assert [b.id for b in store.list_books(subject.id)] == [permanent.id]
    store.close()


def test_purge_with_no_temp_books_is_noop(tmp_path: Path) -> None:
    store, _subject, book = _make_store_with_book(tmp_path)
    assert store.purge_expired_temp_books() == 0
    assert store.get_book(book.id) is not None
    store.close()


# ---------------------------------------------------------------------------
# 7. Migration idempotence: close + reopen the same config_dir.
# ---------------------------------------------------------------------------


def test_reopen_same_config_dir_is_idempotent(tmp_path: Path) -> None:
    # First open creates schema + applies migrations.
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Math")
    book = store.import_document(subject.id, _make_tmp_file(tmp_path, "calc.txt"))
    cat = store.create_category("Freshman")
    store.add_book_to_category(book.id, cat["id"])
    store.mark_book_temporary(book.id, ttl_s=60.0, now=0.0)
    store.close()

    # Reopen simulates upgrading/relaunching against an existing DB.
    reopened = LibraryStore(tmp_path)
    assert [s.name for s in reopened.list_subjects()] == ["Math"]
    books = reopened.list_books(subject.id, include_temp=True)
    assert [b.id for b in books] == [book.id]
    assert [c["name"] for c in reopened.list_categories()] == ["Freshman"]

    # Columns exist exactly once (no duplicate-column damage).
    cols = [
        r[1]
        for r in reopened._conn.execute("PRAGMA table_info(books)").fetchall()
    ]
    assert cols.count("is_temp") == 1
    assert cols.count("expires_at") == 1

    # And the reopened store still works end-to-end.
    new_cat = reopened.create_category("Sophomore")
    assert reopened.add_book_to_category(book.id, new_cat["id"]) is True
    reopened.close()
