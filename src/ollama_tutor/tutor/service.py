"""TutorService façade (research D6). UI-framework-free by contract.

No textual/fastapi imports anywhere in this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from ..models import Message, MessageRole, OllamaOptions
from .assessment import (
    AttemptResult,
    ExamHelpError,
    QuizEngine,
    QuizReport,
    build_code_analysis_prompt,
    build_exercise_prompt,
    build_grade_prompt,
    build_prepare_prompt,
    next_hint,
    parse_exercise_response,
    parse_grade_response,
    parse_prepare_response,
)
from .embeddings import _hash_text, embed_texts
from .extractors import chunk_text, extract_text
from .providers.docling_ocr import DoclingOCRError
from .providers.gguf_embedding import GGUFEmbeddingError
from .providers.llama_server import LlamaServerError
from .models import (
    Book,
    Exercise,
    ExerciseAttempt,
    ResumeBriefing,
    SessionSummary,
    _now_iso,
    _uid,
)
from .progress import ProgressTracker
from .prompts import (
    build_compare_system_prompt,
    build_compare_user_prompt,
    build_system_prompt,
    build_think_instruction,
    build_user_prompt,
    resolve_overrides,
)
from .providers.openai_compat import OpenAICompatProvider
from .retrieval import Retriever
from .review import ReviewScheduler
from .store import LibraryStore
from .vector import ScoredChunk


@dataclass
class PrepareReport:
    """Result of ``prepare_knowledge`` (FR-024/FR-034 idempotent preparation).

    ``skipped`` is the total number of items skipped because they already existed
    (flashcards by ``source_hash``, glossary by ``term``); the detailed counts
    are also exposed for richer UI reporting.
    """

    flashcards_new: int = 0
    flashcards_skipped: int = 0
    glossary_new: int = 0
    glossary_skipped: int = 0
    concepts_new: int = 0
    concepts_skipped: int = 0

    @property
    def skipped(self) -> int:
        return self.flashcards_skipped + self.glossary_skipped + self.concepts_skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "flashcards_new": self.flashcards_new,
            "glossary_new": self.glossary_new,
            "concepts_new": self.concepts_new,
            "skipped": self.skipped,
            "flashcards_skipped": self.flashcards_skipped,
            "glossary_skipped": self.glossary_skipped,
            "concepts_skipped": self.concepts_skipped,
        }


class TutorService:
    """Headless façade used by both frontends (contracts/tutor-core-api.md)."""

    def __init__(
        self,
        store: LibraryStore,
        client,
        config,
        *,
        embedding_provider=None,
        document_parser=None,
    ) -> None:
        self.store = store
        self.client = client
        self.config = config
        self.model = getattr(config, "tutor_embedding_model", "embeddinggemma")
        # Phase 5a provider wiring (keyword-only, optional): when set, the
        # indexing pipeline embeds through the GGUF provider (same hash-cache
        # flow) and imports parse via the hybrid parser instead of pypdf.
        # None keeps the legacy Ollama/pypdf path byte-identical.
        self.embedding_provider = embedding_provider
        self.document_parser = document_parser
        # B1 multi-fournisseur : le client LLM (génération) peut être
        # un fournisseur externe compatible OpenAI, distinct du client
        # Ollama utilisé pour les embeddings.
        if (
            getattr(config, "llm_provider", "ollama") == "openai"
            and getattr(config, "llm_base_url", "")
        ):
            self._llm_client: Any = OpenAICompatProvider(
                base_url=config.llm_base_url,
                api_key=getattr(config, "llm_api_key", ""),
            )
        else:
            self._llm_client = client
        self.retriever = Retriever(store, client, self.model)
        self.progress = ProgressTracker(store)
        self.review = ReviewScheduler(store)
        self.quiz_engine = QuizEngine(store, client, config)
        self._cancel_flags: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    # Grounded ask (US2): sources-first streamed tutoring answer
    # ------------------------------------------------------------------

    async def ask(
        self,
        subject_name: str,
        question: str,
        *,
        model: str | None = None,
        think: bool | None = None,
        socratic: bool | None = None,
        level: str | None = None,
        session_id: str | None = None,
        cancel: asyncio.Event | None = None,
        mode: str = "ask",
        term: str | None = None,
        conversation_id: str | None = None,
        book_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Point d'entrée public : persiste l'historique puis délègue.

        Historique de conversation (005-platform-ui-library, FR-006/SC-003) :
        la question utilisateur est ajoutée au transcript avant le run et la
        réponse accumulée depuis les frames ``delta`` y est ajoutée après —
        persistance inter-restarts. Hors conversation : délégation pure.
        """
        if conversation_id:
            try:
                self.store.append_conversation_message(
                    conversation_id, "user", question
                )
            except Exception:  # jamais bloquant pour le run
                pass
        text_parts: list[str] = []
        async for frame in self._ask_iter(
            subject_name,
            question,
            model=model,
            think=think,
            socratic=socratic,
            level=level,
            session_id=session_id,
            cancel=cancel,
            mode=mode,
            term=term,
            conversation_id=conversation_id,
            book_ids=book_ids,
        ):
            if frame.get("type") == "delta":
                text_parts.append(frame.get("text", ""))
            yield frame
        if conversation_id:
            answer = "".join(text_parts).strip()
            if answer:
                try:
                    self.store.append_conversation_message(
                        conversation_id, "assistant", answer
                    )
                except Exception:
                    pass

    async def _ask_iter(
        self,
        subject_name: str,
        question: str,
        *,
        model: str | None = None,
        think: bool | None = None,
        socratic: bool | None = None,
        level: str | None = None,
        session_id: str | None = None,
        cancel: asyncio.Event | None = None,
        mode: str = "ask",
        term: str | None = None,
        conversation_id: str | None = None,
        book_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a grounded tutoring answer as dict frames.

        Frame order (contracts/tutor-ws-protocol.md): ``sources`` first, then
        ``reasoning`` (only when ``think`` is on) and ``delta`` content chunks,
        then a final ``end`` carrying the ``session_id``. When no passage clears
        the retrieval floor a ``no_passages`` notice frame is emitted before the
        ``end``. The active run can be interrupted via ``cancel`` (yields
        ``end`` with ``status="stopped"``).

        When ``mode == "compare"`` (FR-033) the notion is compared across the
        subject's books: per-book passages are retrieved and a synthesis prompt
        requiring per-source citations and a differences section is streamed
        through the same pipeline (see :meth:`_ask_compare`).
        """
        subject_id = self._resolve_subject(subject_name)
        think = self.config.tutor_think if think is None else bool(think)
        socratic = self.config.tutor_socratic if socratic is None else bool(socratic)
        level = self.config.tutor_level if level is None else level
        # In-conversation overrides (FR-014): an explicit request inside the
        # question text wins over the per-request/config defaults (D10).
        level, socratic = resolve_overrides(question, level, socratic)
        model = model or self.config.tutor_model
        k = self.config.tutor_top_k

        # Conversation nommée (005) : conversation_id EST l'identifiant de
        # session — get-or-create, et l'espace de la conversation prime sur
        # celui transmis lorsqu'elle existe déjà.
        if conversation_id:
            conv = self.store.get_tutoring_session(conversation_id)
            if conv is not None:
                subject_id = conv.subject_id
                session_id = conversation_id
            else:
                self.store.create_tutoring_session(
                    subject_id, session_id=conversation_id
                )
                session_id = conversation_id

        # Open (or continue) a tutoring_sessions row (FR-029 resume hook).
        if session_id:
            existing = self.store.get_tutoring_session(session_id)
            session = existing or self.store.create_tutoring_session(subject_id)
        else:
            session = self.store.create_tutoring_session(subject_id)
        session_id = session.id

        # Périmètre RAG (005) : book_ids explicites > sources actives de la
        # conversation > illimité (None).
        if book_ids is not None:
            scope_book_ids: list[str] | None = [str(b) for b in book_ids]
        elif conversation_id:
            scope_book_ids = self.store.get_conversation_source_ids(conversation_id)
        else:
            scope_book_ids = None
        if conversation_id:
            self.store.touch_conversation(conversation_id)

        if mode == "compare":
            async for frame in self._ask_compare(
                subject_id, subject_name, question, model, think, session_id, cancel
            ):
                yield frame
            return

        if mode == "explain":
            # Glossary explain (FR-034): on-demand explanation of a term,
            # scoped to its provenance chunks, streamed via the ask pipeline.
            term = term or question
            async for frame in self.glossary_explain(
                subject_id, term, model=model, think=think,
                session_id=session_id, cancel=cancel,
            ):
                yield frame
            return

        # Chat-without-sources (Phase 6 UX): when the subject has NO indexed
        # material at all — or nothing clears the retrieval floor — skip RAG
        # entirely and answer from model knowledge (prefixed by a system note;
        # no sources frame, no citations, NO error frame). The tutor stays
        # usable with an empty library. Grounded mode is unchanged whenever
        # passages ARE retrieved.
        chunks: list[ScoredChunk] = []
        if self.store.get_indexed_chunks(subject_id):
            # 1) Retrieve subject-scoped passages (périmètre conversation si
            # des sources actives sont définies — 005-platform-ui-library).
            chunks = await self.retriever.retrieve(
                subject_id, question, k, book_ids=scope_book_ids
            )
        if not chunks:
            async for frame in self._stream_model_answer(
                subject_name=subject_name,
                question=question,
                model=model,
                think=think,
                socratic=socratic,
                level=level,
                session_id=session_id,
                cancel=cancel,
            ):
                yield frame
            return

        sources_frame = [
            {
                "book": c.book_title,
                "chapter": c.chapter,
                "page": c.page,
                "score": round(float(c.score), 4),
            }
            for c in chunks
        ]
        yield {"type": "sources", "sources": sources_frame}

        # 2) Build the prompt (FR-012 + citation + honesty; think toggle D10).
        system_prompt = build_system_prompt(subject_name, level, socratic, sources_frame)
        user_prompt = build_user_prompt(question, chunks)
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(role=MessageRole.USER, content=user_prompt),
        ]
        if think:
            ti = build_think_instruction(True)
            if ti:
                messages.append(Message(role=MessageRole.USER, content=ti))

        async for frame in self._stream_llm(messages, model, think, session_id, cancel):
            yield frame

    # ------------------------------------------------------------------
    # Chat without sources (Phase 6 UX): model-knowledge answers
    # ------------------------------------------------------------------

    _UNGROUNDED_NOTE = (
        "(aucune source sélectionnée — réponse sans contexte documentaire)\n\n"
    )

    def _build_ungrounded_system_prompt(
        self, subject_name: str, level: str, socratic: bool
    ) -> str:
        """System prompt for answers given WITHOUT document context."""
        subject = subject_name.strip() or "sujet général"
        lines = [
            "Tu es un tuteur personnel, bienveillant et rigoureux.",
            "Tu réponds toujours en français.",
            f"Niveau de l'élève à viser : {level}.",
            (
                "Aucune source documentaire n'est sélectionnée : réponds "
                "uniquement à partir de tes propres connaissances, et dis-le "
                "honnêtement si tu n'es pas sûr."
            ),
        ]
        if subject_name.strip():
            lines.append(f"Le sujet d'étude de l'élève est : {subject}.")
        if socratic:
            lines.append(
                "Favorise une démarche socratique : guide l'élève avec des "
                "questions courtes tout en apportant les éléments de réponse."
            )
        return "\n".join(lines)

    async def _stream_model_answer(
        self,
        *,
        subject_name: str,
        question: str,
        model: str,
        think: bool,
        socratic: bool,
        level: str,
        session_id: str | None,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream an answer from model knowledge (no retrieval, no citations).

        Emits the system note as the first ``delta`` frame, then the streamed
        answer via the shared :meth:`_stream_llm` pipeline. No ``sources``
        frame and no ``error`` frame — the tutor stays usable with an empty
        library.
        """
        yield {"type": "delta", "text": self._UNGROUNDED_NOTE}
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content=self._build_ungrounded_system_prompt(
                    subject_name, level, socratic
                ),
            ),
            Message(role=MessageRole.USER, content=question),
        ]
        ti = build_think_instruction(True) if think else None
        if ti:
            messages.append(Message(role=MessageRole.USER, content=ti))
        async for frame in self._stream_llm(
            messages, model, think, session_id or "", cancel
        ):
            yield frame

    async def ask_ungrounded(
        self,
        question: str,
        *,
        model: str | None = None,
        think: bool | None = None,
        socratic: bool | None = None,
        level: str | None = None,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Answer from model knowledge when NO subject/source is available.

        Same frames as the ungrounded branch of :meth:`ask`, but performs no
        store writes (no tutoring session — there is no subject to attach it
        to), so the chat works on a completely empty library.
        """
        resolved_model = model or self.config.tutor_model
        think = self.config.tutor_think if think is None else bool(think)
        socratic = self.config.tutor_socratic if socratic is None else bool(socratic)
        level = self.config.tutor_level if level is None else level
        level, socratic = resolve_overrides(question, level, socratic)
        async for frame in self._stream_model_answer(
            subject_name="",
            question=question,
            model=resolved_model,
            think=think,
            socratic=socratic,
            level=level,
            session_id=None,
            cancel=cancel,
        ):
            yield frame

    async def _stream_llm(
        self,
        messages: list[Message],
        model: str,
        think: bool,
        session_id: str,
        cancel: asyncio.Event | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream an LLM response as ``reasoning``/``delta``/``stats``/``end``.

        Shared by the normal ``ask`` path and compare mode. Cancel-aware between
        chunks; surfaces generation errors as an ``error`` frame followed by an
        ``end`` frame (INVARIANT 5: sources already fired before this runs).
        """
        options = OllamaOptions(num_ctx=max(8192, self.config.options.num_ctx or 0))
        agen = self._llm_client.chat_stream(
            messages, model, think=think, options=options
        ).__aiter__()
        next_task: asyncio.Task | None = None
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    yield {"type": "end", "status": "stopped", "session_id": session_id}
                    return
                if next_task is None:
                    next_task = asyncio.ensure_future(agen.__anext__())
                if cancel is not None:
                    cancel_task = asyncio.ensure_future(cancel.wait())
                    await asyncio.wait(
                        {next_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancel_task.done() and not next_task.done():
                        # Cancellation requested mid-stream.
                        next_task.cancel()
                        yield {
                            "type": "end",
                            "status": "stopped",
                            "session_id": session_id,
                        }
                        return
                    if not cancel_task.done():
                        cancel_task.cancel()
                else:
                    await asyncio.wait({next_task})
                try:
                    ev = next_task.result()
                except StopAsyncIteration:
                    break
                next_task = None

                if ev.kind == "thinking":
                    yield {"type": "reasoning", "text": ev.text}
                elif ev.kind == "content":
                    yield {"type": "delta", "text": ev.text}
                elif ev.kind == "done" and ev.stats is not None:
                    yield {
                        "type": "stats",
                        "prompt_tokens": ev.stats.prompt_tokens,
                        "generated_tokens": ev.stats.generated_tokens,
                        "tok_s": ev.stats.generation_speed,
                    }
        except Exception as e:  # surface as error frame, then end
            yield {
                "type": "error",
                "code": "stream_error",
                "message": f"Erreur de génération : {e}",
            }
            yield {"type": "end", "status": "error", "session_id": session_id}
            return

        yield {"type": "end", "status": "done", "session_id": session_id}

    async def _ask_compare(
        self,
        subject_id: str,
        subject_name: str,
        notion: str,
        model: str,
        think: bool,
        session_id: str,
        cancel: asyncio.Event | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Compare a notion across the subject's books (FR-033).

        Retrieves per-book passages for the notion, emits a ``sources`` frame
        listing them, then streams a synthesis built by
        :func:`build_compare_system_prompt` / :func:`build_compare_user_prompt`
        (per-source citations + a dedicated differences section) through the
        shared :meth:`_stream_llm` pipeline.
        """
        k = max(self.config.tutor_top_k * 2, 10)
        chunks = await self.retriever.retrieve(subject_id, notion, k)
        sources_frame = [
            {
                "book": c.book_title,
                "chapter": c.chapter,
                "page": c.page,
                "score": round(float(c.score), 4),
            }
            for c in chunks
        ]
        yield {"type": "sources", "sources": sources_frame}

        if not chunks:
            yield {
                "type": "error",
                "code": "no_passages",
                "message": (
                    "Aucun passage pertinent trouvé dans vos livres pour cette "
                    "notion."
                ),
            }
            yield {"type": "end", "status": "done", "session_id": session_id}
            return

        # Group passages by book for the synthesis prompt (preserve order of
        # first appearance so the model can attribute each claim to a source).
        per_book: dict[str, list[ScoredChunk]] = {}
        for c in chunks:
            per_book.setdefault(c.book_title, []).append(c)
        ordered = list(per_book.items())

        system_prompt = build_compare_system_prompt(subject_name)
        user_prompt = build_compare_user_prompt(notion, ordered)
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(role=MessageRole.USER, content=user_prompt),
        ]
        if think:
            ti = build_think_instruction(True)
            if ti:
                messages.append(Message(role=MessageRole.USER, content=ti))

        async for frame in self._stream_llm(messages, model, think, session_id, cancel):
            yield frame

    # ------------------------------------------------------------------
    # Practice: exercise generation, grading, solution, code analysis (US4)
    # ------------------------------------------------------------------

    async def _llm_collect(
        self, messages: list[Message], options: OllamaOptions
    ) -> str:
        """Run a non-streaming LLM call and return the concatenated content."""
        parts: list[str] = []
        async for ev in self._llm_client.chat_stream(
            messages, self.config.tutor_model, options=options
        ):
            if ev.kind == "content":
                parts.append(ev.text)
        return "".join(parts)

    def _collect_past_errors(self, concept_id: str) -> list[str]:
        """Recent incorrect/partial attempt feedback for a concept (context)."""
        attempts = self.store.list_attempts_by_concept(concept_id)
        errors: list[str] = []
        for a in attempts[-10:]:
            if a.verdict in ("incorrect", "partial") and a.feedback:
                errors.append(a.feedback)
        return errors[-5:]

    async def generate_exercise(
        self, concept_id: str, difficulty: str, level: str | None = None
    ) -> Exercise:
        """Generate an exercise for ``concept_id`` with a pre-built hint ladder.

        The LLM returns the statement, the (withheld) solution, and exactly
        three hint stages so revealing a hint needs no extra LLM call
        (FR-016/017). The exercise is persisted and returned.
        """
        concept = self.store.get_concept(concept_id)
        if concept is None:
            raise KeyError(f"Unknown concept: {concept_id}")
        level = level or self.config.tutor_level
        past_errors = self._collect_past_errors(concept_id)
        messages = build_exercise_prompt(concept.name, difficulty, level, past_errors)
        options = OllamaOptions(num_ctx=max(8192, self.config.options.num_ctx or 0))
        text = await self._llm_collect(messages, options)
        data = parse_exercise_response(text)
        exercise = Exercise(
            id=_uid(),
            subject_id=concept.subject_id,
            concept_id=concept_id,
            difficulty=difficulty,
            statement=data["statement"],
            solution=data["solution"],
            hints=data["hints"],
            hint_level=0,
            status="open",
            created_at=_now_iso(),
        )
        self.store.add_exercise(exercise)
        return exercise

    async def grade_answer(
        self,
        exercise_id: str,
        answer: str,
        reveal_hint: bool = False,
    ) -> AttemptResult:
        """Grade a learner's answer and update mastery (T033).

        - Calls the LLM for a structured verdict (correct/incorrect/partial)
          plus feedback, records the attempt, and applies the D7 weight for the
          verdict to the concept's mastery.
        - Escalates the hint ladder on an incorrect answer (or when the caller
          explicitly requests a hint). The revealed hint is returned but the
          solution is NEVER returned here (INVARIANT 3).
        - Marks the exercise ``solved`` on a correct answer.
        """
        exercise = self.store.get_exercise(exercise_id)
        if exercise is None:
            raise KeyError(f"Unknown exercise: {exercise_id}")
        answer = (answer or "").strip()

        verdict: str | None = None
        feedback = ""
        if answer:
            messages = build_grade_prompt(exercise, answer)
            options = OllamaOptions(num_ctx=max(8192, self.config.options.num_ctx or 0))
            text = await self._llm_collect(messages, options)
            verdict, feedback = parse_grade_response(text)
            self.store.add_attempt(
                ExerciseAttempt(
                    id=_uid(),
                    exercise_id=exercise_id,
                    verdict=verdict,
                    answer=answer,
                    feedback=feedback,
                )
            )
            # D7 mastery update on the verdict.
            self.progress.record_event(exercise.concept_id, verdict)
            if verdict == "correct":
                self.store.update_exercise(exercise_id, status="solved")

        # Hint escalation (FR-016/017): auto on incorrect, or on explicit ask.
        revealed_hint: str | None = None
        new_hint_level = exercise.hint_level
        escalate = reveal_hint or (verdict == "incorrect")
        if escalate and exercise.hint_level < 3:
            new_level, revealed_hint = next_hint(exercise)
            self.store.update_exercise(exercise_id, hint_level=new_level)
            new_hint_level = new_level
            # The explicit manual hint request carries its own small penalty;
            # an automatic escalation on a wrong answer does not double-count.
            if reveal_hint:
                self.progress.record_event(exercise.concept_id, "hint_used")

        return AttemptResult(
            verdict=verdict,
            feedback=feedback,
            hint_level=new_hint_level,
            hint=revealed_hint,
            solution=None,
        )

    async def request_solution(self, exercise_id: str) -> str:
        """Return the withheld solution — ONLY when explicitly requested.

        INVARIANT 3: this is the single path that returns solution text; grading
        never does. The REST layer enforces the explicit-intent gate.
        """
        exercise = self.store.get_exercise(exercise_id)
        if exercise is None:
            raise KeyError(f"Unknown exercise: {exercise_id}")
        return exercise.solution

    async def analyze_code(
        self,
        code: str,
        context: str = "",
        execution_result: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a pedagogical code analysis (T034, FR-019/FR-020).

        Classifies issues into the six error categories and, when provided,
        folds in an optional execution-result context (FR-020 stub). Streams
        ``delta``/``reasoning``/``stats``/``end`` frames like ``ask``.
        """
        messages = build_code_analysis_prompt(code, context, execution_result)
        options = OllamaOptions(num_ctx=max(8192, self.config.options.num_ctx or 0))
        agen = self._llm_client.chat_stream(
            messages, self.config.tutor_model, options=options
        ).__aiter__()
        try:
            while True:
                try:
                    ev = await agen.__anext__()
                except StopAsyncIteration:
                    break
                if ev.kind == "thinking":
                    yield {"type": "reasoning", "text": ev.text}
                elif ev.kind == "content":
                    yield {"type": "delta", "text": ev.text}
                elif ev.kind == "done" and ev.stats is not None:
                    yield {
                        "type": "stats",
                        "prompt_tokens": ev.stats.prompt_tokens,
                        "generated_tokens": ev.stats.generated_tokens,
                        "tok_s": ev.stats.generation_speed,
                    }
        except Exception as e:  # surface as error frame, then end
            yield {
                "type": "error",
                "code": "stream_error",
                "message": f"Erreur d'analyse : {e}",
            }
        yield {"type": "end", "status": "done"}

    # ------------------------------------------------------------------
    # Revision: knowledge preparation (US5 / T039, FR-024/FR-034/FR-035)
    # ------------------------------------------------------------------

    def _subject_chunks(self, subject_id: str) -> list[dict[str, Any]]:
        """Return chunk rows (id/text/chapter/book_id) for a subject, in order."""
        rows = self.store._conn.execute(
            "SELECT id, text, chapter, book_id FROM chunks "
            "WHERE subject_id = ? ORDER BY ordinal",
            (subject_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _find_concept(self, subject_id: str, name: str) -> Any | None:
        if not name:
            return None
        row = self.store._conn.execute(
            "SELECT * FROM concepts WHERE subject_id = ? AND name = ? "
            "COLLATE NOCASE",
            (subject_id, name.strip()),
        ).fetchone()
        return self.store.get_concept(row["id"]) if row is not None else None

    def _flashcard_exists(self, subject_id: str, source_hash: str) -> bool:
        row = self.store._conn.execute(
            "SELECT 1 FROM flashcards WHERE subject_id = ? AND source_hash = ?",
            (subject_id, source_hash),
        ).fetchone()
        return row is not None

    def _glossary_exists(self, subject_id: str, term: str) -> bool:
        row = self.store._conn.execute(
            "SELECT 1 FROM glossary_terms WHERE subject_id = ? AND term = ? "
            "COLLATE NOCASE",
            (subject_id, term.strip()),
        ).fetchone()
        return row is not None

    def _seed_relations(self, subject_id: str, concept_ids: list[str]) -> None:
        """Seed 'related' knowledge_relations between co-occurring concepts (FR-035)."""
        for i in range(len(concept_ids)):
            for j in range(i + 1, len(concept_ids)):
                self.store.upsert_relation(
                    subject_id, concept_ids[i], concept_ids[j], "related"
                )

    async def prepare_knowledge(self, subject_id: str) -> PrepareReport:
        """Idempotently prepare flashcards/glossary/concepts/relations.

        Derives candidate concepts from chunk batches, LLM-generates flashcards
        (question/answer/level) and glossary definitions in bounded batches,
        skips existing items via ``source_hash`` (flashcards UNIQUE
        (subject_id, source_hash)) / ``term`` (glossary UNIQUE (subject_id, term)),
        seeds ``knowledge_relations`` from co-occurring concepts (FR-035), and
        returns a :class:`PrepareReport`. A second run reports only skipped.
        """
        # KeyError if the subject is unknown (mirrors other subject-scoped ops).
        self.store._get_subject(subject_id)
        report = PrepareReport()
        chunks = self._subject_chunks(subject_id)
        if not chunks:
            return report

        # Bounded batches (research D11 batching) of chunk text.
        batch_size = 6
        batches = [chunks[i : i + batch_size] for i in range(0, len(chunks), batch_size)]
        for batch in batches:
            chapter = batch[0].get("chapter") or ""
            text = "\n\n".join(c["text"] for c in batch)
            try:
                messages = build_prepare_prompt(chapter, text)
                options = OllamaOptions(
                    num_ctx=max(8192, self.config.options.num_ctx or 0)
                )
                resp = await self._llm_collect(messages, options)
                data = parse_prepare_response(resp)
            except Exception:
                continue

            # Concepts (upsert; count new vs existing).
            batch_concept_ids: list[str] = []
            for name in data["concepts"]:
                existing = self._find_concept(subject_id, name)
                if existing is None:
                    concept = self.store.upsert_concept(subject_id, name)
                    report.concepts_new += 1
                    batch_concept_ids.append(concept.id)
                else:
                    report.concepts_skipped += 1
                    batch_concept_ids.append(existing.id)

            # Flashcards (skip existing via source_hash).
            for fc in data["flashcards"]:
                concept_name = str(fc.get("concept", "")).strip()
                concept = self._find_concept(subject_id, concept_name)
                if concept is None:
                    if not concept_name:
                        continue
                    concept = self.store.upsert_concept(subject_id, concept_name)
                    report.concepts_new += 1
                question = str(fc.get("question", "")).strip()
                answer = str(fc.get("answer", "")).strip()
                if not question or not answer:
                    continue
                level = str(fc.get("level", "intermediate"))
                if level not in ("beginner", "intermediate", "advanced", "expert"):
                    level = "intermediate"
                source_hash = hashlib.sha256(
                    (concept.id + "|" + question).encode("utf-8")
                ).hexdigest()
                if self._flashcard_exists(subject_id, source_hash):
                    report.flashcards_skipped += 1
                    continue
                fid = _uid()
                self.store._conn.execute(
                    "INSERT INTO flashcards "
                    "(id, subject_id, concept_id, level, question, answer, "
                    "source_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fid,
                        subject_id,
                        concept.id,
                        level,
                        question,
                        answer,
                        source_hash,
                        _now_iso(),
                    ),
                )
                self.store._conn.commit()
                self.review.seed_schedule(fid)
                report.flashcards_new += 1

            # Glossary (skip existing via term).
            for g in data["glossary"]:
                term = str(g.get("term", "")).strip()
                definition = str(g.get("definition", "")).strip()
                if not term or not definition:
                    continue
                if self._glossary_exists(subject_id, term):
                    report.glossary_skipped += 1
                    continue
                self.store.upsert_glossary_term(
                    subject_id, term, definition, None, chapter or None
                )
                report.glossary_new += 1

            # FR-035 seed: relate co-occurring concepts within this batch.
            self._seed_relations(subject_id, batch_concept_ids)

        return report

    # ------------------------------------------------------------------
    # Revision: spaced repetition (US5 / T038, D8)
    # ------------------------------------------------------------------

    def due_reviews(self, subject_id: str) -> list[Any]:
        """Return due flashcards for a subject (pure SQL, no LLM — SC-008)."""
        return self.review.due_reviews(subject_id)

    def grade_review(self, flashcard_id: str, success: bool) -> dict[str, Any]:
        """Grade a flashcard review, walking the D8 ladder; returns new schedule."""
        return self.review.grade_review(flashcard_id, success)

    # ------------------------------------------------------------------
    # Revision: quizzes & exams (US5 / T040)
    # ------------------------------------------------------------------

    async def create_quiz(
        self,
        subject_id: str,
        size: int,
        kinds: list[str],
        *,
        category_ids: list[int] | None = None,
        corpus_ids: list[int] | None = None,
    ) -> Any:
        """Generate a quiz of ``size`` questions across ``kinds`` bound to concepts.

        When ``category_ids``/``corpus_ids`` are given (Phase 5a exam scoping),
        questions draw only from concepts grounded in books belonging to any
        listed category/corpus (union). Omitted/empty keeps legacy behavior.
        """
        concepts = self._concepts_in_scope(subject_id, category_ids, corpus_ids)
        return await self.quiz_engine.create_quiz(
            subject_id, size, kinds, concepts=concepts
        )

    async def create_exam(
        self,
        subject_id: str,
        size: int,
        time_limit_s: int,
        *,
        category_ids: list[int] | None = None,
        corpus_ids: list[int] | None = None,
    ) -> Any:
        """Generate a timed, assistance-free exam (kind='exam'), optionally scoped."""
        concepts = self._concepts_in_scope(subject_id, category_ids, corpus_ids)
        return await self.quiz_engine.create_exam(
            subject_id, size, time_limit_s, concepts=concepts
        )

    def _concepts_in_scope(
        self,
        subject_id: str,
        category_ids: list[int] | None,
        corpus_ids: list[int] | None,
    ) -> list[Any] | None:
        """Resolve exam scoping to a concept subset, or ``None`` when unscoped.

        A concept is in scope when its name appears in the chunk text of at
        least one book belonging to any listed category/corpus (union).
        Raises ``KeyError`` when the scope selects no books/concepts so the
        transport layer can answer 404.
        """
        if not category_ids and not corpus_ids:
            return None
        book_ids: set[str] = set()
        for cid in category_ids or []:
            book_ids.update(b.id for b in self.store.list_books_by_category(cid))
        for rid in corpus_ids or []:
            book_ids.update(b.id for b in self.store.list_books_by_corpus(rid))
        if not book_ids:
            raise KeyError(f"No books in scope: {category_ids}/{corpus_ids}")
        names = {
            c.name.lower()
            for c in self.store.list_concepts(subject_id)
            if c.name
        }
        if not names:
            raise KeyError(f"No concepts in subject: {subject_id}")
        scoped: set[str] = set()
        for row in self.store.get_subject_chunks(subject_id):
            if row["book_id"] in book_ids:
                low = (row["text"] or "").lower()
                scoped.update(n for n in names if n in low)
        concepts = [
            c
            for c in self.store.list_concepts(subject_id)
            if c.name.lower() in scoped
        ]
        if not concepts:
            raise KeyError(
                f"No concepts grounded in scoped books: {sorted(book_ids)}"
            )
        return concepts

    async def submit_answers(
        self, quiz_id: str, answers: dict[str, Any], hint_requested: bool = False
    ) -> QuizReport:
        """Correct a quiz/exam submission; enforces exam rules (T040)."""
        return await self.quiz_engine.submit_answers(
            quiz_id, answers, hint_requested=hint_requested
        )

    def get_quiz(self, quiz_id: str, include_answers: bool = False) -> Any:
        """Return a quiz with its questions (answers only when completed)."""
        return self.quiz_engine.get_quiz(quiz_id, include_answers=include_answers)

    # ------------------------------------------------------------------
    # Session memory & continuity (US6 / T043-T045, FR-028/FR-029)
    # ------------------------------------------------------------------

    # Mastery thresholds (research D7): >=70 maîtrisé, <40 faible (to_review).
    _MASTERED_SCORE = 70.0
    _TO_REVIEW_SCORE = 40.0

    def _transcript_concepts(self, session: Any) -> list[str]:
        """Concept names referenced in a session's transcript JSON (if any)."""
        names: list[str] = []
        for frame in self.store.get_session_transcript(session.id):
            if not isinstance(frame, dict):
                continue
            c = frame.get("concept")
            if isinstance(c, str) and c:
                names.append(c)
            cs = frame.get("concepts")
            if isinstance(cs, list):
                for x in cs:
                    if isinstance(x, str) and x:
                        names.append(x)
        return names

    def close_session(self, session_id: str) -> SessionSummary:
        """Aggregate a session's activity into a persisted summary (FR-028).

        Pulls the session's exercise attempts (since the session started),
        quiz/exam answers, review grades and any transcript-referenced concepts
        to build the ``studied`` set. Each studied concept is then classified
        as ``mastered`` (score >= 70) or ``to_review`` (open gap or score < 40).
        The summary is persisted and the session row is closed. No model call.
        """
        session = self.store.get_tutoring_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")
        subject_id = session.subject_id
        since = session.started_at

        studied_ids: set[str] = set()

        # Exercise attempts within the session window.
        for att in self.store.list_session_attempts(subject_id, since_iso=since):
            ex = self.store.get_exercise(att.exercise_id)
            if ex is not None:
                studied_ids.add(ex.concept_id)

        # Quiz/exam answers for the subject.
        for concept_id, _verdict in self.store.list_session_quiz_answers(subject_id):
            studied_ids.add(concept_id)

        # Review grades for the subject.
        for concept_id, _result in self.store.list_session_reviews(subject_id):
            studied_ids.add(concept_id)

        # Transcript-referenced concepts (resolved by name within the subject).
        for name in self._transcript_concepts(session):
            c = self.store.get_concept_by_name(subject_id, name)
            if c is not None:
                studied_ids.add(c.id)

        # Classify each studied concept from current mastery + gap status.
        gaps = {g.concept.id for g in self.progress.get_gaps(subject_id)}
        studied_names: list[str] = []
        mastered: list[str] = []
        to_review: list[str] = []
        for cid in studied_ids:
            concept = self.store.get_concept(cid)
            if concept is None:
                continue
            studied_names.append(concept.name)
            score = self.store.get_concept_score(cid)
            is_gap = cid in gaps
            if score is not None and score >= self._MASTERED_SCORE:
                mastered.append(concept.name)
            if is_gap or (score is not None and score < self._TO_REVIEW_SCORE):
                to_review.append(concept.name)

        summary = SessionSummary(
            session_id=session_id,
            concepts_studied=studied_names,
            concepts_mastered=mastered,
            to_review=to_review,
            produced_at=_now_iso(),
        )
        self.store.save_session_summary(summary)
        self.store.close_session_row(session_id)
        return summary

    def resume_briefing(self, subject_id: str) -> ResumeBriefing:
        """Assemble a resume briefing from the last summary + open gaps (FR-029).

        PURE data assembly — performs ZERO model/LLM calls. Combines the most
        recent :class:`SessionSummary` for the subject with the currently open
        gaps (gap-flagged concepts + due flashcards) to produce a briefing the
        UI can show before the learner resumes.
        """
        self.store._get_subject(subject_id)  # KeyError if unknown
        summaries = self.store.list_session_summaries(subject_id)
        last = summaries[0] if summaries else None
        subject = self.store._get_subject(subject_id)

        # Open gaps: gap-flagged concepts + concepts with due reviews.
        difficulties: list[str] = []
        for g in self.progress.get_gaps(subject_id):
            if g.concept.name not in difficulties:
                difficulties.append(g.concept.name)
        for card in self.due_reviews(subject_id):
            c = self.store.get_concept(card.concept_id)
            if c is not None and c.name not in difficulties:
                difficulties.append(c.name)

        # Last topic: most recent studied concept, else the subject itself.
        last_topic = subject.name
        if last is not None:
            if last.concepts_studied:
                last_topic = last.concepts_studied[-1]
            elif last.concepts_mastered:
                last_topic = last.concepts_mastered[-1]

        # Proposed next step.
        if difficulties:
            proposal = "Réviser en priorité : " + ", ".join(difficulties[:3])
        elif last is not None:
            proposal = (
                f"Continuer l'apprentissage de {subject.name} avec de nouvelles notions"
            )
        else:
            proposal = f"Commencer l'apprentissage de {subject.name}"

        return ResumeBriefing(
            last_topic=last_topic,
            difficulties=difficulties,
            proposal=proposal,
            last_summary=last.to_dict() if last is not None else None,
        )

    # ------------------------------------------------------------------
    # Knowledge tools (US7 / T048, FR-034/FR-035)
    # ------------------------------------------------------------------

    async def glossary_explain(
        self,
        subject_id: str,
        term: str,
        *,
        model: str | None = None,
        think: bool | None = None,
        session_id: str | None = None,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Explain a glossary term, scoped to its provenance chunks (FR-034).

        Looks the term up in ``glossary_terms`` (raises ``KeyError`` if
        unknown), emits its stored ``definition`` frame, then runs an
        explanation through the ask pipeline: passages for the term are
        retrieved and filtered to the term's provenance book (and chapter when
        known) so the explanation stays grounded in the source that defined it.
        """
        gterm = self.store.get_glossary_term(subject_id, term)
        if gterm is None:
            raise KeyError(f"Terme inconnu : {term}")
        subject = self.store._get_subject(subject_id)
        model = model or self.config.tutor_model
        think = self.config.tutor_think if think is None else bool(think)

        if session_id:
            existing = self.store.get_tutoring_session(session_id)
            session = existing or self.store.create_tutoring_session(subject_id)
        else:
            session = self.store.create_tutoring_session(subject_id)
        session_id = session.id

        # Retrieve passages for the term, then scope to its provenance.
        chunks = await self.retriever.retrieve(
            subject_id, term, self.config.tutor_top_k
        )
        scoped = [
            c
            for c in chunks
            if (gterm.book_id is None or c.book_id == gterm.book_id)
            and (
                gterm.chapter is None
                or (c.chapter or "").lower() == (gterm.chapter or "").lower()
            )
        ]
        if not scoped and chunks:
            scoped = chunks  # fall back to all retrieved if provenance empty

        yield {
            "type": "definition",
            "term": gterm.term,
            "definition": gterm.definition,
        }

        sources_frame = [
            {
                "book": c.book_title,
                "chapter": c.chapter,
                "page": c.page,
                "score": round(float(c.score), 4),
            }
            for c in scoped
        ]
        yield {"type": "sources", "sources": sources_frame}

        if not scoped:
            yield {
                "type": "error",
                "code": "no_passages",
                "message": "Aucun extrait disponible pour expliquer ce terme.",
            }
            yield {"type": "end", "status": "done", "session_id": session_id}
            return

        system_prompt = build_system_prompt(
            subject.name, self.config.tutor_level, False, sources_frame
        )
        user_prompt = (
            f"Explique le terme « {gterm.term} » en t'appuyant sur les extraits "
            "suivants de ses livres (à citer entre crochets) :\n"
            + self.retriever.assemble_context(scoped)
        )
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(role=MessageRole.USER, content=user_prompt),
        ]
        if think:
            ti = build_think_instruction(True)
            if ti:
                messages.append(Message(role=MessageRole.USER, content=ti))

        async for frame in self._stream_llm(messages, model, think, session_id, cancel):
            yield frame

    def build_knowledge_map(self, subject_id: str) -> dict[str, Any]:
        """Assemble the knowledge map from stored relations (FR-035).

        Pure data assembly — ZERO model calls. Returns
        ``{nodes: [Concept], edges: [relation]}`` where ``nodes`` are the
        subject's concepts and ``edges`` the ``knowledge_relations`` rows.
        """
        self.store._get_subject(subject_id)  # KeyError if unknown
        concepts = self.store.list_concepts(subject_id)
        relations = self.store.list_relations(subject_id)
        return {
            "nodes": [c.to_dict() for c in concepts],
            "edges": [r.to_dict() for r in relations],
        }

    # ------------------------------------------------------------------
    # Subject resolution
    # ------------------------------------------------------------------

    def _resolve_subject(self, subject_name: str) -> str:
        name = subject_name.strip()
        for s in self.store.list_subjects():
            if s.name.lower() == name.lower():
                return s.id
        return self.store.create_subject(name).id

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, book_id: str) -> None:
        """Request cancellation of an in-flight import_and_index run."""
        ev = self._cancel_flags.get(book_id)
        if ev is not None:
            ev.set()
        # also purge via the store so a loop polling status aborts
        self.store.cancel_indexing(book_id)

    def _is_cancelled(self, book_id: str) -> bool:
        ev = self._cancel_flags.get(book_id)
        if ev is not None and ev.is_set():
            return True
        # external cancel (e.g. via store.cancel_indexing) flips status
        try:
            return self.store.get_book_status(book_id) == "pending"
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Import + index pipeline (D5/D6)
    # ------------------------------------------------------------------

    def _run_index_thread(
        self, subject_id: str, book: Book, path: Any, fmt: str | None
    ) -> None:
        try:
            asyncio.run(self._run_index(subject_id, book, path, fmt))
        except Exception:
            # errors are recorded on the book row inside _run_index; never
            # let a stray exception kill the daemon thread silently.
            pass
        finally:
            # The thread's event loop is gone: release the loop-bound httpx
            # client so the next run builds a fresh one.
            self._close_client()

    def register_import(
        self, subject_name: str, path: Any
    ) -> tuple[str, Book]:
        """Register a book row WITHOUT starting indexing (Phase 6 UX).

        Resolves/creates the subject and inserts the ``pending`` book row
        (fingerprint dedup applies). Returns ``(subject_id, book)`` so the
        caller can schedule :meth:`_run_index` itself — typically via
        :meth:`schedule_index` on a long-lived event loop. Pre-flight
        problems (unknown format, missing file) raise before any row exists.
        """
        subject_id = self._resolve_subject(subject_name)
        return subject_id, self.store.import_document(subject_id, path)

    async def reindex_book(self, book_id: str) -> dict[str, Any]:
        """Ré-embed les chunks EXISTANTS d'un livre avec le modèle courant.

        Résout le mélange de vecteurs inter-modèles (005-suite) : mêmes
        textes, même découpage, nouveaux vecteurs signés du modèle courant.
        Aucun re-découpage, aucune perte.
        """
        book = self.store.get_book(book_id)
        if book is None:
            raise KeyError(book_id)
        subject_id = self.store.get_book_subject_id(book_id)
        if subject_id is None:
            raise KeyError(f"no_subject_for_{book_id}")
        rows = [
            r for r in self.store.get_subject_chunks(subject_id)
            if r["book_id"] == book_id
        ]
        texts = [r["text"] for r in rows]
        if not texts:
            return {"book_id": book_id, "reembedded": 0}
        model = self.config.tutor_embedding_model
        vectors = await self.client.embed(model, texts)
        n = self.store.update_chunks_embedding(book_id, vectors, model)
        self.retriever.invalidate(subject_id)
        return {"book_id": book_id, "reembedded": n, "model": model}

    def schedule_index(
        self, subject_id: str, book: Book, path: Any, fmt: str | None = None
    ) -> asyncio.Task:
        """Schedule background indexing on the CURRENT running loop.

        The caller MUST keep a strong reference to the returned task (asyncio
        only holds weak refs). A cancel flag is registered so :meth:`cancel`
        keeps working; ``_run_index`` clears it when the run ends.
        """
        self._cancel_flags[book.id] = threading.Event()
        return asyncio.ensure_future(self._run_index(subject_id, book, path, fmt))

    def import_and_index(
        self,
        subject_name: str,
        path: Any,
        fmt: str | None = None,
        background: bool = True,
    ) -> Book:
        """Import a document into ``subject_name`` and index it.

        Resolves/creates the subject, registers the book (fingerprint no-op
        when already indexed), then extracts → chunks → embeds → stores →
        marks indexed. On error the book row is set to ``error`` with the
        message. When ``background`` is True the pipeline runs in a daemon
        thread and returns immediately; otherwise it runs synchronously.
        """
        subject_id = self._resolve_subject(subject_name)
        book = self.store.import_document(subject_id, path)
        # dedup: an already-indexed book is a no-op (zero new embeddings)
        if self.store.get_book_status(book.id) == "indexed":
            return book

        if background:
            ev = threading.Event()
            self._cancel_flags[book.id] = ev
            t = threading.Thread(
                target=self._run_index_thread,
                args=(subject_id, book, path, fmt),
                daemon=True,
            )
            t.start()
            self._threads[book.id] = t
            return book

        try:
            asyncio.run(self._run_index(subject_id, book, path, fmt))
        finally:
            # asyncio.run's loop is gone: release the loop-bound client so a
            # later run (different loop) rebuilds a fresh httpx client.
            self._close_client()
        # refresh so the caller sees the final status/chunk counts
        return self.store.get_book(book.id) or book

    async def _run_index(
        self, subject_id: str, book: Book, path: Any, fmt: str | None
    ) -> None:
        book_id = book.id
        try:
            self.store.mark_indexing(book_id)
            if self._is_cancelled(book_id):
                self.store.cancel_indexing(book_id)
                return

            if self.document_parser is not None:
                # Hybrid ingestion (Phase 3/5a): page-ordered parse (pypdf
                # text-layer vs Docling OCR); chunk mapping follows the page
                # texts in order.
                parsed = await self.document_parser.parse(Path(path))
                pages = (
                    parsed.get("pages", [])
                    if isinstance(parsed, dict)
                    else list(parsed)
                )
                text = "\n\n".join(str(p.get("text", "")) for p in pages)
            else:
                text = extract_text(path, fmt)
            if self._is_cancelled(book_id):
                self.store.cancel_indexing(book_id)
                return

            chunks = chunk_text(text)
            if not chunks:
                self.store.mark_indexed(book_id, 0)
                return

            if self.embedding_provider is not None:
                embeddings = await self._embed_with_provider(chunks)
            else:
                embeddings = await embed_texts(
                    self.client, self.model, chunks, self.store
                )
            if self._is_cancelled(book_id):
                self.store.cancel_indexing(book_id)
                return

            self.store.add_chunks(
                subject_id, book_id, chunks, embeddings, self.model
            )
            self.store.mark_indexed(book_id, len(chunks))
        except (
            GGUFEmbeddingError,
            DoclingOCRError,
            LlamaServerError,
        ) as e:
            # Explicitly configured GGUF backend failed: surface the cause on
            # the book row. NO silent fallback to Ollama (Phase 5a contract).
            self.store.set_book_error(
                book_id, f"[gguf-provider] {type(e).__name__}: {e}"
            )
        except Exception as e:  # fail-closed: surface on the book row
            self.store.set_book_error(book_id, str(e))
        finally:
            self._cancel_flags.pop(book_id, None)

    def _close_client(self) -> None:
        """Close the shared client from a SYNC context (best-effort).

        Used by thread-based / ``asyncio.run``-based indexing flows: their
        event loop dies when the run ends, so any httpx.AsyncClient created
        inside it must be released (the next run rebuilds one lazily). The
        server-driven task flow NEVER calls this — it keeps using the shared
        loop-bound client.
        """
        try:
            asyncio.run(self.client.close())
        except Exception:
            pass
        # Also close the LLM provider client when it differs from the
        # main client (e.g. OpenAICompatProvider).
        if self._llm_client is not self.client and hasattr(self._llm_client, "close"):
            try:
                asyncio.run(self._llm_client.close())
            except Exception:
                pass

    async def _embed_with_provider(self, chunks: list[str]) -> list[list[float]]:
        """Embed via the configured provider inside the SAME sha256-hash cache.

        Cache hits are reused without a provider call; only misses are sent
        (batched) to ``embedding_provider.embed``; new vectors are written
        back to the shared store cache. Provider errors propagate to the
        caller (surfaced on the book row — no Ollama fallback).
        """
        results: list[list[float] | None] = []
        to_embed: list[tuple[int, str]] = []
        for i, text in enumerate(chunks):
            cached = self.store.get_embedding(_hash_text(text, self.model), self.model)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                to_embed.append((i, text))

        if to_embed:
            texts = [t for _, t in to_embed]
            vectors = await self.embedding_provider.embed(texts)
            for (i, _), vec in zip(to_embed, vectors):
                self.store.add_embedding(_hash_text(chunks[i], self.model), self.model, vec)
                results[i] = vec

        return [r if r is not None else [] for r in results]

    # ------------------------------------------------------------------
    # Auto-classification of books into categories (Phase 6 UX)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_title(name: str) -> str:
        """Fuzzy title key: casefold + collapse all whitespace runs."""
        return " ".join(str(name or "").split()).casefold()

    _CLASSIFY_SYSTEM_PROMPT = (
        "Tu es un bibliothécaire méticuleux. Pour chaque titre de livre "
        "fourni, tu attribues UNE catégorie thématique courte en français "
        "(un ou deux mots). Tu réponds UNIQUEMENT par un tableau JSON strict, "
        "sans aucun texte autour, au format exact : "
        '[{"title": "<titre>", "category": "<catégorie>"}].'
    )

    def _build_classify_messages(self, titles: list[str]) -> list[Message]:
        listing = "\n".join(f"- {t}" for t in titles)
        user = (
            "Catégorise ces titres de livres :\n"
            f"{listing}\n\n"
            "Réponds uniquement par le tableau JSON strict "
            '[{"title": "...", "category": "..."}] couvrant CHAQUE titre '
            "de la liste."
        )
        return [
            Message(role=MessageRole.SYSTEM, content=self._CLASSIFY_SYSTEM_PROMPT),
            Message(role=MessageRole.USER, content=user),
        ]

    @staticmethod
    def _parse_strict_json_array(text: str) -> list[Any]:
        """Robustly extract a JSON array from an LLM answer.

        Strips Markdown code fences, slices from the first ``[`` to the last
        ``]`` and ``json.loads`` the result. Raises ``ValueError`` on any
        malformation so callers can record a failed batch.
        """
        s = (text or "").strip()
        if s.startswith("```"):
            s = re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n?", "", s)
            s = re.sub(r"\r?\n?[ \t]*```$", "", s).strip()
        start = s.find("[")
        end = s.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("aucun tableau JSON trouvé dans la réponse")
        arr = json.loads(s[start : end + 1])
        if not isinstance(arr, list):
            raise ValueError("la réponse n'est pas un tableau JSON")
        return arr

    def _ensure_category(self, name: str) -> tuple[int, bool]:
        """Return ``(category_id, created)`` for ``name``, reusing duplicates.

        ``store.create_category`` raises ``ValueError`` on case-insensitive
        duplicates; the existing row is then looked up (same normalization)
        and reused instead.
        """
        try:
            cat = self.store.create_category(name)
            return int(cat["id"]), True
        except ValueError:
            wanted = self._normalize_title(name)
            for c in self.store.list_categories():
                if self._normalize_title(c["name"]) == wanted:
                    return int(c["id"]), False
            raise

    async def auto_classify_categories(
        self, batch_size: int = 25
    ) -> dict[str, Any]:
        """Classify every book title into categories via the tutor LLM.

        Books are processed in consecutive batches of ``batch_size`` with ONE
        LLM call per batch. A malformed/unparsable batch is recorded in
        ``failed_batches`` (by batch index) and the remaining batches still
        run. Title→book matching is normalized (casefold + whitespace
        collapse); categories are deduplicated through the store. Connection
        failures propagate to the caller (transport maps them to 502).
        """
        books = self.store.list_all_books()
        total = len(books)
        if total == 0:
            return {"assignments": [], "message": "aucun livre"}

        try:
            batch_size = max(1, int(batch_size))
        except (TypeError, ValueError):
            batch_size = 25

        by_norm: dict[str, Book] = {}
        for b in books:
            by_norm.setdefault(self._normalize_title(b.title), b)

        assignments: list[dict[str, Any]] = []
        categories_created: list[str] = []
        failed_batches: list[int] = []
        options = OllamaOptions(num_ctx=max(8192, self.config.options.num_ctx or 0))

        for index, start in enumerate(range(0, total, batch_size)):
            batch = books[start : start + batch_size]
            titles = [b.title for b in batch]
            messages = self._build_classify_messages(titles)
            # Engine failures (unreachable model…) propagate so the transport
            # layer can answer 502; only OUTPUT problems mark a failed batch.
            raw = await self._llm_collect(messages, options)
            try:
                items = self._parse_strict_json_array(raw)
            except Exception:
                # Malformed output / parse failure: skip this batch, keep going.
                failed_batches.append(index)
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                category = str(item.get("category", "")).strip()
                book = by_norm.get(self._normalize_title(title))
                if book is None or not category:
                    continue  # unknown title / empty category → ignore
                cat_id, created = self._ensure_category(category)
                if created and category not in categories_created:
                    categories_created.append(category)
                self.store.add_book_to_category(book.id, cat_id)  # idempotent
                assignments.append({
                    "book_id": book.id,
                    "title": book.title,
                    "category": category,
                })

        return {
            "assignments": assignments,
            "categories_created": categories_created,
            "failed_batches": failed_batches,
            "total_books": total,
        }
