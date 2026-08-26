"""Dataclass entities for the local AI tutor (data-model.md).

Mirrors the persistence style of ``core/projects.py``: every entity exposes a
``to_dict()`` (native Python values, JSON-able) and a tolerant ``from_dict()``
classmethod that never raises on missing/extra keys. Storage layers are
responsible for JSON-encoding list/dict fields into TEXT columns.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _uid() -> str:
    """Generate an 8-char hex id (data-model.md: ``uuid4().hex[:8]``)."""
    return uuid.uuid4().hex[:8]


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _coerce_json(value: Any, default: Any) -> Any:
    """Return ``value`` parsed from JSON when it is a JSON string, else as-is.

    Tolerant helper for ``from_dict`` so callers may pass either a JSON string
    (as stored in the DB) or an already-decoded list/dict.
    """
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


# ----------------------------------------------------------------------
# Subjects & documents
# ----------------------------------------------------------------------


@dataclass
class Subject:
    """A learning subject (data-model.md: ``subjects``)."""

    id: str
    name: str
    created_at: str
    last_used_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Subject":
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            created_at=str(raw.get("created_at", "")),
            last_used_at=str(raw.get("last_used_at", "")),
        )


@dataclass
class Book:
    """An imported book (data-model.md: ``books``)."""

    id: str
    title: str
    source_path: str
    format: str  # txt | md | pdf | epub
    fingerprint: str  # sha256 of normalized text
    status: str = "pending"  # pending | indexing | done | failed
    error: str | None = None
    chunks_done: int = 0
    chunks_total: int = 0
    created_at: str = ""
    retry_count: int = 0
    next_retry_at: str | None = None
    last_error_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source_path": self.source_path,
            "format": self.format,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "error": self.error,
            "chunks_done": self.chunks_done,
            "chunks_total": self.chunks_total,
            "created_at": self.created_at,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "last_error_at": self.last_error_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Book":
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title", "")),
            source_path=str(raw.get("source_path", "")),
            format=str(raw.get("format", "")),
            fingerprint=str(raw.get("fingerprint", "")),
            status=str(raw.get("status", "pending")),
            error=raw.get("error"),
            chunks_done=int(raw.get("chunks_done", 0)),
            chunks_total=int(raw.get("chunks_total", 0)),
            created_at=str(raw.get("created_at", "")),
            retry_count=int(raw.get("retry_count", 0)),
            next_retry_at=raw.get("next_retry_at"),
            last_error_at=raw.get("last_error_at"),
        )


@dataclass
class Chunk:
    """A chunk of a book (data-model.md: ``chunks``)."""

    id: str
    subject_id: str
    book_id: str
    ordinal: int
    text: str
    text_hash: str
    chapter: str | None = None
    section: str | None = None
    page: int | None = None
    position: float = 0.0
    difficulty: str | None = None
    content_type: str = "prose"
    embedding: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "book_id": self.book_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "text_hash": self.text_hash,
            "chapter": self.chapter,
            "section": self.section,
            "page": self.page,
            "position": self.position,
            "difficulty": self.difficulty,
            "content_type": self.content_type,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Chunk":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            book_id=str(raw.get("book_id", "")),
            ordinal=int(raw.get("ordinal", 0)),
            text=str(raw.get("text", "")),
            text_hash=str(raw.get("text_hash", "")),
            chapter=raw.get("chapter"),
            section=raw.get("section"),
            page=raw.get("page"),
            position=float(raw.get("position", 0.0)),
            difficulty=raw.get("difficulty"),
            content_type=str(raw.get("content_type", "prose")),
            embedding=raw.get("embedding"),
        )


# ----------------------------------------------------------------------
# Concepts & progress
# ----------------------------------------------------------------------


@dataclass
class Concept:
    """A concept within a subject (data-model.md: ``concepts``)."""

    id: str
    subject_id: str
    name: str
    path_rank: int | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "name": self.name,
            "path_rank": self.path_rank,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Concept":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            name=str(raw.get("name", "")),
            path_rank=raw.get("path_rank"),
            summary=raw.get("summary"),
        )


@dataclass
class SkillProgress:
    """Mastery score for a concept (data-model.md: ``progress``)."""

    subject_id: str
    concept_id: str
    score: float  # 0.0 - 100.0
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "concept_id": self.concept_id,
            "score": self.score,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillProgress":
        return cls(
            subject_id=str(raw.get("subject_id", "")),
            concept_id=str(raw.get("concept_id", "")),
            score=float(raw.get("score", 0.0)),
            updated_at=str(raw.get("updated_at", "")),
        )


# ----------------------------------------------------------------------
# Exercises
# ----------------------------------------------------------------------


@dataclass
class Exercise:
    """A generated exercise (data-model.md: ``exercises``)."""

    id: str
    subject_id: str
    concept_id: str
    difficulty: str  # easy | medium | hard
    statement: str
    solution: str = ""
    hint_level: int = 0  # 0-3
    hints: list[str] = None  # type: ignore[assignment]
    status: str = "open"  # open | solved | given_up
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.hints is None:
            self.hints = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "concept_id": self.concept_id,
            "difficulty": self.difficulty,
            "statement": self.statement,
            "solution": self.solution,
            "hint_level": self.hint_level,
            "hints": self.hints,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Exercise":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            concept_id=str(raw.get("concept_id", "")),
            difficulty=str(raw.get("difficulty", "medium")),
            statement=str(raw.get("statement", "")),
            solution=str(raw.get("solution", "")),
            hint_level=int(raw.get("hint_level", 0)),
            hints=list(_coerce_json(raw.get("hints", []), [])),
            status=str(raw.get("status", "open")),
            created_at=str(raw.get("created_at", "")),
        )


@dataclass
class ExerciseAttempt:
    """A learner attempt at an exercise (data-model.md: ``exercise_attempts``)."""

    id: str
    exercise_id: str
    verdict: str  # correct | incorrect | partial
    answer: str = ""
    feedback: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "exercise_id": self.exercise_id,
            "verdict": self.verdict,
            "answer": self.answer,
            "feedback": self.feedback,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExerciseAttempt":
        return cls(
            id=str(raw["id"]),
            exercise_id=str(raw.get("exercise_id", "")),
            verdict=str(raw.get("verdict", "incorrect")),
            answer=str(raw.get("answer", "")),
            feedback=str(raw.get("feedback", "")),
            created_at=str(raw.get("created_at", "")),
        )


# ----------------------------------------------------------------------
# Flashcards & spaced repetition
# ----------------------------------------------------------------------


@dataclass
class Flashcard:
    """A spaced-repetition card (data-model.md: ``flashcards``)."""

    id: str
    subject_id: str
    concept_id: str
    level: str  # beginner | intermediate | advanced | expert
    question: str
    answer: str
    source_hash: str
    book_id: str | None = None
    chapter: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "concept_id": self.concept_id,
            "level": self.level,
            "question": self.question,
            "answer": self.answer,
            "source_hash": self.source_hash,
            "book_id": self.book_id,
            "chapter": self.chapter,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Flashcard":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            concept_id=str(raw.get("concept_id", "")),
            level=str(raw.get("level", "intermediate")),
            question=str(raw.get("question", "")),
            answer=str(raw.get("answer", "")),
            source_hash=str(raw.get("source_hash", "")),
            book_id=raw.get("book_id"),
            chapter=raw.get("chapter"),
            created_at=str(raw.get("created_at", "")),
        )


@dataclass
class ReviewItem:
    """Spaced-repetition schedule for a flashcard (data-model.md: ``review_schedule``)."""

    flashcard_id: str
    streak_index: int  # 0-4 -> ladder [1,2,5,12,30]
    next_due: str  # ISO date
    last_result: str | None = None  # success | failure

    def to_dict(self) -> dict[str, Any]:
        return {
            "flashcard_id": self.flashcard_id,
            "streak_index": self.streak_index,
            "next_due": self.next_due,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewItem":
        return cls(
            flashcard_id=str(raw.get("flashcard_id", "")),
            streak_index=int(raw.get("streak_index", 0)),
            next_due=str(raw.get("next_due", "")),
            last_result=raw.get("last_result"),
        )


# ----------------------------------------------------------------------
# Quizzes & exams
# ----------------------------------------------------------------------


@dataclass
class Quiz:
    """A quiz or exam container (data-model.md: ``quizzes``)."""

    id: str
    subject_id: str
    kind: str = "quiz"  # quiz | exam
    status: str = "created"  # created | in_progress | completed
    allow_help: bool = True
    time_limit_s: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    score: float | None = None
    report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "kind": self.kind,
            "status": self.status,
            "allow_help": self.allow_help,
            "time_limit_s": self.time_limit_s,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "score": self.score,
            "report": self.report,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Quiz":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            kind=str(raw.get("kind", "quiz")),
            status=str(raw.get("status", "created")),
            allow_help=bool(raw.get("allow_help", True)),
            time_limit_s=raw.get("time_limit_s"),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            score=raw.get("score"),
            report=_coerce_json(raw.get("report"), None),
        )


@dataclass
class QuizQuestion:
    """A single question inside a quiz (data-model.md: ``quiz_questions``)."""

    id: str
    quiz_id: str
    type: str  # mcq | true_false | open | matching | code
    payload: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    concept_id: str | None = None
    points: float = 0.0

    def __post_init__(self) -> None:
        if self.payload is None:
            self.payload = {}
        if self.answer is None:
            self.answer = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "quiz_id": self.quiz_id,
            "type": self.type,
            "payload": self.payload,
            "answer": self.answer,
            "concept_id": self.concept_id,
            "points": self.points,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuizQuestion":
        return cls(
            id=str(raw["id"]),
            quiz_id=str(raw.get("quiz_id", "")),
            type=str(raw.get("type", "open")),
            payload=_coerce_json(raw.get("payload", {}), {}),
            answer=_coerce_json(raw.get("answer", {}), {}),
            concept_id=raw.get("concept_id"),
            points=float(raw.get("points", 0.0)),
        )


@dataclass
class QuizAnswer:
    """A learner answer to a quiz question (data-model.md: ``quiz_answers``)."""

    question_id: str
    verdict: str  # correct | incorrect | partial
    response: dict[str, Any] | None = None
    awarded: float = 0.0

    def __post_init__(self) -> None:
        if self.response is None:
            self.response = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "verdict": self.verdict,
            "response": self.response,
            "awarded": self.awarded,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuizAnswer":
        return cls(
            question_id=str(raw.get("question_id", "")),
            verdict=str(raw.get("verdict", "incorrect")),
            response=_coerce_json(raw.get("response", {}), {}),
            awarded=float(raw.get("awarded", 0.0)),
        )


@dataclass
class ExamSession:
    """A timed, assistance-free exam (data-model.md: ``quizzes`` kind='exam').

    Stored in the ``quizzes`` table with ``kind='exam'``; kept as a distinct
    dataclass for type clarity in the service layer.
    """

    id: str
    subject_id: str
    time_limit_s: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    score: float | None = None
    report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "time_limit_s": self.time_limit_s,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "score": self.score,
            "report": self.report,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExamSession":
        return cls(
            id=str(raw.get("id", "")),
            subject_id=str(raw.get("subject_id", "")),
            time_limit_s=raw.get("time_limit_s"),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            score=raw.get("score"),
            report=_coerce_json(raw.get("report"), None),
        )


# ----------------------------------------------------------------------
# Tutoring sessions & summaries
# ----------------------------------------------------------------------


@dataclass
class TutoringSession:
    """A tutoring session (data-model.md: ``tutoring_sessions``)."""

    id: str
    subject_id: str
    started_at: str
    last_active_at: str
    status: str = "active"  # active | closed
    transcript_path: str | None = None
    # Conversation nommée (005-platform-ui-library) : titre éditable +
    # horodatage de dernière activité mis à jour à chaque ask.
    title: str = ""
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "started_at": self.started_at,
            "last_active_at": self.last_active_at,
            "status": self.status,
            "transcript_path": self.transcript_path,
            "title": self.title,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TutoringSession":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            started_at=str(raw.get("started_at", "")),
            last_active_at=str(raw.get("last_active_at", "")),
            status=str(raw.get("status", "active")),
            transcript_path=raw.get("transcript_path"),
            title=str(raw.get("title", "") or ""),
            updated_at=raw.get("updated_at"),
        )


@dataclass
class SessionSummary:
    """End-of-session summary (data-model.md: ``session_summaries``).

    Field names mirror the DB columns (``concepts_studied`` …); ``to_dict``
    exposes the contract-friendly keys ``studied`` / ``mastered`` / ``to_review``
    (contracts/tutor-core-api.md) so the wire shape matches the spec while the
    dataclass stays aligned with the schema.
    """

    session_id: str
    concepts_studied: list[str]
    concepts_mastered: list[str]
    to_review: list[str]
    produced_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "studied": self.concepts_studied,
            "mastered": self.concepts_mastered,
            "to_review": self.to_review,
            "produced_at": self.produced_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionSummary":
        return cls(
            session_id=str(raw.get("session_id", "")),
            concepts_studied=list(
                _coerce_json(raw.get("concepts_studied", raw.get("studied", [])), [])
            ),
            concepts_mastered=list(
                _coerce_json(raw.get("concepts_mastered", raw.get("mastered", [])), [])
            ),
            to_review=list(
                _coerce_json(raw.get("to_review", raw.get("to_review", [])), [])
            ),
            produced_at=str(raw.get("produced_at", "")),
        )


@dataclass
class ResumeBriefing:
    """Resume briefing assembled from the last summary + open gaps (FR-029).

    Pure data assembly — carries no model output. ``last_topic`` is the most
    recently studied notion, ``difficulties`` the open gaps, and ``proposal`` a
    suggested next action. ``last_summary`` (optional) echoes the most recent
    :class:`SessionSummary` for convenience.
    """

    last_topic: str
    difficulties: list[str]
    proposal: str
    last_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_topic": self.last_topic,
            "difficulties": self.difficulties,
            "proposal": self.proposal,
            "last_summary": self.last_summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResumeBriefing":
        return cls(
            last_topic=str(raw.get("last_topic", "")),
            difficulties=list(_coerce_json(raw.get("difficulties", []), [])),
            proposal=str(raw.get("proposal", "")),
            last_summary=raw.get("last_summary"),
        )


# ----------------------------------------------------------------------
# Glossary & knowledge graph
# ----------------------------------------------------------------------


@dataclass
class GlossaryTerm:
    """A glossary definition (data-model.md: ``glossary_terms``)."""

    id: str
    subject_id: str
    term: str
    definition: str
    book_id: str | None = None
    chapter: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "term": self.term,
            "definition": self.definition,
            "book_id": self.book_id,
            "chapter": self.chapter,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GlossaryTerm":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            term=str(raw.get("term", "")),
            definition=str(raw.get("definition", "")),
            book_id=raw.get("book_id"),
            chapter=raw.get("chapter"),
            created_at=str(raw.get("created_at", "")),
        )


@dataclass
class KnowledgeRelation:
    """An edge in the knowledge graph (data-model.md: ``knowledge_relations``)."""

    id: str
    subject_id: str
    from_concept_id: str
    to_concept_id: str
    relation: str  # prerequisite | related | part_of
    source: str = "indexing"  # indexing | manual

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "from_concept_id": self.from_concept_id,
            "to_concept_id": self.to_concept_id,
            "relation": self.relation,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "KnowledgeRelation":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            from_concept_id=str(raw.get("from_concept_id", "")),
            to_concept_id=str(raw.get("to_concept_id", "")),
            relation=str(raw.get("relation", "related")),
            source=str(raw.get("source", "indexing")),
        )


# ----------------------------------------------------------------------
# Learning paths (Feature 006 — adaptive learning)
# ----------------------------------------------------------------------


@dataclass
class LearningPath:
    """A learning path — ordered sequence of activities (Feature 006)."""

    id: str
    subject_id: str
    title: str
    description: str = ""
    status: str = "draft"  # draft | active | completed
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LearningPath":
        return cls(
            id=str(raw["id"]),
            subject_id=str(raw.get("subject_id", "")),
            title=str(raw.get("title", "")),
            description=str(raw.get("description", "")),
            status=str(raw.get("status", "draft")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )


@dataclass
class PathStep:
    """One step in a learning path (Feature 006)."""

    id: str
    path_id: str
    ordinal: int
    activity_type: str  # concept | quiz | exercise | flashcard_review | reading
    activity_id: str    # ID of the referenced concept/quiz/exercise/flashcard/book
    title: str = ""
    status: str = "pending"  # pending | in_progress | completed
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path_id": self.path_id,
            "ordinal": self.ordinal,
            "activity_type": self.activity_type,
            "activity_id": self.activity_id,
            "title": self.title,
            "status": self.status,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PathStep":
        return cls(
            id=str(raw["id"]),
            path_id=str(raw.get("path_id", "")),
            ordinal=int(raw.get("ordinal", 0)),
            activity_type=str(raw.get("activity_type", "")),
            activity_id=str(raw.get("activity_id", "")),
            title=str(raw.get("title", "")),
            status=str(raw.get("status", "pending")),
            completed_at=raw.get("completed_at"),
        )
