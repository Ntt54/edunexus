"""SQLite-backed library store for the local AI tutor (data-model.md).

Implements the ``LibraryStore`` contract from ``contracts/tutor-core-api.md``.
Synchronous (stdlib ``sqlite3``), WAL mode, single DB file at
``<config_dir>/tutor/library.db``. All subject-owned rows cascade on subject
deletion via foreign keys.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from .models import (
    Concept,
    Exercise,
    ExerciseAttempt,
    Flashcard,
    GlossaryTerm,
    KnowledgeRelation,
    LearningPath,
    PathStep,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    ReviewItem,
    SessionSummary,
    Subject,
    TutoringSession,
    _now_iso,
    _uid,
)

_SUBJECT_NAME_MAX = 80
_VALID_LEVELS = {"beginner", "intermediate", "advanced", "expert"}


def _json(value: Any) -> str:
    """Serialize a list/dict to a JSON TEXT column value."""
    return json.dumps(value, ensure_ascii=False)


class LibraryStore:
    """CRUD + subject-scoped persistence for the tutor library."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = Path(config_dir)
        self.tutor_dir = self.config_dir / "tutor"
        self.tutor_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tutor_dir / "library.db"
        # One sqlite connection per thread (research D6: indexing runs in a
        # background thread while the request thread reads). WAL mode lets the
        # connections share the DB safely; check_same_thread is relaxed so a
        # connection is never migrated across threads.
        self._local = threading.local()
        self._create_schema()
        self._migrate()
        self._active_id: str | None = None

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        cur = self._conn
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                format TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                chunks_done INTEGER NOT NULL DEFAULT 0,
                chunks_total INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subject_books (
                subject_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                PRIMARY KEY (subject_id, book_id),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL,
                chapter TEXT,
                section TEXT,
                page INTEGER,
                position REAL NOT NULL DEFAULT 0.0,
                difficulty TEXT,
                content_type TEXT NOT NULL DEFAULT 'prose',
                text_hash TEXT NOT NULL,
                embedding BLOB,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS embeddings (
                text_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY (text_hash, model)
            );

            CREATE TABLE IF NOT EXISTS concepts (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                name TEXT NOT NULL,
                path_rank INTEGER,
                summary TEXT,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                UNIQUE (subject_id, name)
            );

            CREATE TABLE IF NOT EXISTS progress (
                subject_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (subject_id, concept_id),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS exercises (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                statement TEXT NOT NULL,
                solution TEXT NOT NULL DEFAULT '',
                hint_level INTEGER NOT NULL DEFAULT 0,
                hints TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS exercise_attempts (
                id TEXT PRIMARY KEY,
                exercise_id TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                verdict TEXT NOT NULL,
                feedback TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS flashcards (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                book_id TEXT,
                chapter TEXT,
                level TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
                UNIQUE (subject_id, source_hash)
            );

            CREATE TABLE IF NOT EXISTS review_schedule (
                flashcard_id TEXT PRIMARY KEY,
                streak_index INTEGER NOT NULL DEFAULT 0,
                next_due TEXT NOT NULL,
                last_result TEXT,
                FOREIGN KEY (flashcard_id) REFERENCES flashcards(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS quizzes (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'quiz',
                status TEXT NOT NULL DEFAULT 'created',
                allow_help INTEGER NOT NULL DEFAULT 1,
                time_limit_s INTEGER,
                started_at TEXT,
                finished_at TEXT,
                score REAL,
                report TEXT,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS quiz_questions (
                id TEXT PRIMARY KEY,
                quiz_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                answer TEXT NOT NULL DEFAULT '{}',
                concept_id TEXT,
                points REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE,
                FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS quiz_answers (
                question_id TEXT NOT NULL,
                response TEXT NOT NULL DEFAULT '{}',
                verdict TEXT NOT NULL,
                awarded REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (question_id),
                FOREIGN KEY (question_id) REFERENCES quiz_questions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tutoring_sessions (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                transcript_path TEXT,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS session_summaries (
                session_id TEXT PRIMARY KEY,
                concepts_studied TEXT NOT NULL DEFAULT '[]',
                concepts_mastered TEXT NOT NULL DEFAULT '[]',
                to_review TEXT NOT NULL DEFAULT '[]',
                produced_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES tutoring_sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS glossary_terms (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                term TEXT NOT NULL COLLATE NOCASE,
                definition TEXT NOT NULL,
                book_id TEXT,
                chapter TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                UNIQUE (subject_id, term)
            );

            CREATE TABLE IF NOT EXISTS knowledge_relations (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                from_concept_id TEXT NOT NULL,
                to_concept_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'indexing',
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (from_concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
                FOREIGN KEY (to_concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
                UNIQUE (subject_id, from_concept_id, to_concept_id, relation)
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );

            CREATE TABLE IF NOT EXISTS corpora (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );

            -- book_id is TEXT to match books.id affinity exactly: an
            -- INTEGER-typed child column would silently coerce numeric-looking
            -- ids and break foreign-key cascade lookups.
            CREATE TABLE IF NOT EXISTS book_categories (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                PRIMARY KEY (book_id, category_id)
            );

            CREATE TABLE IF NOT EXISTS book_corpora (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                corpus_id INTEGER NOT NULL REFERENCES corpora(id) ON DELETE CASCADE,
                PRIMARY KEY (book_id, corpus_id)
            );

            -- Sources actives par conversation (005-platform-ui-library).
            -- Référence pure : aucun document ni embedding dupliqué.
            CREATE TABLE IF NOT EXISTS conversation_sources (
                conversation_id TEXT NOT NULL REFERENCES tutoring_sessions(id) ON DELETE CASCADE,
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                PRIMARY KEY (conversation_id, book_id)
            );

            CREATE TABLE IF NOT EXISTS learning_paths (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS path_steps (
                id TEXT PRIMARY KEY,
                path_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                activity_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                completed_at TEXT,
                FOREIGN KEY (path_id) REFERENCES learning_paths(id) ON DELETE CASCADE
            );
            """
        )
        # Error history (Feature 007 — US11)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS error_history (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                concept_name TEXT NOT NULL,
                question_text TEXT NOT NULL DEFAULT '',
                given_answer TEXT NOT NULL DEFAULT '',
                correct_answer TEXT NOT NULL DEFAULT '',
                source_refs TEXT DEFAULT '[]',
                error_type TEXT NOT NULL DEFAULT 'unknown',
                created_at TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            );
            """
        )
        # Document metadata (Feature 007 — US9)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS document_metadata (
                book_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (book_id, key),
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            );
            """
        )
        self._conn.commit()

    def _migrate(self) -> None:
        """Apply idempotent schema migrations for later lanes (US4+).

        Adds the ``gap_flag`` column to ``progress`` (data-model gap detection,
        FR-022), the ``closed_at`` column to ``tutoring_sessions`` (US6
        session close, FR-028), and the temp-doc lifecycle columns
        ``is_temp``/``expires_at`` to ``books`` (Phase 4) when absent. Safe
        to call on every startup.
        """
        cur = self._conn
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(progress)")}
        if "gap_flag" not in cols:
            cur.execute(
                "ALTER TABLE progress ADD COLUMN gap_flag INTEGER NOT NULL DEFAULT 0"
            )
            cur.commit()
        scols = {r["name"] for r in cur.execute("PRAGMA table_info(tutoring_sessions)")}
        if "closed_at" not in scols:
            cur.execute(
                "ALTER TABLE tutoring_sessions ADD COLUMN closed_at TEXT"
            )
            cur.commit()
        bcols = {r["name"] for r in cur.execute("PRAGMA table_info(books)")}
        if "is_temp" not in bcols:
            cur.execute(
                "ALTER TABLE books ADD COLUMN is_temp INTEGER NOT NULL DEFAULT 0"
            )
            cur.commit()
        if "expires_at" not in bcols:
            cur.execute(
                "ALTER TABLE books ADD COLUMN expires_at REAL"
            )
            cur.commit()
        # Conversations nommées (005-platform-ui-library) : titre éditable +
        # horodatage de dernière activité par session.
        # Provenance d'embedding par chunk (005-suite) : sans cette colonne,
        # changer de modèle d'embedding mélangerait des vecteurs incompatibles.
        ccols = {r["name"] for r in cur.execute("PRAGMA table_info(chunks)")}
        if "embedding_model" not in ccols:
            cur.execute("ALTER TABLE chunks ADD COLUMN embedding_model TEXT")
            cur.commit()
            # Backfill : retrouve le modèle réel depuis le cache d'embeddings
            cur.execute(
                "UPDATE chunks SET embedding_model = ("
                " SELECT e.model FROM embeddings e"
                " WHERE e.text_hash = chunks.text_hash"
                " ORDER BY e.rowid DESC LIMIT 1)"
                " WHERE embedding IS NOT NULL AND embedding_model IS NULL"
            )
            cur.commit()
        tcols = {r["name"] for r in cur.execute("PRAGMA table_info(tutoring_sessions)")}
        if "title" not in tcols:
            cur.execute(
                "ALTER TABLE tutoring_sessions ADD COLUMN title TEXT NOT NULL DEFAULT ''"
            )
            cur.commit()
        if "updated_at" not in tcols:
            cur.execute(
                "ALTER TABLE tutoring_sessions ADD COLUMN updated_at REAL"
            )
            cur.commit()
        # Domain classification for adaptive learning (Feature 006).
        scols2 = {r["name"] for r in cur.execute("PRAGMA table_info(subjects)")}
        if "domain" not in scols2:
            cur.execute(
                "ALTER TABLE subjects ADD COLUMN domain TEXT NOT NULL DEFAULT 'generique'"
            )
            cur.commit()

    # ------------------------------------------------------------------
    # Error history (Feature 007 — US11)
    # ------------------------------------------------------------------

    def record_error(
        self,
        subject_id: str,
        concept_name: str,
        question_text: str,
        given_answer: str,
        correct_answer: str,
        source_refs: list[str] | None = None,
        error_type: str = "unknown",
    ) -> None:
        """Persist a detailed error record for later analysis."""
        from .models import _now_iso, _uid

        self._conn.execute(
            """INSERT INTO error_history
               (id, subject_id, concept_name, question_text, given_answer,
                correct_answer, source_refs, error_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _uid(),
                subject_id,
                concept_name,
                question_text,
                given_answer,
                correct_answer,
                json.dumps(source_refs or [], ensure_ascii=False),
                error_type,
                _now_iso(),
            ),
        )
        self._conn.commit()

    def get_error_history(
        self,
        subject_id: str,
        concept_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent errors, optionally filtered by concept."""
        if concept_name:
            rows = self._conn.execute(
                """SELECT * FROM error_history
                   WHERE subject_id = ? AND concept_name = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (subject_id, concept_name, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM error_history
                   WHERE subject_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (subject_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Document metadata (Feature 007 — US9)
    # ------------------------------------------------------------------

    def set_document_metadata(self, book_id: str, key: str, value: str) -> None:
        """Set a key/value metadata pair for a book (upsert)."""
        self._conn.execute(
            """INSERT INTO document_metadata (book_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(book_id, key) DO UPDATE SET value = excluded.value""",
            (book_id, key, value),
        )
        self._conn.commit()

    def get_document_metadata(self, book_id: str) -> dict[str, str]:
        """Return all metadata for a book as a dict."""
        rows = self._conn.execute(
            "SELECT key, value FROM document_metadata WHERE book_id = ?",
            (book_id,),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ------------------------------------------------------------------
    # Concepts & progress (extensions: gap flag, exercise/attempt storage)
    # ------------------------------------------------------------------

    def get_concept(self, concept_id: str) -> Concept | None:
        row = self._conn.execute(
            "SELECT * FROM concepts WHERE id = ?", (concept_id,)
        ).fetchone()
        return Concept.from_dict(dict(row)) if row is not None else None

    def set_concept_path_rank(self, concept_id: str, path_rank: int) -> None:
        self._conn.execute(
            "UPDATE concepts SET path_rank = ? WHERE id = ?",
            (path_rank, concept_id),
        )
        self._conn.commit()

    def get_concept_score(self, concept_id: str) -> float | None:
        row = self._conn.execute(
            "SELECT score FROM progress WHERE concept_id = ?", (concept_id,)
        ).fetchone()
        return float(row["score"]) if row is not None else None

    def get_gap_flag(self, concept_id: str) -> bool:
        row = self._conn.execute(
            "SELECT gap_flag FROM progress WHERE concept_id = ?", (concept_id,)
        ).fetchone()
        return bool(row["gap_flag"]) if row is not None else False

    def set_gap_flag(self, concept_id: str, flag: bool) -> None:
        self._conn.execute(
            "UPDATE progress SET gap_flag = ? WHERE concept_id = ?",
            (1 if flag else 0, concept_id),
        )
        self._conn.commit()

    def add_exercise(self, exercise: Exercise) -> Exercise:
        self._get_subject(exercise.subject_id)
        now = _now_iso()
        exercise.created_at = exercise.created_at or now
        self._conn.execute(
            "INSERT INTO exercises "
            "(id, subject_id, concept_id, difficulty, statement, solution, "
            "hint_level, hints, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                exercise.id,
                exercise.subject_id,
                exercise.concept_id,
                exercise.difficulty,
                exercise.statement,
                exercise.solution,
                exercise.hint_level,
                _json(exercise.hints),
                exercise.status,
                exercise.created_at,
            ),
        )
        self._conn.commit()
        return exercise

    def get_exercise(self, exercise_id: str) -> Exercise | None:
        row = self._conn.execute(
            "SELECT * FROM exercises WHERE id = ?", (exercise_id,)
        ).fetchone()
        return Exercise.from_dict(dict(row)) if row is not None else None

    def update_exercise(
        self,
        exercise_id: str,
        hint_level: int | None = None,
        status: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if hint_level is not None:
            sets.append("hint_level = ?")
            params.append(hint_level)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if sets:
            params.append(exercise_id)
            self._conn.execute(
                f"UPDATE exercises SET {', '.join(sets)} WHERE id = ?", params
            )
            self._conn.commit()

    def add_attempt(self, attempt: ExerciseAttempt) -> ExerciseAttempt:
        attempt.id = attempt.id or _uid()
        attempt.created_at = attempt.created_at or _now_iso()
        self._conn.execute(
            "INSERT INTO exercise_attempts "
            "(id, exercise_id, answer, verdict, feedback, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                attempt.id,
                attempt.exercise_id,
                attempt.answer,
                attempt.verdict,
                attempt.feedback,
                attempt.created_at,
            ),
        )
        self._conn.commit()
        return attempt

    def list_attempts_by_concept(self, concept_id: str) -> list[ExerciseAttempt]:
        rows = self._conn.execute(
            "SELECT ea.* FROM exercise_attempts ea "
            "JOIN exercises e ON ea.exercise_id = e.id "
            "WHERE e.concept_id = ? ORDER BY ea.created_at ASC",
            (concept_id,),
        ).fetchall()
        return [ExerciseAttempt.from_dict(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Subjects
    # ------------------------------------------------------------------

    def create_subject(self, name: str) -> Subject:
        """Create a subject; name must be non-empty, <=80 chars, unique (CI)."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Subject name must be a non-empty string")
        name = name.strip()
        if len(name) > _SUBJECT_NAME_MAX:
            raise ValueError(
                f"Subject name exceeds {_SUBJECT_NAME_MAX} chars: {len(name)}"
            )
        existing = self._conn.execute(
            "SELECT id FROM subjects WHERE name = ?", (name,)
        ).fetchone()
        if existing is not None:
            raise ValueError(f"Subject already exists: {name}")
        now = _now_iso()
        subject = Subject(id=_uid(), name=name, created_at=now, last_used_at=now)
        self._conn.execute(
            "INSERT INTO subjects (id, name, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?)",
            (subject.id, subject.name, subject.created_at, subject.last_used_at),
        )
        self._conn.commit()
        return subject

    def list_subjects(self) -> list[Subject]:
        rows = self._conn.execute(
            "SELECT * FROM subjects ORDER BY created_at, name"
        ).fetchall()
        return [Subject.from_dict(dict(r)) for r in rows]

    def rename_subject(self, subject_id: str, name: str) -> Subject:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Subject name must be a non-empty string")
        name = name.strip()
        if len(name) > _SUBJECT_NAME_MAX:
            raise ValueError(
                f"Subject name exceeds {_SUBJECT_NAME_MAX} chars: {len(name)}"
            )
        subject = self._get_subject(subject_id)  # KeyError if unknown
        clash = self._conn.execute(
            "SELECT id FROM subjects WHERE name = ? AND id != ?",
            (name, subject_id),
        ).fetchone()
        if clash is not None:
            raise ValueError(f"Subject already exists: {name}")
        self._conn.execute(
            "UPDATE subjects SET name = ? WHERE id = ?", (name, subject_id)
        )
        self._conn.commit()
        subject.name = name
        return subject

    def delete_subject(self, subject_id: str) -> None:
        self._get_subject(subject_id)  # KeyError if unknown
        self._conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
        self._conn.commit()
        if self._active_id == subject_id:
            self._active_id = None

    def select_subject(self, subject_id: str) -> Subject:
        subject = self._get_subject(subject_id)
        now = _now_iso()
        self._conn.execute(
            "UPDATE subjects SET last_used_at = ? WHERE id = ?",
            (now, subject_id),
        )
        self._conn.commit()
        self._active_id = subject_id
        subject.last_used_at = now
        return subject

    def active_subject(self) -> Subject | None:
        if self._active_id is None:
            return None
        row = self._conn.execute(
            "SELECT * FROM subjects WHERE id = ?", (self._active_id,)
        ).fetchone()
        return Subject.from_dict(dict(row)) if row is not None else None

    def _get_subject(self, subject_id: str) -> Subject:
        row = self._conn.execute(
            "SELECT * FROM subjects WHERE id = ?", (subject_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown subject: {subject_id}")
        return Subject.from_dict(dict(row))

    # ------------------------------------------------------------------
    # Documents (minimal import lifecycle; extended in later lanes)
    # ------------------------------------------------------------------

    def import_document(self, subject_id: str, path: Any) -> Any:
        """Register a book for ``subject_id``; returns a pending ``Book``.

        Raises ``FileNotFoundError`` when the path is missing and ``ValueError``
        for unsupported formats. Re-import of the same fingerprint within the
        subject is a no-op returning the existing ``Book``. (Full extraction +
        chunking is added in later lanes; this establishes the row + join.)
        """
        from .models import Book

        self._get_subject(subject_id)  # KeyError if unknown
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"No such file: {p}")
        suffix = p.suffix.lower()
        if suffix not in (".txt", ".md", ".pdf", ".epub"):
            raise ValueError(f"Unsupported format: {suffix}")
        fmt = suffix.lstrip(".")
        import hashlib

        if fmt in ("txt", "md"):
            raw = p.read_text(encoding="utf-8", errors="replace")
        else:
            raw = p.read_bytes().hex()
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        existing = self._conn.execute(
            "SELECT b.* FROM books b "
            "JOIN subject_books sb ON sb.book_id = b.id "
            "WHERE sb.subject_id = ? AND b.fingerprint = ?",
            (subject_id, fingerprint),
        ).fetchone()
        if existing is not None:
            return Book.from_dict(dict(existing))

        now = _now_iso()
        book = Book(
            id=_uid(),
            title=p.stem,
            source_path=str(p),
            format=fmt,
            fingerprint=fingerprint,
            status="pending",
            error=None,
            chunks_done=0,
            chunks_total=0,
            created_at=now,
        )
        self._conn.execute(
            "INSERT INTO books (id, title, source_path, format, fingerprint, "
            "status, error, chunks_done, chunks_total, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book.id,
                book.title,
                book.source_path,
                book.format,
                book.fingerprint,
                book.status,
                book.error,
                book.chunks_done,
                book.chunks_total,
                book.created_at,
            ),
        )
        self._conn.execute(
            "INSERT INTO subject_books (subject_id, book_id) VALUES (?, ?)",
            (subject_id, book.id),
        )
        self._conn.commit()
        return book

    def cancel_indexing(self, book_id: str) -> None:
        """Return a book to ``pending`` and purge its partial chunks."""
        self._conn.execute(
            "DELETE FROM chunks WHERE book_id = ?", (book_id,)
        )
        self._conn.execute(
            "UPDATE books SET status = 'pending', error = NULL, "
            "chunks_done = 0, chunks_total = 0 WHERE id = ?",
            (book_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Indexing (T014): chunks, embeddings cache, status transitions
    # ------------------------------------------------------------------

    def mark_indexing(self, book_id: str) -> None:
        """Flip a book to ``indexing`` (D5/D6)."""
        self._conn.execute(
            "UPDATE books SET status = 'indexing', error = NULL WHERE id = ?",
            (book_id,),
        )
        self._conn.commit()

    def mark_indexed(self, book_id: str, chunk_count: int) -> None:
        """Mark a book ``indexed`` with its final chunk count (T014)."""
        self._conn.execute(
            "UPDATE books SET status = 'indexed', error = NULL, "
            "chunks_done = ?, chunks_total = ? WHERE id = ?",
            (chunk_count, chunk_count, book_id),
        )
        self._conn.commit()

    def set_book_error(self, book_id: str, msg: str) -> None:
        """Record a failure on the book row (T014)."""
        self._conn.execute(
            "UPDATE books SET status = 'error', error = ? WHERE id = ?",
            (msg, book_id),
        )
        self._conn.commit()

    def get_book_status(self, book_id: str) -> str | None:
        """Return the current book status, or ``None`` if unknown (T014)."""
        row = self._conn.execute(
            "SELECT status FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        return row["status"] if row is not None else None

    def get_embedding(self, text_hash: str, model: str) -> list[float] | None:
        """Read a cached embedding vector, or ``None`` on miss (D2)."""
        row = self._conn.execute(
            "SELECT vector FROM embeddings WHERE text_hash = ? AND model = ?",
            (text_hash, model),
        ).fetchone()
        if row is None or row["vector"] is None:
            return None
        return np.frombuffer(row["vector"], dtype=np.float32).tolist()

    def add_embedding(self, text_hash: str, model: str, vector: list[float]) -> None:
        """Upsert a cached embedding vector (D2)."""
        dim = len(vector)
        blob = np.array(vector, dtype=np.float32).tobytes()
        self._conn.execute(
            "INSERT INTO embeddings (text_hash, model, dim, vector) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(text_hash, model) DO UPDATE SET "
            "dim = excluded.dim, vector = excluded.vector",
            (text_hash, model, dim, blob),
        )
        self._conn.commit()

    def add_chunks(
        self,
        subject_id: str,
        book_id: str,
        chunks: list[str] | list[dict[str, Any]],
        embeddings: list[list[float]],
        model: str,
    ) -> None:
        """Store chunk rows + embeddings cache rows for a book (T014).

        ``chunks`` and ``embeddings`` are aligned by index. Each chunk gets a
        ``text_hash`` (sha256 of its text) and an ``embedding`` BLOB; the same
        hash also populates the shared embeddings cache so identical text
        re-imported elsewhere is free (D2/D5).

        ``chunks`` may be plain strings (legacy) or structured dicts with
        keys ``text``, ``section``, ``page`` (and optionally ``chapter``)
        produced by :func:`chunk_text_structured`.  Metadata from the dicts
        is stored in the ``chunks`` table columns (T056).
        """
        total = len(chunks)
        for i, (chunk, vec) in enumerate(zip(chunks, embeddings)):
            if isinstance(chunk, dict):
                text = chunk["text"]
                chapter = chunk.get("chapter")
                section = chunk.get("section")
                page = chunk.get("page")
            else:
                text = chunk
                chapter = None
                section = None
                page = None
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            blob = np.array(vec, dtype=np.float32).tobytes() if vec else None
            position = i / total if total else 0.0
            chunk_id = _uid()
            self._conn.execute(
                "INSERT INTO chunks "
                "(id, subject_id, book_id, ordinal, text, text_hash, chapter, "
                "section, page, position, difficulty, content_type, embedding, "
                "embedding_model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'prose', ?, ?)",
                (
                    chunk_id,
                    subject_id,
                    book_id,
                    i,
                    text,
                    text_hash,
                    chapter,
                    section,
                    page,
                    position,
                    blob,
                    model,
                ),
            )
            # shared hash-keyed cache (idempotent across re-imports)
            self._conn.execute(
                "INSERT INTO embeddings (text_hash, model, dim, vector) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(text_hash, model) DO UPDATE SET "
                "dim = excluded.dim, vector = excluded.vector",
                (text_hash, model, len(vec), blob),
            )
        self._conn.commit()

    def get_indexed_chunks(
        self, subject_id: str, model: str | None = None
    ) -> list[dict[str, Any]]:
        """Chunk rows with a non-null embedding for a subject.

        ``model`` restreint aux chunks dont l'embedding provient de CE modèle
        (005-suite : empêche le mélange de vecteurs incompatibles entre
        modèles d'embedding). ``None`` = comportement historique (tout).
        """
        sql = (
            "SELECT id, book_id, text, chapter, section, page, embedding, "
            "embedding_model FROM chunks "
            "WHERE subject_id = ? AND embedding IS NOT NULL"
        )
        params: list[Any] = [subject_id]
        if model is not None:
            sql += " AND embedding_model = ?"
            params.append(model)
        rows = self._conn.execute(sql + " ORDER BY ordinal", params).fetchall()
        return [dict(r) for r in rows]

    def stale_books(self, subject_id: str, model: str) -> list[dict[str, Any]]:
        """Books whose indexed chunks do not match ``model`` (à ré-indexer).

        Retourne [{id, title, ok_count, total}] pour les livres ayant au
        moins un chunk dont l'embedding n'est pas du modèle demandé.
        """
        rows = self._conn.execute(
            "SELECT b.id, b.title, "
            " SUM(CASE WHEN c.embedding_model = ? THEN 1 ELSE 0 END) AS ok_count,"
            " COUNT(c.id) AS total "
            "FROM books b "
            "JOIN subject_books sb ON sb.book_id = b.id "
            "JOIN chunks c ON c.book_id = b.id "
            "WHERE sb.subject_id = ? "
            "GROUP BY b.id, b.title "
            "HAVING ok_count < total",
            (model, subject_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_chunks_embedding(
        self, book_id: str, embeddings: list[list[float]], model: str
    ) -> int:
        """Re-embed les chunks EXISTANTS d'un livre (même découpage).

        Met à jour blob + embedding_model par ordinal. Retourne le nombre
        mis à jour. Les textes ne changent pas : pas de re-découpage.
        """
        rows = self._conn.execute(
            "SELECT id FROM chunks WHERE book_id = ? ORDER BY ordinal",
            (book_id,),
        ).fetchall()
        if len(rows) != len(embeddings):
            raise ValueError(
                f"Embedding count mismatch: {len(rows)} chunks vs "
                f"{len(embeddings)} vectors for book {book_id}"
            )
        n = 0
        for row, vec in zip(rows, embeddings):
            blob = np.array(vec, dtype=np.float32).tobytes() if vec else None
            self._conn.execute(
                "UPDATE chunks SET embedding = ?, embedding_model = ? WHERE id = ?",
                (blob, model, row["id"]),
            )
            n += 1
        self._conn.commit()
        return n

    def get_subject_chunks(self, subject_id: str) -> list[dict[str, Any]]:
        """Return ALL chunk rows for a subject (regardless of embedding).

        Used by the pure keyword ``locate``/``rank_books`` navigation tools
        (FR-031/FR-032) which need every chunk's text, not just indexed ones.
        """
        rows = self._conn.execute(
            "SELECT id, book_id, text, chapter, section, page "
            "FROM chunks WHERE subject_id = ? ORDER BY ordinal",
            (subject_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_chunks_by_provenance(
        self, subject_id: str, book_id: str | None, chapter: str | None
    ) -> list[dict[str, Any]]:
        """Return chunk rows scoped to a glossary term's provenance.

        Filters by ``book_id`` (and ``chapter`` when known) so an on-demand
        term explanation is grounded in the term's own source passages (FR-034).
        """
        sql = (
            "SELECT id, book_id, text, chapter, section, page "
            "FROM chunks WHERE subject_id = ?"
        )
        params: list[Any] = [subject_id]
        if book_id:
            sql += " AND book_id = ?"
            params.append(book_id)
        if chapter:
            sql += " AND chapter = ?"
            params.append(chapter)
        sql += " ORDER BY ordinal LIMIT 30"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_all_books(self) -> list[Any]:
        """Return every book regardless of subject (T016 listing)."""
        from .models import Book

        rows = self._conn.execute(
            "SELECT * FROM books ORDER BY created_at"
        ).fetchall()
        return [Book.from_dict(dict(r)) for r in rows]

    def delete_book(self, book_id: str) -> None:
        """Delete a book and its subject joins; chunks cascade (T016)."""
        self._conn.execute(
            "DELETE FROM subject_books WHERE book_id = ?", (book_id,)
        )
        self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self._conn.commit()

    def get_book(self, book_id: str) -> Any | None:
        """Return a single book by id, or ``None`` (T014)."""
        from .models import Book

        row = self._conn.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        return Book.from_dict(dict(row)) if row is not None else None

    def get_book_subject_id(self, book_id: str) -> str | None:
        """Return the subject_id linked to this book via subject_books, or None."""
        row = self._conn.execute(
            "SELECT subject_id FROM subject_books WHERE book_id = ? LIMIT 1",
            (book_id,),
        ).fetchone()
        return row["subject_id"] if row else None

    def list_books(
        self, subject_id: str, *, include_temp: bool = False
    ) -> list[Any]:
        """List books for a subject (temp docs filtered out by default)."""
        from .models import Book

        sql = (
            "SELECT b.* FROM books b "
            "JOIN subject_books sb ON sb.book_id = b.id "
            "WHERE sb.subject_id = ?"
        )
        if not include_temp:
            sql += " AND b.is_temp = 0"
        sql += " ORDER BY b.created_at"
        rows = self._conn.execute(sql, (subject_id,)).fetchall()
        return [Book.from_dict(dict(r)) for r in rows]

    def remove_book(self, subject_id: str, book_id: str) -> None:
        self._conn.execute(
            "DELETE FROM subject_books WHERE subject_id = ? AND book_id = ?",
            (subject_id, book_id),
        )
        self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Categories, corpora & temp-doc lifecycle (Phase 4)
    # ------------------------------------------------------------------
    # Conventions (matching the rest of this store):
    # - Duplicate names raise ValueError (case-insensitive, whitespace-
    #   stripped), mirroring create_subject.
    # - Unknown ids raise KeyError, mirroring _get_subject.
    # - Category/corpus rows are returned as plain dicts (no model class),
    #   like the chunk helpers; books come back as Book models.
    # - Membership cascades rely on SQLite foreign keys: ``PRAGMA
    #   foreign_keys=ON`` is set on every connection and both join tables
    #   declare ON DELETE CASCADE in both directions.

    def _require_book(self, book_id: str) -> None:
        if self.get_book(book_id) is None:
            raise KeyError(f"Unknown book: {book_id}")

    @staticmethod
    def _validate_label(name: str, kind: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{kind.capitalize()} name must be a non-empty string")
        return name.strip()

    def _create_label(self, table: str, kind: str, name: str) -> dict[str, Any]:
        name = self._validate_label(name, kind)
        existing = self._conn.execute(
            f"SELECT * FROM {table} WHERE name = ?", (name,)
        ).fetchone()
        if existing is not None:
            raise ValueError(f"{kind.capitalize()} already exists: {name}")
        cur = self._conn.execute(
            f"INSERT INTO {table} (name) VALUES (?)", (name,)
        )
        self._conn.commit()
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)

    def _list_labels(self, table: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT * FROM {table} ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_label(self, table: str, kind: str, label_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (label_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown {kind}: {label_id}")
        return dict(row)

    def _rename_label(
        self, table: str, kind: str, label_id: int, name: str
    ) -> dict[str, Any]:
        name = self._validate_label(name, kind)
        self._get_label(table, kind, label_id)  # KeyError if unknown
        clash = self._conn.execute(
            f"SELECT id FROM {table} WHERE name = ? AND id != ?",
            (name, label_id),
        ).fetchone()
        if clash is not None:
            raise ValueError(f"{kind.capitalize()} already exists: {name}")
        self._conn.execute(
            f"UPDATE {table} SET name = ? WHERE id = ?", (name, label_id)
        )
        self._conn.commit()
        return self._get_label(table, kind, label_id)

    def _delete_label(self, table: str, kind: str, label_id: int) -> bool:
        self._get_label(table, kind, label_id)  # KeyError if unknown
        self._conn.execute(f"DELETE FROM {table} WHERE id = ?", (label_id,))
        self._conn.commit()
        return True

    def _add_membership(
        self, join_table: str, book_col: str, label_col: str,
        book_id: str, label_id: int,
    ) -> bool:
        self._require_book(book_id)
        row = self._conn.execute(
            f"SELECT 1 FROM {join_table} WHERE {book_col} = ? AND {label_col} = ?",
            (book_id, label_id),
        ).fetchone()
        if row is not None:
            return False
        try:
            self._conn.execute(
                f"INSERT INTO {join_table} ({book_col}, {label_col}) VALUES (?, ?)",
                (book_id, label_id),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise KeyError(str(exc)) from exc  # dangling label id
        return True

    def _remove_membership(
        self, join_table: str, book_col: str, label_col: str,
        book_id: str, label_id: int,
    ) -> bool:
        cur = self._conn.execute(
            f"DELETE FROM {join_table} WHERE {book_col} = ? AND {label_col} = ?",
            (book_id, label_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def _list_books_via_join(
        self, join_table: str, label_col: str, label_id: int
    ) -> list[Any]:
        from .models import Book

        rows = self._conn.execute(
            f"SELECT b.* FROM books b "
            f"JOIN {join_table} j ON j.book_id = b.id "
            f"WHERE j.{label_col} = ? ORDER BY b.created_at",
            (label_id,),
        ).fetchall()
        return [Book.from_dict(dict(r)) for r in rows]

    def _list_labels_for_book(
        self, label_table: str, join_table: str, label_col: str, book_id: str
    ) -> list[dict[str, Any]]:
        self._require_book(book_id)
        rows = self._conn.execute(
            f"SELECT t.* FROM {label_table} t "
            f"JOIN {join_table} j ON j.{label_col} = t.id "
            f"WHERE j.book_id = ? ORDER BY t.name",
            (book_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Categories ---

    def create_category(self, name: str) -> dict[str, Any]:
        """Create a category; duplicates (case-insensitive) raise ValueError."""
        return self._create_label("categories", "category", name)

    def list_categories(self) -> list[dict[str, Any]]:
        return self._list_labels("categories")

    def rename_category(self, category_id: int, name: str) -> dict[str, Any]:
        return self._rename_label("categories", "category", category_id, name)

    def delete_category(self, category_id: int) -> bool:
        """Delete a category; its book memberships cascade (books survive)."""
        return self._delete_label("categories", "category", category_id)

    def get_category(self, category_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    # --- Corpora ---

    def create_corpus(self, name: str) -> dict[str, Any]:
        """Create a corpus; duplicates (case-insensitive) raise ValueError."""
        return self._create_label("corpora", "corpus", name)

    def list_corpora(self) -> list[dict[str, Any]]:
        return self._list_labels("corpora")

    def rename_corpus(self, corpus_id: int, name: str) -> dict[str, Any]:
        return self._rename_label("corpora", "corpus", corpus_id, name)

    def delete_corpus(self, corpus_id: int) -> bool:
        """Delete a corpus; its book memberships cascade (books survive)."""
        return self._delete_label("corpora", "corpus", corpus_id)

    def get_corpus(self, corpus_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM corpora WHERE id = ?", (corpus_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    # --- Membership: books <-> categories ---

    def add_book_to_category(self, book_id: str, category_id: int) -> bool:
        """Join a book to a category; False when already a member."""
        self._get_label("categories", "category", category_id)  # KeyError
        return self._add_membership(
            "book_categories", "book_id", "category_id", book_id, category_id
        )

    def remove_book_from_category(self, book_id: str, category_id: int) -> bool:
        """Detach a book from a category; False when not a member."""
        return self._remove_membership(
            "book_categories", "book_id", "category_id", book_id, category_id
        )

    def list_books_by_category(self, category_id: int) -> list[Any]:
        self._get_label("categories", "category", category_id)  # KeyError
        return self._list_books_via_join("book_categories", "category_id", category_id)

    def list_categories_for_book(self, book_id: str) -> list[dict[str, Any]]:
        return self._list_labels_for_book(
            "categories", "book_categories", "category_id", book_id
        )

    # --- Membership: books <-> corpora ---

    def add_book_to_corpus(self, book_id: str, corpus_id: int) -> bool:
        """Join a book to a corpus; False when already a member."""
        self._get_label("corpora", "corpus", corpus_id)  # KeyError
        return self._add_membership(
            "book_corpora", "book_id", "corpus_id", book_id, corpus_id
        )

    def remove_book_from_corpus(self, book_id: str, corpus_id: int) -> bool:
        """Detach a book from a corpus; False when not a member."""
        return self._remove_membership(
            "book_corpora", "book_id", "corpus_id", book_id, corpus_id
        )

    def list_books_by_corpus(self, corpus_id: int) -> list[Any]:
        self._get_label("corpora", "corpus", corpus_id)  # KeyError
        return self._list_books_via_join("book_corpora", "corpus_id", corpus_id)

    def list_corpora_for_book(self, book_id: str) -> list[dict[str, Any]]:
        return self._list_labels_for_book(
            "corpora", "book_corpora", "corpus_id", book_id
        )

    # --- Temp-doc lifecycle ---

    def mark_book_temporary(
        self, book_id: str, ttl_s: float, *, now: float | None = None
    ) -> Any:
        """Flag a book as temporary with expiry ``now + ttl_s`` (injectable clock)."""
        from .models import Book

        self._require_book(book_id)
        ts = time.time() if now is None else float(now)
        self._conn.execute(
            "UPDATE books SET is_temp = 1, expires_at = ? WHERE id = ?",
            (ts + float(ttl_s), book_id),
        )
        self._conn.commit()
        return self.get_book(book_id)

    def make_book_permanent(self, book_id: str) -> Any:
        """Clear the temp flag and expiry so the book never auto-purges."""
        self._require_book(book_id)
        self._conn.execute(
            "UPDATE books SET is_temp = 0, expires_at = NULL WHERE id = ?",
            (book_id,),
        )
        self._conn.commit()
        return self.get_book(book_id)

    def purge_expired_temp_books(self, *, now: float | None = None) -> int:
        """Delete expired temp books (and their data); returns the count.

        Reuses :meth:`delete_book` so cleanup matches the manual delete path
        exactly. Permanent books and unexpired temps are untouched.
        """
        ts = time.time() if now is None else float(now)
        rows = self._conn.execute(
            "SELECT id FROM books "
            "WHERE is_temp = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (ts,),
        ).fetchall()
        for row in rows:
            self.delete_book(row["id"])
        return len(rows)

    # ------------------------------------------------------------------
    # Concepts & progress
    # ------------------------------------------------------------------

    def upsert_concept(
        self, subject_id: str, name: str, path_rank: int | None = None
    ) -> Concept:
        self._get_subject(subject_id)
        concept_id = self._conn.execute(
            "SELECT id FROM concepts WHERE subject_id = ? AND name = ?",
            (subject_id, name),
        ).fetchone()
        if concept_id is None:
            concept = Concept(
                id=_uid(),
                subject_id=subject_id,
                name=name,
                path_rank=path_rank,
                summary=None,
            )
            self._conn.execute(
                "INSERT INTO concepts (id, subject_id, name, path_rank, summary) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    concept.id,
                    concept.subject_id,
                    concept.name,
                    concept.path_rank,
                    concept.summary,
                ),
            )
        else:
            cid = concept_id["id"]
            self._conn.execute(
                "UPDATE concepts SET path_rank = ? WHERE id = ?",
                (path_rank, cid),
            )
            concept = Concept(
                id=cid,
                subject_id=subject_id,
                name=name,
                path_rank=path_rank,
                summary=None,
            )
        self._conn.commit()
        return concept

    def list_concepts(self, subject_id: str) -> list[Concept]:
        rows = self._conn.execute(
            "SELECT * FROM concepts WHERE subject_id = ? "
            "ORDER BY path_rank, name",
            (subject_id,),
        ).fetchall()
        return [Concept.from_dict(dict(r)) for r in rows]

    def get_progress(self, subject_id: str) -> list[tuple[Concept, float | None]]:
        rows = self._conn.execute(
            "SELECT c.*, p.score AS p_score FROM concepts c "
            "LEFT JOIN progress p ON p.subject_id = c.subject_id "
            "AND p.concept_id = c.id "
            "WHERE c.subject_id = ? ORDER BY c.path_rank, c.name",
            (subject_id,),
        ).fetchall()
        result: list[tuple[Concept, float | None]] = []
        for r in rows:
            d = dict(r)
            score = d.pop("p_score", None)
            result.append((Concept.from_dict(d), float(score) if score is not None else None))
        return result

    def record_progress(self, concept_id: str, delta: float) -> None:
        row = self._conn.execute(
            "SELECT subject_id FROM concepts WHERE id = ?", (concept_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown concept: {concept_id}")
        subject_id = row["subject_id"]
        cur = self._conn.execute(
            "SELECT score FROM progress WHERE subject_id = ? AND concept_id = ?",
            (subject_id, concept_id),
        ).fetchone()
        current = float(cur["score"]) if cur is not None else 0.0
        new_score = max(0.0, min(100.0, current + float(delta)))
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO progress (subject_id, concept_id, score, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(subject_id, concept_id) DO UPDATE SET "
            "score = excluded.score, updated_at = excluded.updated_at",
            (subject_id, concept_id, new_score, now),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Glossary & relations
    # ------------------------------------------------------------------

    def upsert_glossary_term(
        self,
        subject_id: str,
        term: str,
        definition: str,
        book_id: str | None,
        chapter: str | None,
    ) -> GlossaryTerm:
        self._get_subject(subject_id)
        existing = self._conn.execute(
            "SELECT id FROM glossary_terms WHERE subject_id = ? AND term = ?",
            (subject_id, term),
        ).fetchone()
        if existing is None:
            term_obj = GlossaryTerm(
                id=_uid(),
                subject_id=subject_id,
                term=term,
                definition=definition,
                book_id=book_id,
                chapter=chapter,
                created_at=_now_iso(),
            )
            self._conn.execute(
                "INSERT INTO glossary_terms "
                "(id, subject_id, term, definition, book_id, chapter, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    term_obj.id,
                    term_obj.subject_id,
                    term_obj.term,
                    term_obj.definition,
                    term_obj.book_id,
                    term_obj.chapter,
                    term_obj.created_at,
                ),
            )
        else:
            tid = existing["id"]
            self._conn.execute(
                "UPDATE glossary_terms SET definition = ?, book_id = ?, chapter = ? "
                "WHERE id = ?",
                (definition, book_id, chapter, tid),
            )
            term_obj = GlossaryTerm(
                id=tid,
                subject_id=subject_id,
                term=term,
                definition=definition,
                book_id=book_id,
                chapter=chapter,
                created_at=_now_iso(),
            )
        self._conn.commit()
        return term_obj

    def list_glossary(self, subject_id: str) -> list[GlossaryTerm]:
        rows = self._conn.execute(
            "SELECT * FROM glossary_terms WHERE subject_id = ? ORDER BY term",
            (subject_id,),
        ).fetchall()
        return [GlossaryTerm.from_dict(dict(r)) for r in rows]

    def upsert_relation(
        self, subject_id: str, from_c: str, to_c: str, relation: str
    ) -> None:
        self._get_subject(subject_id)
        self._conn.execute(
            "INSERT OR IGNORE INTO knowledge_relations "
            "(id, subject_id, from_concept_id, to_concept_id, relation, source) "
            "VALUES (?, ?, ?, ?, ?, 'manual')",
            (_uid(), subject_id, from_c, to_c, relation),
        )
        self._conn.commit()

    def list_relations(self, subject_id: str) -> list[KnowledgeRelation]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_relations WHERE subject_id = ? "
            "ORDER BY from_concept_id, to_concept_id",
            (subject_id,),
        ).fetchall()
        return [KnowledgeRelation.from_dict(dict(r)) for r in rows]

    def has_relation(
        self, subject_id: str, from_c: str, to_c: str, relation: str
    ) -> bool:
        """Return True when an identical knowledge edge already exists (FR-035)."""
        row = self._conn.execute(
            "SELECT 1 FROM knowledge_relations "
            "WHERE subject_id = ? AND from_concept_id = ? "
            "AND to_concept_id = ? AND relation = ?",
            (subject_id, from_c, to_c, relation),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Flashcards & spaced-repetition schedule (US5)
    # ------------------------------------------------------------------

    def get_concept_by_name(self, subject_id: str, name: str) -> Concept | None:
        """Case-insensitive concept lookup by name within a subject."""
        row = self._conn.execute(
            "SELECT * FROM concepts WHERE subject_id = ? AND name = ? COLLATE NOCASE",
            (subject_id, name),
        ).fetchone()
        return Concept.from_dict(dict(row)) if row is not None else None

    def add_flashcard(self, flashcard: Flashcard) -> Flashcard:
        """Persist a flashcard and seed its review schedule (due today)."""
        self._get_subject(flashcard.subject_id)
        now = _now_iso()
        flashcard.created_at = flashcard.created_at or now
        self._conn.execute(
            "INSERT INTO flashcards "
            "(id, subject_id, concept_id, book_id, chapter, level, question, "
            "answer, source_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                flashcard.id,
                flashcard.subject_id,
                flashcard.concept_id,
                flashcard.book_id,
                flashcard.chapter,
                flashcard.level,
                flashcard.question,
                flashcard.answer,
                flashcard.source_hash,
                flashcard.created_at,
            ),
        )
        # A freshly prepared card is reviewable immediately (next_due = today);
        # the D8 ladder applies from the first grade onward.
        self._conn.execute(
            "INSERT OR IGNORE INTO review_schedule "
            "(flashcard_id, streak_index, next_due, last_result) "
            "VALUES (?, 0, ?, NULL)",
            (flashcard.id, date.today().isoformat()),
        )
        self._conn.commit()
        return flashcard

    def get_flashcard(self, flashcard_id: str) -> Flashcard | None:
        row = self._conn.execute(
            "SELECT * FROM flashcards WHERE id = ?", (flashcard_id,)
        ).fetchone()
        return Flashcard.from_dict(dict(row)) if row is not None else None

    def list_flashcards(self, subject_id: str) -> list[Flashcard]:
        rows = self._conn.execute(
            "SELECT * FROM flashcards WHERE subject_id = ? ORDER BY created_at",
            (subject_id,),
        ).fetchall()
        return [Flashcard.from_dict(dict(r)) for r in rows]

    def get_flashcard_by_source_hash(
        self, subject_id: str, source_hash: str
    ) -> Flashcard | None:
        """Idempotency check for prepare_knowledge (FR-024 source_hash)."""
        row = self._conn.execute(
            "SELECT * FROM flashcards WHERE subject_id = ? AND source_hash = ?",
            (subject_id, source_hash),
        ).fetchone()
        return Flashcard.from_dict(dict(row)) if row is not None else None

    def list_due_review_items(
        self, subject_id: str, today: date | None = None
    ) -> list[ReviewItem]:
        """Review-schedule rows due for the subject (``next_due <= today``)."""
        today = today or date.today()
        rows = self._conn.execute(
            "SELECT rs.* FROM review_schedule rs "
            "JOIN flashcards f ON f.id = rs.flashcard_id "
            "WHERE f.subject_id = ? AND rs.next_due <= ? "
            "ORDER BY rs.next_due",
            (subject_id, today.isoformat()),
        ).fetchall()
        return [ReviewItem.from_dict(dict(r)) for r in rows]

    def list_due_reviews(
        self, subject_id: str, today: date | None = None
    ) -> list[Flashcard]:
        """Flashcards due for review (``next_due <= today``) — for the UI/REST."""
        today = today or date.today()
        rows = self._conn.execute(
            "SELECT f.* FROM flashcards f "
            "JOIN review_schedule rs ON rs.flashcard_id = f.id "
            "WHERE f.subject_id = ? AND rs.next_due <= ? "
            "ORDER BY rs.next_due",
            (subject_id, today.isoformat()),
        ).fetchall()
        return [Flashcard.from_dict(dict(r)) for r in rows]

    def get_review_item(self, flashcard_id: str) -> ReviewItem | None:
        row = self._conn.execute(
            "SELECT * FROM review_schedule WHERE flashcard_id = ?", (flashcard_id,)
        ).fetchone()
        return ReviewItem.from_dict(dict(row)) if row is not None else None

    def upsert_review_schedule(
        self,
        flashcard_id: str,
        streak_index: int,
        next_due: str,
        last_result: str | None,
    ) -> None:
        """Insert or update a flashcard's review schedule row (D8 ladder)."""
        self._conn.execute(
            "INSERT INTO review_schedule "
            "(flashcard_id, streak_index, next_due, last_result) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(flashcard_id) DO UPDATE SET "
            "streak_index = excluded.streak_index, "
            "next_due = excluded.next_due, "
            "last_result = excluded.last_result",
            (flashcard_id, streak_index, next_due, last_result),
        )
        self._conn.commit()

    def get_glossary_term(
        self, subject_id: str, term: str
    ) -> GlossaryTerm | None:
        """Case-insensitive glossary lookup by term (FR-034 idempotency)."""
        row = self._conn.execute(
            "SELECT * FROM glossary_terms WHERE subject_id = ? AND term = ? "
            "COLLATE NOCASE",
            (subject_id, term),
        ).fetchone()
        return GlossaryTerm.from_dict(dict(row)) if row is not None else None

    # ------------------------------------------------------------------
    # Quizzes & exams (US5)
    # ------------------------------------------------------------------

    def add_quiz(self, quiz: Quiz) -> Quiz:
        self._get_subject(quiz.subject_id)
        self._conn.execute(
            "INSERT INTO quizzes "
            "(id, subject_id, kind, status, allow_help, time_limit_s, "
            "started_at, finished_at, score, report) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quiz.id,
                quiz.subject_id,
                quiz.kind,
                quiz.status,
                int(quiz.allow_help),
                quiz.time_limit_s,
                quiz.started_at,
                quiz.finished_at,
                quiz.score,
                _json(quiz.report) if quiz.report is not None else None,
            ),
        )
        self._conn.commit()
        return quiz

    def get_quiz(self, quiz_id: str) -> Quiz | None:
        row = self._conn.execute(
            "SELECT * FROM quizzes WHERE id = ?", (quiz_id,)
        ).fetchone()
        return Quiz.from_dict(dict(row)) if row is not None else None

    def update_quiz(self, quiz: Quiz) -> None:
        self._conn.execute(
            "UPDATE quizzes SET status = ?, finished_at = ?, score = ?, "
            "report = ? WHERE id = ?",
            (
                quiz.status,
                quiz.finished_at,
                quiz.score,
                _json(quiz.report) if quiz.report is not None else None,
                quiz.id,
            ),
        )
        self._conn.commit()

    def add_quiz_question(self, question: QuizQuestion) -> QuizQuestion:
        self._conn.execute(
            "INSERT INTO quiz_questions "
            "(id, quiz_id, type, payload, answer, concept_id, points) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                question.id,
                question.quiz_id,
                question.type,
                _json(question.payload),
                _json(question.answer),
                question.concept_id,
                question.points,
            ),
        )
        self._conn.commit()
        return question

    def list_quiz_questions(self, quiz_id: str) -> list[QuizQuestion]:
        rows = self._conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY ordinal",
            (quiz_id,),
        ).fetchall()
        return [QuizQuestion.from_dict(dict(r)) for r in rows]

    def add_quiz_answer(self, answer: QuizAnswer) -> QuizAnswer:
        self._conn.execute(
            "INSERT OR REPLACE INTO quiz_answers "
            "(question_id, response, verdict, awarded) "
            "VALUES (?, ?, ?, ?)",
            (
                answer.question_id,
                _json(answer.response),
                answer.verdict,
                answer.awarded,
            ),
        )
        self._conn.commit()
        return answer

    # ------------------------------------------------------------------
    # Tutoring sessions
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Conversations nommées (005-platform-ui-library)
    # ------------------------------------------------------------------

    def rename_conversation(self, session_id: str, title: str) -> bool:
        """Set a conversation's display title. Returns False if unknown."""
        cur = self._conn.execute(
            "UPDATE tutoring_sessions SET title = ? WHERE id = ?",
            (title, session_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def touch_conversation(self, session_id: str) -> None:
        """Bump ``updated_at`` (called after each ask in a conversation)."""
        self._conn.execute(
            "UPDATE tutoring_sessions SET updated_at = ? WHERE id = ?",
            (time.time(), session_id),
        )
        self._conn.commit()

    def list_conversations(self) -> list[dict[str, Any]]:
        """All sessions as conversations, most recently active first."""
        rows = self._conn.execute(
            "SELECT s.*, sub.name AS subject_name "
            "FROM tutoring_sessions s "
            "LEFT JOIN subjects sub ON sub.id = s.subject_id "
            "ORDER BY COALESCE(s.updated_at, 0) DESC, s.started_at DESC"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["message_count"] = len(self.get_session_transcript(d["id"]))
            out.append(d)
        return out

    def delete_conversation(self, session_id: str) -> bool:
        """Delete a conversation: sources, transcript file, session row."""
        session = self.get_tutoring_session(session_id)
        if session is None:
            return False
        self._conn.execute(
            "DELETE FROM conversation_sources WHERE conversation_id = ?",
            (session_id,),
        )
        self._conn.commit()
        if session.transcript_path:
            try:
                Path(session.transcript_path).unlink(missing_ok=True)
            except OSError:
                pass
        cur = self._conn.execute(
            "DELETE FROM tutoring_sessions WHERE id = ?", (session_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_conversation_sources(
        self, session_id: str, book_ids: list[str]
    ) -> int:
        """Replace the active-source set of a conversation (deduplicated).

        Unknown book ids are silently ignored ; returns the number of
        sources actually set. Never touches books or embeddings.
        """
        known = {r["id"] for r in self._conn.execute("SELECT id FROM books")}
        wanted: list[str] = []
        for bid in book_ids:
            b = str(bid)
            if b in known and b not in wanted:
                wanted.append(b)
        self._conn.execute(
            "DELETE FROM conversation_sources WHERE conversation_id = ?",
            (session_id,),
        )
        for bid in wanted:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversation_sources "
                "(conversation_id, book_id) VALUES (?, ?)",
                (session_id, bid),
            )
        self._conn.commit()
        return len(wanted)

    def get_conversation_source_ids(self, session_id: str) -> list[str]:
        """Active book ids of a conversation ([] if none/unknown)."""
        rows = self._conn.execute(
            "SELECT book_id FROM conversation_sources WHERE conversation_id = ?",
            (session_id,),
        )
        return [r["book_id"] for r in rows]

    def append_conversation_message(
        self, session_id: str, role: str, text: str
    ) -> None:
        """Append ``{role, text, ts}`` au transcript JSON de la conversation.

        Crée ``<tutor_dir>/transcripts/<id>.json`` au premier message et met
        ``transcript_path`` à jour si vide. Best-effort : ne lève jamais vers
        le run (perte de journal préférable à une réponse cassée).
        """
        session = self.get_tutoring_session(session_id)
        if session is None:
            return
        tdir = self.tutor_dir / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{session_id}.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
            if not isinstance(data, list):
                data = []
            data.append({"role": role, "text": text, "ts": time.time()})
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            if not session.transcript_path:
                self._conn.execute(
                    "UPDATE tutoring_sessions SET transcript_path = ? WHERE id = ?",
                    (str(p), session_id),
                )
                self._conn.commit()
        except OSError:
            pass

    def create_tutoring_session(
        self,
        subject_id: str,
        *,
        title: str = "",
        session_id: str | None = None,
    ) -> TutoringSession:
        """Open a new ``active`` tutoring session row (US2 / 005 conversations).

        ``session_id`` permet d'ouvrir une conversation avec un identifiant
        imposé (création explicite via l'API conversations) ; ``title`` nomme
        la conversation (défaut : sans titre).
        """
        self._get_subject(subject_id)
        now = _now_iso()
        sid = session_id or _uid()
        ts = time.time()
        self._conn.execute(
            "INSERT INTO tutoring_sessions (id, subject_id, started_at, "
            "last_active_at, status, title, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (sid, subject_id, now, now, title, ts),
        )
        self._conn.commit()
        return TutoringSession(
            id=sid, subject_id=subject_id, started_at=now,
            last_active_at=now, status="active", title=title,
            updated_at=ts,
        )

    def get_tutoring_session(self, session_id: str) -> TutoringSession | None:
        row = self._conn.execute(
            "SELECT * FROM tutoring_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return TutoringSession.from_dict(dict(row)) if row is not None else None

    def touch_tutoring_session(self, session_id: str) -> None:
        """Bump ``last_active_at`` (kept for continuity; no-op if unknown)."""
        self._conn.execute(
            "UPDATE tutoring_sessions SET last_active_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Session listing (US6 / T045 history panel) — summaries live in the
    # dedicated ``Session summaries`` section below (save_session_summary,
    # get_session_summary, list_session_summaries, close_session_row).
    # ------------------------------------------------------------------

    def list_sessions(self, subject_id: str) -> list[TutoringSession]:
        """List tutoring sessions for a subject, newest first (history panel)."""
        rows = self._conn.execute(
            "SELECT * FROM tutoring_sessions WHERE subject_id = ? "
            "ORDER BY started_at DESC",
            (subject_id,),
        ).fetchall()
        return [TutoringSession.from_dict(dict(r)) for r in rows]

    def close_session_row(self, session_id: str) -> None:
        """Mark a tutoring session ``closed`` and stamp ``closed_at`` (FR-028)."""
        self._conn.execute(
            "UPDATE tutoring_sessions SET status = 'closed', closed_at = ? "
            "WHERE id = ?",
            (_now_iso(), session_id),
        )
        self._conn.commit()

    def get_active_session(self, subject_id: str) -> TutoringSession | None:
        """Return the most recent ``active`` session for a subject, if any."""
        row = self._conn.execute(
            "SELECT * FROM tutoring_sessions WHERE subject_id = ? AND status = 'active' "
            "ORDER BY started_at DESC LIMIT 1",
            (subject_id,),
        ).fetchone()
        return TutoringSession.from_dict(dict(row)) if row is not None else None

    # ------------------------------------------------------------------
    # Session summaries (US6 / FR-028 / FR-029)
    # ------------------------------------------------------------------

    def save_session_summary(self, summary: SessionSummary) -> SessionSummary:
        """Persist (or replace) a session's end-of-session summary."""
        self._conn.execute(
            "INSERT OR REPLACE INTO session_summaries "
            "(session_id, concepts_studied, concepts_mastered, to_review, produced_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                summary.session_id,
                _json(summary.concepts_studied),
                _json(summary.concepts_mastered),
                _json(summary.to_review),
                summary.produced_at,
            ),
        )
        self._conn.commit()
        return summary

    def get_session_summary(self, session_id: str) -> SessionSummary | None:
        """Return a single session summary, or ``None`` (US6)."""
        row = self._conn.execute(
            "SELECT * FROM session_summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        return SessionSummary.from_dict(dict(row)) if row is not None else None

    def list_session_summaries(self, subject_id: str) -> list[SessionSummary]:
        """Return session summaries for a subject, newest first (US6 history)."""
        rows = self._conn.execute(
            "SELECT ss.* FROM session_summaries ss "
            "JOIN tutoring_sessions ts ON ts.id = ss.session_id "
            "WHERE ts.subject_id = ? ORDER BY ss.produced_at DESC",
            (subject_id,),
        ).fetchall()
        return [SessionSummary.from_dict(dict(r)) for r in rows]

    def get_session_transcript(self, session_id: str) -> list[dict[str, Any]]:
        """Load a session's transcript JSON file, or ``[]`` if absent/invalid."""
        session = self.get_tutoring_session(session_id)
        if session is None or not session.transcript_path:
            return []
        p = Path(session.transcript_path)
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def list_session_attempts(
        self, subject_id: str, since_iso: str | None = None
    ) -> list[ExerciseAttempt]:
        """Exercise attempts for a subject, optionally since ``since_iso`` (US6)."""
        sql = (
            "SELECT ea.* FROM exercise_attempts ea "
            "JOIN exercises e ON ea.exercise_id = e.id "
            "WHERE e.subject_id = ?"
        )
        params: list[Any] = [subject_id]
        if since_iso:
            sql += " AND ea.created_at >= ?"
            params.append(since_iso)
        sql += " ORDER BY ea.created_at"
        rows = self._conn.execute(sql, params).fetchall()
        return [ExerciseAttempt.from_dict(dict(r)) for r in rows]

    def list_session_reviews(
        self, subject_id: str
    ) -> list[tuple[str, str | None]]:
        """Review grades for a subject as ``(concept_id, last_result)`` (US6)."""
        rows = self._conn.execute(
            "SELECT rs.last_result, f.concept_id FROM review_schedule rs "
            "JOIN flashcards f ON f.id = rs.flashcard_id "
            "WHERE f.subject_id = ?",
            (subject_id,),
        ).fetchall()
        return [(str(r["concept_id"]), r["last_result"]) for r in rows]

    def list_session_quiz_answers(
        self, subject_id: str
    ) -> list[tuple[str, str]]:
        """Quiz/exam answers for a subject as ``(concept_id, verdict)`` (US6)."""
        rows = self._conn.execute(
            "SELECT qa.verdict, qq.concept_id FROM quiz_answers qa "
            "JOIN quiz_questions qq ON qq.id = qa.question_id "
            "JOIN quizzes q ON q.id = qq.quiz_id "
            "WHERE q.subject_id = ?",
            (subject_id,),
        ).fetchall()
        return [(str(r["concept_id"]), str(r["verdict"])) for r in rows]

    # ------------------------------------------------------------------
    # Learning paths (Feature 006 — adaptive learning)
    # ------------------------------------------------------------------

    def create_learning_path(self, subject_id: str, title: str, description: str = "") -> LearningPath:
        """Create a new learning path for a subject."""
        path = LearningPath(
            id=_uid(),
            subject_id=subject_id,
            title=title,
            description=description,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._conn.execute(
            "INSERT INTO learning_paths (id, subject_id, title, description, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path.id, path.subject_id, path.title, path.description, path.status, path.created_at, path.updated_at),
        )
        self._conn.commit()
        return path

    def get_learning_path(self, path_id: str) -> LearningPath | None:
        row = self._conn.execute("SELECT * FROM learning_paths WHERE id = ?", (path_id,)).fetchone()
        return LearningPath.from_dict(dict(row)) if row is not None else None

    def list_learning_paths(self, subject_id: str) -> list[LearningPath]:
        rows = self._conn.execute(
            "SELECT * FROM learning_paths WHERE subject_id = ? ORDER BY created_at DESC", (subject_id,)
        ).fetchall()
        return [LearningPath.from_dict(dict(r)) for r in rows]

    def update_learning_path(self, path_id: str, *, title: str | None = None, description: str | None = None, status: str | None = None) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if title is not None:
            updates.append("title = ?"); params.append(title)
        if description is not None:
            updates.append("description = ?"); params.append(description)
        if status is not None:
            updates.append("status = ?"); params.append(status)
        if updates:
            updates.append("updated_at = ?"); params.append(_now_iso())
            params.append(path_id)
            self._conn.execute(f"UPDATE learning_paths SET {', '.join(updates)} WHERE id = ?", params)
            self._conn.commit()

    def delete_learning_path(self, path_id: str) -> None:
        self._conn.execute("DELETE FROM learning_paths WHERE id = ?", (path_id,))
        self._conn.commit()

    def add_path_step(self, path_id: str, activity_type: str, activity_id: str, title: str = "", ordinal: int | None = None) -> PathStep:
        if ordinal is None:
            row = self._conn.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM path_steps WHERE path_id = ?", (path_id,)).fetchone()
            ordinal = row[0] if row else 0
        step = PathStep(
            id=_uid(), path_id=path_id, ordinal=ordinal,
            activity_type=activity_type, activity_id=activity_id, title=title,
        )
        self._conn.execute(
            "INSERT INTO path_steps (id, path_id, ordinal, activity_type, activity_id, title, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (step.id, step.path_id, step.ordinal, step.activity_type, step.activity_id, step.title, step.status),
        )
        self._conn.commit()
        return step

    def list_path_steps(self, path_id: str) -> list[PathStep]:
        rows = self._conn.execute(
            "SELECT * FROM path_steps WHERE path_id = ? ORDER BY ordinal", (path_id,)
        ).fetchall()
        return [PathStep.from_dict(dict(r)) for r in rows]

    def get_path_step(self, step_id: str) -> PathStep | None:
        row = self._conn.execute("SELECT * FROM path_steps WHERE id = ?", (step_id,)).fetchone()
        return PathStep.from_dict(dict(row)) if row is not None else None

    def update_path_step(self, step_id: str, *, status: str | None = None, ordinal: int | None = None) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?"); params.append(status)
            if status == "completed":
                updates.append("completed_at = ?"); params.append(_now_iso())
        if ordinal is not None:
            updates.append("ordinal = ?"); params.append(ordinal)
        if updates:
            params.append(step_id)
            self._conn.execute(f"UPDATE path_steps SET {', '.join(updates)} WHERE id = ?", params)
            self._conn.commit()

    def delete_path_step(self, step_id: str) -> None:
        self._conn.execute("DELETE FROM path_steps WHERE id = ?", (step_id,))
        self._conn.commit()

    def reorder_path_steps(self, path_id: str, step_ids: list[str]) -> None:
        for idx, sid in enumerate(step_ids):
            self._conn.execute("UPDATE path_steps SET ordinal = ? WHERE id = ? AND path_id = ?", (idx, sid, path_id))
        self._conn.commit()

    def get_subject_domain(self, subject_id: str) -> str:
        row = self._conn.execute("SELECT domain FROM subjects WHERE id = ?", (subject_id,)).fetchone()
        return str(row["domain"]) if row else "generique"

    def set_subject_domain(self, subject_id: str, domain: str) -> None:
        self._conn.execute("UPDATE subjects SET domain = ? WHERE id = ?", (domain, subject_id))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
