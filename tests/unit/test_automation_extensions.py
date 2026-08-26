from __future__ import annotations

from pathlib import Path

from ollama_tutor.config import Config
from ollama_tutor.tutor.service import TutorService
from ollama_tutor.tutor.store import LibraryStore


class DummyClient:
    async def close(self) -> None:
        return None


def test_config_nightly_values_are_persisted_and_bounded(tmp_path: Path) -> None:
    config = Config(tmp_path / "config")
    config.tutor_nightly_start_at = "25:99"
    config.tutor_nightly_stop_at = "06:30"
    config.tutor_nightly_max_runtime_minutes = 99999
    config.tutor_nightly_enabled = True
    config.save()

    again = Config(tmp_path / "config")
    assert again.tutor_nightly_start_at == "23:00"
    assert again.tutor_nightly_stop_at == "06:30"
    assert again.tutor_nightly_max_runtime_minutes == 1440
    assert again.tutor_nightly_enabled is True


def test_same_source_path_is_reindexed_without_duplicate_book(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    subject = store.create_subject("Diff")
    source = tmp_path / "cours.txt"
    source.write_text("version un", encoding="utf-8")
    first = store.import_document(subject.id, source)
    store.mark_indexing(first.id)
    store.add_chunks(subject.id, first.id, ["ancien fragment"], [[1.0, 0.0]], "model")
    store.mark_indexed(first.id, 1)

    source.write_text("version deux", encoding="utf-8")
    second = store.import_document(subject.id, source)

    assert second.id == first.id
    assert second.status == "pending"
    assert second.chunks_total == 0
    assert store.list_all_books() == [second]
    assert store.get_subject_chunks(subject.id) == []


def test_nightly_window_supports_cross_midnight() -> None:
    assert TutorService._nightly_window_open("23:30", "23:00", "07:00")
    assert TutorService._nightly_window_open("06:59", "23:00", "07:00")
    assert not TutorService._nightly_window_open("12:00", "23:00", "07:00")
    assert TutorService._nightly_window_open("10:00", "09:00", "11:00")
    assert not TutorService._nightly_window_open("11:00", "09:00", "11:00")


def test_maintenance_removes_only_orphan_embedding(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "config")
    store.add_embedding("orphan-hash", "model", [1.0, 0.0])
    report = store.maintenance_report()
    assert report["ok"] is True
    assert report["orphan_embeddings"] == 1
    assert store.cleanup_orphan_embeddings() == 1
    assert store.maintenance_report()["orphan_embeddings"] == 0
