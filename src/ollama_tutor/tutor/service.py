"""TutorService façade (research D6). UI-framework-free by contract.

No textual/fastapi imports anywhere in this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from ..client import OllamaClient
from ..models import Message, MessageRole, OllamaOptions
from .assessment import (
    AttemptResult,
    ExamHelpError,
    QuizEngine,
    QuizReport,
    _extract_json,
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
from .extractors import chunk_text, chunk_text_structured, extract_text
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
    build_diagnostic_question_prompt,
    build_exam_analysis_prompt,
    build_exam_resolve_prompt,
    build_learning_path_prompt,
    build_path_from_books_prompt,
    build_revision_sheet_prompt,
    build_summary_prompt,
    build_system_prompt,
    build_think_instruction,
    build_user_prompt,
    resolve_overrides,
)
from .providers.openai_compat import OpenAICompatProvider
from .retrieval import Retriever, validate_citations
from .reranker import SimpleReranker
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
            # Les embeddings passent TOUJOURS par Ollama (local), jamais par le
            # fournisseur cloud. Si le client injecté est déjà un OllamaClient
            # (tests), on le réutilise ; sinon on crée un client Ollama dédié.
            if isinstance(client, OllamaClient):
                self.client = client
            else:
                self.client = OllamaClient()
        else:
            self._llm_client = client
            self.client = client
        self.retriever = Retriever(
            store, self.client, self.model,
            reranker=SimpleReranker() if getattr(config, "tutor_reranking_enabled", False) else None,
        )
        self.progress = ProgressTracker(store)
        self.review = ReviewScheduler(store)
        # QuizEngine génère (chat_stream) → client LLM (cloud ou Ollama).
        self.quiz_engine = QuizEngine(store, self._llm_client, config)
        self._cancel_flags: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        # One app-loop worker owns the persistent pending queue. Books are
        # deliberately processed one at a time on modest CPU/RAM machines.
        self._index_queue_task: asyncio.Task | None = None
        self._index_queue_stop: asyncio.Event | None = None
        self._index_queue_current: str | None = None
        self._index_queue_completed = 0
        self._index_queue_errors = 0
        self._index_queue_started_at: str | None = None
        self._nightly_scheduler_task: asyncio.Task | None = None
        self._nightly_last_run: str | None = None
        self._nightly_last_error: str | None = None
        self._nightly_last_window_key: str | None = None
        self._nightly_prepare_status: dict[str, Any] = {}

    def index_queue_status(self) -> dict[str, Any]:
        """Return a lightweight, persistence-backed queue snapshot."""
        books = self.store.list_all_books()
        pending = sum(1 for book in books if book.status == "pending")
        current = self.store.get_book(self._index_queue_current) if self._index_queue_current else None
        running = bool(self._index_queue_task and not self._index_queue_task.done())
        return {
            "running": running,
            "paused": not running and pending > 0,
            "current_book_id": self._index_queue_current,
            "current_title": current.title if current is not None else None,
            "pending_count": pending,
            "completed_count": self._index_queue_completed,
            "error_count": self._index_queue_errors,
            "started_at": self._index_queue_started_at,
        }

    def recover_interrupted_indexing(self) -> int:
        """Make interrupted books eligible for a later controlled resume."""
        return self.store.recover_interrupted_indexing()

    async def start_index_queue(self, *, retry_errors: bool = False) -> dict[str, Any]:
        """Start exactly one worker consuming pending books by creation order."""
        if self._index_queue_task is not None and not self._index_queue_task.done():
            return self.index_queue_status()
        self.store.recover_interrupted_indexing()
        if retry_errors:
            for book in self.store.list_all_books():
                if book.status == "error":
                    self.store.cancel_indexing(book.id)
        self._index_queue_stop = asyncio.Event()
        # Keep counters cumulative so rapid imports that start successive
        # short worker runs still report the complete session history.
        self._index_queue_started_at = _now_iso()
        self._index_queue_task = asyncio.create_task(self._run_index_queue())
        return self.index_queue_status()

    async def stop_index_queue(self, *, wait: bool = True) -> dict[str, Any]:
        """Pause the worker; an in-flight book is returned to ``pending``."""
        task = self._index_queue_task
        if task is None or task.done():
            return self.index_queue_status()
        if self._index_queue_stop is not None:
            self._index_queue_stop.set()
        if self._index_queue_current:
            flag = self._cancel_flags.get(self._index_queue_current)
            if flag is not None:
                flag.set()
        if wait:
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.index_queue_status()

    async def cancel_queued_book(self, book_id: str) -> dict[str, Any]:
        """Cancel one pending/current book and leave it safely resumable."""
        book = self.store.get_book(book_id)
        if book is None:
            raise KeyError(book_id)
        if book.status not in {"pending", "indexing"}:
            return {"book_id": book_id, "status": book.status, "cancelled": False}
        flag = self._cancel_flags.get(book_id)
        if flag is not None:
            flag.set()
        # Purge immediately so the API and the next queue snapshot agree;
        # _run_index will observe the pending state at its next checkpoint.
        self.store.cancel_indexing(book_id)
        return {"book_id": book_id, "status": "pending", "cancelled": True}

    @staticmethod
    def _retryable_index_error(error: str | None) -> bool:
        text = (error or "").casefold()
        return any(token in text for token in (
            "connection", "timeout", "temporarily", "ollama", "http", "server", "transport"
        ))

    async def _run_index_queue(self) -> None:
        """Consume all pending books sequentially until paused or exhausted."""
        stop = self._index_queue_stop
        try:
            while stop is not None and not stop.is_set():
                books = [
                    book for book in self.store.list_all_books()
                    if book.status == "pending"
                ]
                if not books:
                    break
                book = books[0]
                subject_id = self.store.get_book_subject_id(book.id)
                if subject_id is None:
                    self.store.set_book_error(book.id, "subject missing")
                    self._index_queue_errors += 1
                    continue
                self._index_queue_current = book.id
                self._cancel_flags[book.id] = threading.Event()
                try:
                    await self._run_index(
                        subject_id, book, Path(book.source_path), book.format
                    )
                finally:
                    self._index_queue_current = None
                final = self.store.get_book(book.id)
                if final is not None and final.status == "indexed":
                    self._index_queue_completed += 1
                elif final is not None and final.status == "error":
                    if self._retryable_index_error(final.error) and final.retry_count < 3:
                        self.store.retry_book(book.id)
                        await asyncio.sleep(min(30, 2 ** final.retry_count))
                        continue
                    self._index_queue_errors += 1
        finally:
            self._index_queue_current = None
            self._index_queue_task = None
            self._index_queue_stop = None

    def nightly_status(self) -> dict[str, Any]:
        """Return scheduler state without starting any background work."""
        now = datetime.now().strftime("%H:%M")
        start = getattr(self.config, "tutor_nightly_start_at", "23:00")
        stop = getattr(self.config, "tutor_nightly_stop_at", "07:00")
        return {
            "enabled": bool(getattr(self.config, "tutor_nightly_enabled", False)),
            "scheduler_running": bool(self._nightly_scheduler_task and not self._nightly_scheduler_task.done()),
            "window_open": self._nightly_window_open(now, start, stop),
            "on_ac_power": self._on_ac_power(),
            "start_at": start,
            "stop_at": stop,
            "only_on_ac": getattr(self.config, "tutor_nightly_only_on_ac", True),
            "last_run": self._nightly_last_run,
            "last_error": self._nightly_last_error,
            "prepare_enabled": getattr(self.config, "tutor_nightly_prepare_enabled", False),
            "prepare": self._nightly_prepare_status,
            "queue": self.index_queue_status(),
        }

    @staticmethod
    def _nightly_window_open(now: str, start: str, stop: str) -> bool:
        """Support normal and overnight windows such as 23:00 → 07:00."""
        if start == stop:
            return False
        if start < stop:
            return start <= now < stop
        return now >= start or now < stop

    @staticmethod
    def _on_ac_power() -> bool:
        """Best-effort Linux power check; unknown platforms are allowed."""
        power_dir = Path("/sys/class/power_supply")
        if not power_dir.exists():
            return True
        found = False
        for online in power_dir.glob("*/online"):
            try:
                found = True
                if online.read_text(encoding="utf-8").strip() == "1":
                    return True
            except OSError:
                continue
        return True if not found else False

    async def start_nightly_scheduler(self) -> dict[str, Any]:
        """Start one lightweight in-process clock scheduler when enabled."""
        if self._nightly_scheduler_task is None or self._nightly_scheduler_task.done():
            self._nightly_scheduler_task = asyncio.create_task(self._nightly_loop())
        return self.nightly_status()

    async def stop_nightly_scheduler(self) -> dict[str, Any]:
        task = self._nightly_scheduler_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._nightly_scheduler_task = None
        return self.nightly_status()

    async def _nightly_loop(self) -> None:
        """Check the local clock periodically; no external service is used."""
        while True:
            try:
                start = getattr(self.config, "tutor_nightly_start_at", "23:00")
                stop = getattr(self.config, "tutor_nightly_stop_at", "07:00")
                now = datetime.now().strftime("%H:%M")
                allowed_power = not getattr(self.config, "tutor_nightly_only_on_ac", True) or self._on_ac_power()
                window_key = datetime.now().strftime("%Y-%m-%d")
                if start > stop and now < stop:
                    window_key = (datetime.now().date()).isoformat()
                books = self.store.list_all_books()
                if (
                    getattr(self.config, "tutor_nightly_enabled", False)
                    and self._nightly_window_open(now, start, stop)
                    and allowed_power
                    and any(book.status == "pending" for book in books)
                    and window_key != self._nightly_last_window_key
                    and not (self._index_queue_task and not self._index_queue_task.done())
                ):
                    self._nightly_last_window_key = window_key
                    await self._run_nightly_cycle()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._nightly_last_error = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(30)

    async def _run_nightly_cycle(self) -> None:
        started = datetime.now().isoformat(timespec="seconds")
        self._nightly_last_run = started
        self._nightly_last_error = None
        try:
            await asyncio.wait_for(
                self.start_index_queue(),
                timeout=max(1, getattr(self.config, "tutor_nightly_max_runtime_minutes", 420)) * 60,
            )
            queue_task = self._index_queue_task
            if queue_task is not None:
                await asyncio.wait_for(
                    queue_task,
                    timeout=max(1, getattr(self.config, "tutor_nightly_max_runtime_minutes", 420)) * 60,
                )
            if getattr(self.config, "tutor_nightly_prepare_enabled", False):
                for subject in self.store.list_subjects():
                    try:
                        report = await self.prepare_knowledge(subject.id)
                        self._nightly_prepare_status[subject.id] = report.to_dict()
                    except Exception as exc:
                        self._nightly_prepare_status[subject.id] = {"error": str(exc)}
        except asyncio.TimeoutError:
            await self.stop_index_queue()
            self._nightly_last_error = "durée maximale nocturne atteinte"
        except Exception as exc:
            self._nightly_last_error = f"{type(exc).__name__}: {exc}"

    def run_maintenance(self, *, vacuum: bool = False, backup: bool = True) -> dict[str, Any]:
        """Run safe local maintenance and optionally create a SQLite backup."""
        report = self.store.maintenance_report()
        deleted = self.store.cleanup_orphan_embeddings()
        backup_path = None
        if backup:
            backup_dir = self.config.config_dir / "tutor" / "backups"
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = str(self.store.backup_to(backup_dir / f"library-{stamp}.db"))
        optimized = self.store.optimize(vacuum=vacuum)
        return {
            "before": report,
            "orphan_embeddings_deleted": deleted,
            "backup_path": backup_path,
            "after": optimized,
        }

    def set_embedding_model(self, model: str) -> None:
        """Switch the active embedding model and invalidate retrieval caches."""
        model = model.strip()
        if not model:
            raise ValueError("embedding model must be non-empty")
        self.model = model
        self.retriever.set_model(model)

    def _generation_options(self) -> OllamaOptions:
        """Build inference options from persisted settings.

        The previous implementation rebuilt an ``OllamaOptions`` instance with
        only ``num_ctx`` for every tutor call, silently discarding settings such
        as ``num_predict``, ``num_thread`` and ``keep_alive``.  A 4096-token
        context and a 384-token output cap are lighter CPU-oriented defaults;
        explicit user settings still take precedence.

        Provider-aware output cap : pour le fournisseur cloud (OpenAI-compatible),
        on ne plafonne PAS ``num_predict`` (qui devient ``max_tokens``) — sinon la
        réflexion ET la réponse sont coupées en plein milieu. Le cloud gère le
        calcul, il peut produire de longues réponses. Le plafond ne s'applique
        qu'aux modèles locaux (Ollama/GGUF) pour borner l'usage CPU/RAM.
        """
        is_cloud = (
            getattr(self.config, "llm_provider", "ollama") == "openai"
            and getattr(self.config, "llm_base_url", "")
        )
        values = self.config.options.to_dict()
        if not values.get("num_ctx"):
            values["num_ctx"] = 4096
        if not values.get("num_predict") and not is_cloud:
            # Plafond par défaut pour les modèles locaux (CPU) : 2048 jetons.
            values["num_predict"] = 2048
        return OllamaOptions(**values)

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
                self._autotitle_conversation(conversation_id, question)
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

    def _autotitle_conversation(
        self, conversation_id: str, question: str
    ) -> None:
        """Nomme automatiquement une conversation encore sans titre.

        Utilise la première question comme titre (tronqué) dès que le premier
        message y est enregistré. Best-effort : ne lève jamais vers le run.
        """
        try:
            sess = self.store.get_tutoring_session(conversation_id)
            if sess is None or (sess.title or "").strip():
                return  # inconnue ou déjà nommée
            transcript = self.store.get_session_transcript(conversation_id)
            if len(transcript) != 1:
                return  # pas le tout premier message
            title = " ".join(question.split())
            if len(title) > 60:
                title = title[:60].rstrip() + "…"
            if title:
                self.store.rename_conversation(conversation_id, title)
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
        history = self._conversation_history(conversation_id, question)

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
        if self.store.get_indexed_chunks(subject_id, model=self.model):
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
                history=history,
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
            *history,
            Message(role=MessageRole.USER, content=user_prompt),
        ]
        if think:
            ti = build_think_instruction(True)
            if ti:
                messages.append(Message(role=MessageRole.USER, content=ti))

        # Accumulate the streamed response so we can validate citations once
        # the full text is available (US10 / T059).
        _text_parts: list[str] = []
        async for frame in self._stream_llm(messages, model, think, session_id, cancel):
            if frame.get("type") == "delta":
                _text_parts.append(frame.get("text", ""))
            yield frame

        # --- US10: citation validation (T059) ---
        response_text = "".join(_text_parts)
        if response_text:
            _valid, _invalid = validate_citations(response_text, sources_frame)
            if _invalid:
                yield {
                    "type": "citation_warnings",
                    "warnings": _invalid,
                    "valid_citations": _valid,
                }

    # ------------------------------------------------------------------
    # Chat without sources (Phase 6 UX): model-knowledge answers
    # ------------------------------------------------------------------

    _MAX_HISTORY_MESSAGES = 12
    _MAX_HISTORY_CHARS = 12000

    def _conversation_history(
        self, conversation_id: str | None, current_question: str
    ) -> list[Message]:
        """Return a bounded prior transcript suitable for an LLM prompt."""
        if not conversation_id:
            return []
        rows = self.store.get_session_transcript(conversation_id)
        if (
            rows
            and rows[-1].get("role") == "user"
            and rows[-1].get("text") == current_question
        ):
            rows = rows[:-1]
        selected: list[Message] = []
        total_chars = 0
        for row in reversed(rows):
            role = row.get("role")
            text = str(row.get("text") or "").strip()
            if role not in ("user", "assistant") or not text:
                continue
            if len(selected) >= self._MAX_HISTORY_MESSAGES:
                break
            remaining = self._MAX_HISTORY_CHARS - total_chars
            if remaining <= 0:
                break
            text = text[-remaining:]
            selected.append(
                Message(
                    role=MessageRole.USER if role == "user" else MessageRole.ASSISTANT,
                    content=text,
                )
            )
            total_chars += len(text)
        selected.reverse()
        return selected

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
        history: list[Message] | None = None,
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
            *(history or []),
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
        options = self._generation_options()
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
                elif ev.kind == "done":
                    if ev.stats is not None:
                        yield {
                            "type": "stats",
                            "prompt_tokens": ev.stats.prompt_tokens,
                            "generated_tokens": ev.stats.generated_tokens,
                            "tok_s": ev.stats.generation_speed,
                        }
                    if getattr(ev, "truncated", False):
                        yield {
                            "type": "warning",
                            "code": "truncated",
                            "message": (
                                "Réponse interrompue : limite de jetons atteinte. "
                                "Augmentez « num_predict » dans les réglages pour "
                                "des réponses plus longues."
                            ),
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
    # Revision sheet (US2 / T016): auto-generated fiche de révision
    # ------------------------------------------------------------------

    def generate_revision_sheet(
        self,
        subject_id: str,
        book_id: str | None = None,
        chapter: str | None = None,
    ) -> dict[str, Any]:
        """Generate a revision sheet (fiche) from the subject's indexed chunks.

        When *book_id* is provided, only chunks from that book are used.
        Otherwise all indexed chunks for the subject are included.
        Returns ``{"sheet": text, "subject_name": str}``.
        """
        subject = self.store.require_subject(subject_id)
        if book_id:
            raw_chunks = self.store.get_chunks_by_provenance(
                subject_id, book_id, chapter
            )
        else:
            raw_chunks = self.store.get_subject_chunks(subject_id)

        texts = [c["text"] for c in raw_chunks if c.get("text")]

        level = self.config.tutor_level or "intermediate"
        system_prompt = build_revision_sheet_prompt(texts, subject.name, level)
        model = self.config.tutor_model

        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(
                role=MessageRole.USER,
                content="Génère la fiche de révision à partir des extraits fournis.",
            ),
        ]

        # Synchronous collection via an internal event-loop bridge so the
        # method stays synchronous (matches existing non-streaming helpers).
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an async context — run in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self._collect_revision_sheet(model, messages),
                )
                sheet = future.result(timeout=120)
        else:
            sheet = asyncio.run(self._collect_revision_sheet(model, messages))

        return {"sheet": sheet, "subject_name": subject.name}

    async def _collect_revision_sheet(
        self, model: str, messages: list[Message]
    ) -> str:
        """Non-streaming LLM call for revision sheet generation."""
        options = self._generation_options()
        parts: list[str] = []
        truncated = False
        async for ev in self._llm_client.chat_stream(
            messages, model, options=options
        ):
            if ev.kind == "content":
                parts.append(ev.text)
            elif ev.kind == "done" and getattr(ev, "truncated", False):
                truncated = True
        text = "".join(parts)
        if truncated:
            text += "\n\n[Réponse tronquée : limite de jetons atteinte]"
        return text

    async def _llm_collect(
        self, messages: list[Message], options: OllamaOptions
    ) -> str:
        """Run a non-streaming LLM call and return the concatenated content."""
        parts: list[str] = []
        truncated = False
        async for ev in self._llm_client.chat_stream(
            messages, self.config.tutor_model, options=options
        ):
            if ev.kind == "content":
                parts.append(ev.text)
            elif ev.kind == "done" and getattr(ev, "truncated", False):
                truncated = True
        text = "".join(parts)
        if truncated:
            text += "\n\n[Réponse tronquée : limite de jetons atteinte]"
        return text

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
        options = self._generation_options()
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
            options = self._generation_options()
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
            # T063: record exercise errors in error_history.
            if verdict in ("incorrect", "partial"):
                concept = self.store.get_concept(exercise.concept_id)
                self.store.record_error(
                    subject_id=exercise.subject_id,
                    concept_name=concept.name if concept else exercise.concept_id,
                    question_text=exercise.statement,
                    given_answer=answer,
                    correct_answer=exercise.solution,
                    error_type=verdict,
                )
            if verdict == "correct":
                self.store.update_exercise(exercise_id, status="solved")
                # US15 / T088: +15 XP for a correct exercise answer.
                self.store.add_xp(15)
            # Feature 008 — US4 (FR-016): after each activity, recompute only a
            # window of path steps, not the whole path.
            try:
                from .adaptation import AdaptationService
                AdaptationService(self.store).recompute_window(
                    exercise.subject_id, anchor_step_id=None
                )
            except Exception:
                # Adaptation is best-effort; never break grading on it.
                pass

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

    # ------------------------------------------------------------------
    # Error history (Feature 007 — US11, T064)
    # ------------------------------------------------------------------

    def get_error_history(
        self,
        subject_id: str,
        concept_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent errors for a subject, optionally filtered by concept."""
        return self.store.get_error_history(subject_id, concept_name, limit)

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
        options = self._generation_options()
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
                elif ev.kind == "done":
                    if ev.stats is not None:
                        yield {
                            "type": "stats",
                            "prompt_tokens": ev.stats.prompt_tokens,
                            "generated_tokens": ev.stats.generated_tokens,
                            "tok_s": ev.stats.generation_speed,
                        }
                    if getattr(ev, "truncated", False):
                        yield {
                            "type": "warning",
                            "code": "truncated",
                            "message": (
                                "Réponse interrompue : limite de jetons atteinte. "
                                "Augmentez « num_predict » dans les réglages pour "
                                "des réponses plus longues."
                            ),
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
        return self.store.list_chunks_meta(subject_id)

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
        self.store.require_subject(subject_id)
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
        """Correct a quiz/exam submission; enforces exam rules (T040).

        US15 / T088: Awards +20 XP for quiz completion and a +10 XP streak
        bonus when the learner has been active on consecutive days.
        """
        report = await self.quiz_engine.submit_answers(
            quiz_id, answers, hint_requested=hint_requested
        )
        # US15 / T088: gamification — XP for quiz completion + streak bonus.
        self.store.add_xp(20)
        streak = self.store.update_streak()
        if streak > 1:
            self.store.add_xp(10)
        return report

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
        self.store.require_subject(subject_id)  # KeyError if unknown
        summaries = self.store.list_session_summaries(subject_id)
        last = summaries[0] if summaries else None
        subject = self.store.require_subject(subject_id)

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
        subject = self.store.require_subject(subject_id)
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
        self.store.require_subject(subject_id)  # KeyError if unknown
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
                text_segments = [
                    (str(p.get("text", "")), {})
                    for p in pages
                    if str(p.get("text", "")).strip()
                ]
            else:
                text_segments = list(extract_text(path, fmt))

            if self._is_cancelled(book_id):
                self.store.cancel_indexing(book_id)
                return

            # Build combined text with metadata markers so chunk_text_structured
            # can re-derive page/section info from the annotated text.
            combined_parts: list[str] = []
            for text, meta in text_segments:
                if not text.strip():
                    continue
                if meta.get("page") is not None:
                    combined_parts.append(
                        f"--- Page {meta['page']} ---\n{text}"
                    )
                elif meta.get("section"):
                    combined_parts.append(f"# {meta['section']}\n{text}")
                else:
                    combined_parts.append(text)
            combined_text = "\n\n".join(combined_parts)

            chunk_dicts = chunk_text_structured(combined_text)
            if not chunk_dicts:
                self.store.mark_indexed(book_id, 0)
                return

            chunk_texts = [c["text"] for c in chunk_dicts]
            batch_size = getattr(self.config, "tutor_embed_batch_size", 16)
            max_concurrency = getattr(self.config, "tutor_max_parallel_embed", 1)
            self.store.update_index_progress(book_id, 0, len(chunk_texts))

            if self.embedding_provider is not None:
                embeddings = await self._embed_with_provider(
                    chunk_texts,
                    batch_size=batch_size,
                    max_concurrency=max_concurrency,
                )
            else:
                embeddings = await embed_texts(
                    self.client,
                    self.model,
                    chunk_texts,
                    self.store,
                    batch_size=batch_size,
                    max_concurrency=max_concurrency,
                )
            if self._is_cancelled(book_id):
                self.store.cancel_indexing(book_id)
                return

            self.store.add_chunks(
                subject_id, book_id, chunk_dicts, embeddings, self.model
            )
            self.store.mark_indexed(book_id, len(chunk_dicts))
            # A subject index is cached after the first question. Invalidate it
            # so newly indexed books/chunks become searchable immediately.
            self.retriever.invalidate(subject_id)
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

    async def _embed_with_provider(
        self,
        chunks: list[str],
        *,
        batch_size: int = 16,
        max_concurrency: int = 1,
    ) -> list[list[float]]:
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
            batches = [
                to_embed[start : start + max(1, int(batch_size))]
                for start in range(0, len(to_embed), max(1, int(batch_size)))
            ]
            semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

            async def _embed_batch(batch: list[tuple[int, str]]) -> list[tuple[int, list[float]]]:
                async with semaphore:
                    vectors = await self.embedding_provider.embed([t for _, t in batch])
                    return [(i, vec) for (i, _), vec in zip(batch, vectors)]

            batch_results = await asyncio.gather(*[_embed_batch(batch) for batch in batches])
            for batch_pairs in batch_results:
                for i, vec in batch_pairs:
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
        options = self._generation_options()

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

    # ------------------------------------------------------------------
    # Learning paths (Feature 006 — adaptive learning)
    # ------------------------------------------------------------------

    def create_path(self, subject_id: str, title: str, description: str = "") -> dict:
        """Create a learning path and return it as a dict."""
        path = self.store.create_learning_path(subject_id, title, description)
        return path.to_dict()

    def list_paths(self, subject_id: str) -> list[dict]:
        """List all learning paths for a subject."""
        return [p.to_dict() for p in self.store.list_learning_paths(subject_id)]

    def get_path(self, path_id: str) -> dict | None:
        """Get a learning path with its steps and progress."""
        path = self.store.get_learning_path(path_id)
        if path is None:
            return None
        d = path.to_dict()
        steps = [s.to_dict() for s in self.store.list_path_steps(path_id)]
        d["steps"] = steps
        if steps:
            completed = sum(1 for s in steps if s["status"] == "completed")
            d["progress"] = round(completed / len(steps) * 100, 1)
        else:
            d["progress"] = 0.0
        return d

    def update_path(self, path_id: str, *, title: str | None = None,
                    description: str | None = None, status: str | None = None) -> dict | None:
        """Update a learning path."""
        self.store.update_learning_path(path_id, title=title, description=description, status=status)
        return self.get_path(path_id)

    def delete_path(self, path_id: str) -> bool:
        """Delete a learning path and its steps."""
        path = self.store.get_learning_path(path_id)
        if path is None:
            return False
        self.store.delete_learning_path(path_id)
        return True

    def add_path_step(self, path_id: str, activity_type: str, activity_id: str,
                      title: str = "") -> dict:
        """Add a step to a learning path."""
        step = self.store.add_path_step(path_id, activity_type, activity_id, title)
        return step.to_dict()

    def reorder_path_steps(self, path_id: str, step_ids: list[str]) -> list[dict]:
        """Reorder steps in a learning path."""
        self.store.reorder_path_steps(path_id, step_ids)
        return [s.to_dict() for s in self.store.list_path_steps(path_id)]

    def complete_path_step(self, step_id: str) -> dict | None:
        """Mark a path step as completed."""
        step = self.store.get_path_step(step_id)
        if step is None:
            return None
        self.store.update_path_step(step_id, status="completed")
        updated = self.store.get_path_step(step_id)
        return updated.to_dict() if updated else None

    def delete_path_step(self, step_id: str) -> bool:
        """Delete a step from a learning path."""
        step = self.store.get_path_step(step_id)
        if step is None:
            return False
        self.store.delete_path_step(step_id)
        return True

    async def auto_generate_path(self, subject_id: str) -> dict[str, Any]:
        """Auto-generate a learning path from concepts and diagnostic gaps (US13 / T075).

        1. Fetches the subject's concepts and current gaps.
        2. Calls the LLM via ``build_learning_path_prompt`` to order concepts
           by pedagogical dependency.
        3. Creates a ``LearningPath`` with ``PathStep`` entries for each
           ordered concept.
        4. Returns the created path as a dict (with steps).
        """
        subject = self.store.require_subject(subject_id)
        concepts = self.store.list_concepts(subject_id)
        if not concepts:
            raise KeyError(f"Aucun concept pour la matière : {subject_id}")

        concept_names = [c.name for c in concepts]
        # Gaps from progress tracker (FR-022).
        gap_rows = self.progress.get_gaps(subject_id)
        gap_names = [g.concept.name for g in gap_rows]
        level = self.config.tutor_level or "intermediate"

        # Build prompt and call LLM for ordered concept list.
        system_prompt = build_learning_path_prompt(concept_names, gap_names, level)
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(
                role=MessageRole.USER,
                content=(
                    f"Ordonne les concepts de la matière « {subject.name} » "
                    f"({len(concept_names)} concepts) en un parcours d'apprentissage optimal."
                ),
            ),
        ]
        options = self._generation_options()
        raw = await self._llm_collect(messages, options)

        # Parse the LLM response: expect a JSON array of concept names.
        ordered_names = self._parse_learning_path_response(raw, concept_names)

        # Create the learning path.
        title = f"Parcours auto-généré — {subject.name}"
        description = (
            f"Parcours optimisé pour {len(ordered_names)} concepts"
            + (f", en priorité sur : {', '.join(gap_names)}" if gap_names else "")
        )
        path = self.store.create_learning_path(subject_id, title, description)

        # Build a name→concept lookup for fast access.
        by_name: dict[str, Any] = {c.name.lower(): c for c in concepts}

        # Create PathSteps in the LLM-ordered sequence.
        for ordinal, name in enumerate(ordered_names):
            concept = by_name.get(name.lower())
            if concept is None:
                continue  # skip unknown names returned by the LLM
            self.store.add_path_step(
                path.id, "concept", concept.id, title=concept.name, ordinal=ordinal
            )

        # Return the path with its steps.
        result = path.to_dict()
        result["steps"] = [s.to_dict() for s in self.store.list_path_steps(path.id)]
        return result

    @staticmethod
    def _parse_learning_path_response(raw: str, original_names: list[str]) -> list[str]:
        """Parse the LLM JSON array response for learning path ordering (T075).

        Returns concept names in the LLM-ordered sequence, filtered to only
        names present in ``original_names``. Falls back to the original order
        on parse failure.
        """
        import re as _re

        text = (raw or "").strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            text = _re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n?", "", text)
            text = _re.sub(r"\r?\n?[ \t]*```$", "", text).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return list(original_names)  # fallback: original order
        try:
            arr = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return list(original_names)
        if not isinstance(arr, list):
            return list(original_names)
        # Filter to known names, preserve LLM order.
        known = {n.lower() for n in original_names}
        ordered: list[str] = []
        seen: set[str] = set()
        for item in arr:
            name = str(item or "").strip()
            if name.lower() in known and name.lower() not in seen:
                ordered.append(name)
                seen.add(name.lower())
        # Append any concepts the LLM missed (preserving original order).
        for orig in original_names:
            if orig.lower() not in seen:
                ordered.append(orig)
        return ordered

    # ------------------------------------------------------------------
    # Learning path from books TOC / captured program (Feature 008)
    # ------------------------------------------------------------------

    async def generate_path_from_books(
        self, subject_id: str, book_ids: list[str],
    ) -> dict[str, Any]:
        """Generate a learning path from selected books' table of contents.

        1. Fetches chunks from the selected books, grouped by chapter.
        2. Builds a TOC structure: [{title, chapters: [{title, sections: [...]}]}]
        3. Calls the LLM via build_path_from_books_prompt to generate structured steps.
        4. Creates a LearningPath with PathStep entries.
        5. Returns the created path as a dict.
        """
        subject = self.store.require_subject(subject_id)

        # Fetch chunks from the selected books to build the TOC structure
        # via public store API (no private _conn access).
        rows = self.store.list_chunks_meta(subject_id, book_ids)

        # Group by book → chapter → sections.
        book_toc: dict[str, dict[str, set[str]]] = {}
        for row in rows:
            bid = row["book_id"]
            ch = row["chapter"] or ""
            sec = row["section"] or ""
            if bid not in book_toc:
                book_toc[bid] = {}
            if ch not in book_toc[bid]:
                book_toc[bid][ch] = set()
            if sec:
                book_toc[bid][ch].add(sec)

        # Build the book_structures list for the prompt.
        book_structures: list[dict] = []
        for bid in book_ids:
            book = self.store.get_book(bid)
            title = book.title if book else bid
            chapters: list[dict] = []
            if bid in book_toc:
                for ch_title, sections in book_toc[bid].items():
                    chapters.append({
                        "title": ch_title,
                        "sections": sorted(sections),
                    })
            book_structures.append({"title": title, "chapters": chapters})

        # Build prompt and call LLM.
        level = self.config.tutor_level or "intermediate"
        system_prompt = build_path_from_books_prompt(book_structures, level)
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(
                role=MessageRole.USER,
                content=(
                    f"Crée un parcours d'apprentissage structuré à partir des "
                    f"{len(book_structures)} livres sélectionnés pour la matière "
                    f"« {subject.name} »."
                ),
            ),
        ]
        options = self._generation_options()
        raw = await self._llm_collect(messages, options)

        # Parse the LLM response.
        steps_data = self._parse_path_steps_response(raw)

        # Create the learning path.
        title = f"Parcours depuis livres — {subject.name}"
        description = (
            f"Parcours structuré basé sur {len(book_structures)} livre(s) "
            f"({len(steps_data)} étapes)"
        )
        path = self.store.create_learning_path(subject_id, title, description)

        # Create PathSteps from the LLM response.
        for ordinal, step in enumerate(steps_data):
            activity_type = step.get("type", "concept")
            if activity_type not in ("concept", "exercise", "quiz", "reading"):
                activity_type = "concept"
            step_title = step.get("title", f"Étape {ordinal + 1}")
            source = step.get("source", "")
            # Determine activity_id: chapter name, book title, or generic.
            if activity_type in ("exercise", "quiz"):
                activity_id = f"{activity_type}-{source}-{ordinal}"
            else:
                activity_id = source or "Général"
            self.store.add_path_step(
                path.id, activity_type, activity_id, title=step_title,
                ordinal=ordinal,
            )

        result = path.to_dict()
        result["steps"] = [s.to_dict() for s in self.store.list_path_steps(path.id)]
        return result

    def path_from_program(
        self, subject_id: str, program_id: str,
    ) -> dict[str, Any]:
        """Convert a confirmed captured program into a learning path.

        1. Fetches the captured program and its nodes.
        2. Converts chapters → concept steps, competencies → exercise steps.
        3. Adds a quiz step after every 2-3 concept steps.
        4. Creates a LearningPath with PathStep entries.
        5. Returns the created path as a dict.
        """
        subject = self.store.require_subject(subject_id)
        program = self.store.get_captured_program(program_id)
        if program is None:
            raise KeyError(f"Programme introuvable : {program_id}")
        if program.subject_id != subject_id:
            raise ValueError("Le programme n'appartient pas à cette matière")

        nodes = self.store.get_program_nodes(program_id)
        if not nodes:
            raise KeyError(f"Aucun nœud pour le programme : {program_id}")

        # Build a tree from flat nodes (parent_id relationships).
        by_id = {n.id: n for n in nodes}
        children: dict[str | None, list] = {}
        for n in nodes:
            parent = n.parent_id or None
            children.setdefault(parent, []).append(n)

        # Traverse tree in depth-first order, generating steps.
        steps: list[dict] = []
        concept_count = 0  # tracks consecutive concepts for quiz insertion.

        def _traverse(parent_id: str | None) -> None:
            nonlocal concept_count
            for node in (children.get(parent_id) or []):
                if node.kind == "chapter":
                    steps.append({
                        "activity_type": "concept",
                        "activity_id": node.id,
                        "title": node.title,
                    })
                    concept_count += 1
                    # Insert quiz after every 2-3 concepts.
                    if concept_count >= 2:
                        steps.append({
                            "activity_type": "quiz",
                            "activity_id": f"quiz-{node.id}",
                            "title": f"Quiz — {node.title}",
                        })
                        concept_count = 0
                elif node.kind == "competency":
                    steps.append({
                        "activity_type": "exercise",
                        "activity_id": node.id,
                        "title": node.title,
                    })
                else:
                    # sub_part or other kinds → concept step.
                    steps.append({
                        "activity_type": "concept",
                        "activity_id": node.id,
                        "title": node.title,
                    })
                # Recurse into children.
                _traverse(node.id)

        _traverse(None)

        if not steps:
            raise KeyError("Aucune étape n'a pu être générée depuis le programme")

        # Create the learning path.
        title = f"Parcours depuis programme — {subject.name}"
        description = (
            f"Parcours converti depuis le programme « {program.recognized_text[:80]}… » "
            f"({len(steps)} étapes)"
        )
        path = self.store.create_learning_path(subject_id, title, description)

        # Create PathSteps.
        for ordinal, step in enumerate(steps):
            self.store.add_path_step(
                path.id,
                step["activity_type"],
                step["activity_id"],
                title=step["title"],
                ordinal=ordinal,
            )

        result = path.to_dict()
        result["steps"] = [s.to_dict() for s in self.store.list_path_steps(path.id)]
        return result

    @staticmethod
    def _parse_path_steps_response(raw: str) -> list[dict[str, Any]]:
        """Parse the LLM JSON array response for structured learning path steps.

        Expects: [{"title": "...", "type": "concept", "duration": 15, "source": "..."}]
        Falls back to a single concept step on parse failure.
        """
        import re as _re

        text = (raw or "").strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            text = _re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n?", "", text)
            text = _re.sub(r"\r?\n?[ \t]*```$", "", text).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            arr = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(arr, list):
            return []
        valid_types = {"concept", "exercise", "quiz", "reading"}
        result: list[dict[str, Any]] = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            step_type = str(item.get("type", "concept")).strip()
            if step_type not in valid_types:
                step_type = "concept"
            result.append({
                "title": str(item.get("title", "")),
                "type": step_type,
                "duration": int(item.get("duration", 15)),
                "source": str(item.get("source", "")),
            })
        return result

    def get_subject_domain(self, subject_id: str) -> str:
        """Get the classified domain for a subject."""
        return self.store.get_subject_domain(subject_id)

    def set_subject_domain(self, subject_id: str, domain: str) -> None:
        """Set the domain for a subject (manual override)."""
        self.store.set_subject_domain(subject_id, domain)

    async def classify_subject(self, subject_id: str) -> str:
        """Auto-classify a subject's content domain using the hybrid classifier."""
        from .classifier import classify_subject_chunks
        return await classify_subject_chunks(
            self.store, subject_id, self._llm_client, self.config.tutor_model
        )

    # ------------------------------------------------------------------
    # Summary (US1 / T011): document summary generation
    # ------------------------------------------------------------------

    async def summarize_book(self, book_id: str, chapter: str | None = None) -> dict[str, Any]:
        """Generate a structured summary for a book (US1)."""
        book = self.store.get_book(book_id)
        if book is None:
            raise KeyError(f"Livre introuvable : {book_id}")
        # Books are linked to subjects through the subject_books join table;
        # Book itself intentionally has no subject_id field.
        subject_id = self.store.get_book_subject_id(book_id)
        if subject_id is None:
            raise KeyError(f"Livre non rattaché à un sujet : {book_id}")
        raw_chunks = self.store.get_chunks_by_provenance(
            subject_id, book_id, chapter
        )
        texts = [c["text"] for c in raw_chunks if c.get("text")]
        if not texts:
            return {"summary": "Aucun contenu indexé pour ce livre.", "book_title": book.title}
        prompt = build_summary_prompt(texts[:30], book.title, chapter)
        model = self.config.tutor_model
        messages = [
            Message(role=MessageRole.SYSTEM, content=prompt),
            Message(role=MessageRole.USER, content="Génère le résumé à partir des extraits fournis."),
        ]
        parts: list[str] = []
        truncated = False
        async for ev in self._llm_client.chat_stream(
            messages, model, options=self._generation_options()
        ):
            if ev.kind == "content":
                parts.append(ev.text)
            elif ev.kind == "done" and getattr(ev, "truncated", False):
                truncated = True
        summary = "".join(parts)
        if truncated:
            summary += "\n\n[Réponse tronquée : limite de jetons atteinte]"
        return {"summary": summary, "book_title": book.title}

    # ------------------------------------------------------------------
    # Diagnostic initial / quiz de positionnement (US3 / T021-T023)
    # ------------------------------------------------------------------

    _DIAGNOSTIC_TOTAL_QUESTIONS = 10

    def start_diagnostic(self, subject_id: str) -> dict[str, Any]:
        """Start a diagnostic quiz for a subject (T021).

        Creates a tutoring_session with ``mode="diagnostic"``, picks the
        first concept, generates an initial question, and persists the
        diagnostic state in the session's transcript path as JSON.

        Returns a dict with ``session_id``, ``question``, ``options``,
        ``question_num``, ``total_questions``.
        """
        subject = self.store.require_subject(subject_id)
        concepts = self.store.list_concepts(subject_id)
        if not concepts:
            raise KeyError(f"Aucun concept pour la matière : {subject_id}")

        total = min(self._DIAGNOSTIC_TOTAL_QUESTIONS, len(concepts))
        concept_pool = concepts[:total]
        level = self.config.tutor_level or "intermediate"

        # Create a tutoring session with mode=diagnostic in the title
        session = self.store.create_tutoring_session(
            subject_id, title="diagnostic"
        )

        # Build the first question via LLM
        first_concept = concept_pool[0]
        question_data = self._generate_diagnostic_question(first_concept.name, level)

        # Persist diagnostic state as JSON in transcript_path
        state: dict[str, Any] = {
            "mode": "diagnostic",
            "subject_id": subject_id,
            "concept_names": [c.name for c in concept_pool],
            "concept_ids": [c.id for c in concept_pool],
            "total_questions": total,
            "current_index": 0,
            "current_level": level,
            "correct_count": 0,
            "answers": [],
            "current_correct": question_data.get("correct", "A"),
            "current_explanation": question_data.get("explication", ""),
        }
        state_json = json.dumps(state, ensure_ascii=False)
        self.store._conn.execute(
            "UPDATE tutoring_sessions SET transcript_path = ? WHERE id = ?",
            (state_json, session.id),
        )
        self.store._conn.commit()

        return {
            "session_id": session.id,
            "question": question_data.get("question", ""),
            "options": question_data.get("options", {}),
            "question_num": 1,
            "total_questions": total,
        }

    def _generate_diagnostic_question(
        self, concept_name: str, level: str
    ) -> dict[str, Any]:
        """Generate a diagnostic MCQ question (synchronous bridge, T020)."""
        system_prompt = build_diagnostic_question_prompt(concept_name, level)
        model = self.config.tutor_model
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
            Message(
                role=MessageRole.USER,
                content=(
                    f"Génère une question de diagnostic sur le concept : "
                    f"{concept_name}, au niveau {level}."
                ),
            ),
        ]
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self._collect_diagnostic_question(model, messages),
                )
                raw = future.result(timeout=120)
        else:
            raw = asyncio.run(
                self._collect_diagnostic_question(model, messages)
            )

        return self._parse_diagnostic_response(raw)

    async def _collect_diagnostic_question(
        self, model: str, messages: list[Message]
    ) -> str:
        """Non-streaming LLM call for diagnostic question generation."""
        options = self._generation_options()
        parts: list[str] = []
        truncated = False
        async for ev in self._llm_client.chat_stream(
            messages, model, options=options
        ):
            if ev.kind == "content":
                parts.append(ev.text)
            elif ev.kind == "done" and getattr(ev, "truncated", False):
                truncated = True
        text = "".join(parts)
        if truncated:
            text += "\n\n[Réponse tronquée]"
        return text

    @staticmethod
    def _parse_diagnostic_response(text: str) -> dict[str, Any]:
        """Parse the LLM diagnostic JSON into a clean dict (T020)."""
        data = _extract_json(text)
        question = str(data.get("question", "")).strip()
        raw_options = data.get("options", {})
        if not isinstance(raw_options, dict):
            raw_options = {}
        options: dict[str, str] = {}
        for key in ("A", "B", "C", "D"):
            options[key] = str(raw_options.get(key, "")).strip()
        correct = str(data.get("correct", "A")).strip().upper()
        if correct not in ("A", "B", "C", "D"):
            correct = "A"
        explanation = str(data.get("explication", "")).strip()
        return {
            "question": question,
            "options": options,
            "correct": correct,
            "explication": explanation,
        }

    def submit_diagnostic_answer(
        self, session_id: str, answer: str
    ) -> dict[str, Any]:
        """Submit an answer to the current diagnostic question (T022).

        Checks the answer, tracks score, adjusts difficulty, and either
        returns the next question or the final result.

        Returns a dict with ``session_id``, ``correct``, ``explanation``,
        ``next_question`` (or ``None``), ``is_finished``.
        """
        session = self.store.get_tutoring_session(session_id)
        if session is None:
            raise KeyError(f"Session introuvable : {session_id}")

        state_json = session.transcript_path or "{}"
        state: dict[str, Any] = _extract_json(state_json) if isinstance(state_json, str) else {}
        if not state or state.get("mode") != "diagnostic":
            raise ValueError("La session n'est pas un diagnostic.")

        idx = state.get("current_index", 0)
        concept_names = state.get("concept_names", [])
        correct_answer = state.get("answers", [])
        if isinstance(correct_answer, list) and idx < len(correct_answer):
            stored_correct = correct_answer[idx]
        else:
            # Retrieve from the last generated question state
            stored_correct = state.get("current_correct", "A")

        is_correct = answer.strip().upper() == stored_correct
        if is_correct:
            state["correct_count"] = state.get("correct_count", 0) + 1

        # Record this answer
        if "answers" not in state:
            state["answers"] = []
        state["answers_sent"] = state.get("answers_sent", [])
        state["answers_sent"].append(
            {"answer": answer, "correct": stored_correct, "is_correct": is_correct}
        )

        explanation = state.get("current_explanation", "")

        # Advance to next question or finish
        next_index = idx + 1
        total = state.get("total_questions", self._DIAGNOSTIC_TOTAL_QUESTIONS)

        next_question_data: dict[str, Any] | None = None
        is_finished = next_index >= total

        if not is_finished:
            # Adjust difficulty based on recent performance
            state["current_index"] = next_index
            recent = state["answers_sent"][-3:] if state.get("answers_sent") else []
            recent_correct = sum(1 for a in recent if a.get("is_correct"))
            if recent_correct >= 2:
                # Doing well → increase difficulty
                if state.get("current_level") == "beginner":
                    state["current_level"] = "intermediate"
                elif state.get("current_level") == "intermediate":
                    state["current_level"] = "advanced"
            elif recent_correct == 0 and len(recent) >= 2:
                # Struggling → decrease difficulty
                if state.get("current_level") == "advanced":
                    state["current_level"] = "intermediate"
                elif state.get("current_level") == "intermediate":
                    state["current_level"] = "beginner"

            concept_name = concept_names[next_index] if next_index < len(concept_names) else concept_names[-1]
            question_data = self._generate_diagnostic_question(
                concept_name, state.get("current_level", "intermediate")
            )
            state["current_correct"] = question_data.get("correct", "A")
            state["current_explanation"] = question_data.get("explication", "")
            next_question_data = {
                "question": question_data.get("question", ""),
                "options": question_data.get("options", {}),
                "question_num": next_index + 1,
                "total_questions": total,
            }
        else:
            state["current_index"] = total  # mark as done

        # Persist updated state
        state_json_out = json.dumps(state, ensure_ascii=False)
        self.store._conn.execute(
            "UPDATE tutoring_sessions SET transcript_path = ?, last_active_at = ? WHERE id = ?",
            (state_json_out, _now_iso(), session_id),
        )
        self.store._conn.commit()

        result: dict[str, Any] = {
            "session_id": session_id,
            "correct": is_correct,
            "explanation": explanation,
            "next_question": next_question_data,
            "is_finished": is_finished,
        }
        if is_finished:
            result["result"] = self.get_diagnostic_result(session_id)
        return result

    def get_diagnostic_result(self, session_id: str) -> dict[str, Any]:
        """Compute the final diagnostic result (T023).

        Returns per-concept scores, strengths, weaknesses, and a suggested
        learning path.
        """
        session = self.store.get_tutoring_session(session_id)
        if session is None:
            raise KeyError(f"Session introuvable : {session_id}")

        state_json = session.transcript_path or "{}"
        state: dict[str, Any] = _extract_json(state_json) if isinstance(state_json, str) else {}
        if not state or state.get("mode") != "diagnostic":
            raise ValueError("La session n'est pas un diagnostic.")

        concept_names = state.get("concept_names", [])
        concept_ids = state.get("concept_ids", [])
        answers_sent = state.get("answers_sent", [])
        total = state.get("total_questions", len(concept_names))
        correct_count = state.get("correct_count", 0)
        score_pct = round((correct_count / total) * 100.0, 2) if total > 0 else 0.0

        # Per-concept analysis
        per_concept: list[dict[str, Any]] = []
        concept_correct: dict[str, bool] = {}
        for i, ans in enumerate(answers_sent):
            name = concept_names[i] if i < len(concept_names) else f"concept_{i}"
            concept_correct[name] = ans.get("is_correct", False)
            per_concept.append({
                "concept": name,
                "correct": ans.get("is_correct", False),
            })

        strengths: list[str] = [n for n, c in concept_correct.items() if c]
        weaknesses: list[str] = [n for n, c in concept_correct.items() if not c]

        # Suggested path: focus on weaknesses first
        if weaknesses:
            suggested = (
                f"Concentrez-vous sur : {', '.join(weaknesses)}. "
                f"Score global : {score_pct}%. "
                "Un travail ciblé sur ces concepts est recommandé."
            )
        else:
            suggested = (
                f"Félicitations ! Score global : {score_pct}%. "
                "Vous maîtrisez tous les concepts évalués. "
                "Poursuivez avec des exercices avancés."
            )

        return {
            "session_id": session_id,
            "total_questions": total,
            "correct_count": correct_count,
            "score_pct": score_pct,
            "per_concept": per_concept,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "suggested_path": suggested,
        }

    # ------------------------------------------------------------------
    # US14 — Mode Épreuve (T079-T085): exam document import & resolution
    # ------------------------------------------------------------------

    #: Image extensions handled as OCR stubs (T079).
    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif"}

    def parse_exam_document(self, paths: list[str]) -> str:
        """Extract text from a list of exam document file paths (T079).

        PDFs are processed through the existing ``extract_text`` extractor.
        Images return a raw text placeholder (OCR stub — real OCR is out of
        scope for this task).  All extracted text segments are concatenated
        with double-newline separators.

        Raises ``FileNotFoundError`` when any path does not exist and
        ``ValueError`` for unsupported formats.
        """
        segments: list[str] = []
        for raw_path in paths:
            p = Path(raw_path)
            if not p.exists():
                raise FileNotFoundError(f"No such file: {p}")
            suffix = p.suffix.lower()
            if suffix == ".pdf":
                for text, _meta in extract_text(str(p), fmt="pdf"):
                    if text:
                        segments.append(text)
            elif suffix in self._IMAGE_EXTENSIONS:
                # OCR stub: return a placeholder so downstream pipeline works.
                segments.append(
                    f"[Image OCR non disponible — {p.name}]\n"
                    f"Contenu image de {p.name} non extractible sans OCR."
                )
            elif suffix in {".txt", ".md"}:
                segments.append(p.read_text(encoding="utf-8", errors="replace"))
            else:
                raise ValueError(f"Unsupported exam format: {suffix}")
        return "\n\n".join(segments)

    async def analyze_exam(self, exam_text: str) -> list[dict[str, Any]]:
        """Analyze exam OCR text and extract structured questions (T081).

        Sends the exam text through the LLM using ``build_exam_analysis_prompt``
        and parses the JSON response into a list of question dicts, each
        containing ``number``, ``statement``, ``concepts``, and ``status``.
        """
        messages = build_exam_analysis_prompt(exam_text)
        options = self._generation_options()
        raw = await self._llm_collect(messages, options)
        data = self._parse_exam_json(raw)
        questions: list[dict[str, Any]] = []
        for q in data.get("questions", []):
            questions.append({
                "number": q.get("number", 0),
                "statement": q.get("statement", ""),
                "concepts": q.get("concepts", []),
                "status": q.get("status", "pending"),
            })
        return questions

    @staticmethod
    def _parse_exam_json(raw: str) -> dict[str, Any]:
        """Best-effort extraction of the JSON payload from the LLM response."""
        raw = raw.strip()
        # Fast path: the entire response is valid JSON.
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        # Fallback: locate the first { ... } block in the response.
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(raw[start : end + 1])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        return {"questions": []}

    async def resolve_exam_question(
        self,
        question_statement: str,
        concepts: list[str],
        hint_level: int = 0,
        rag_context: str = "",
    ) -> dict[str, Any]:
        """Resolve an exam question: full answer or progressive hint (T082).

        When ``hint_level`` is 0 the LLM generates a full answer.  For
        ``hint_level`` > 0 it generates a progressive hint (1=gentle nudge,
        2=intermediate guidance, 3=near-complete hint).  ``rag_context``
        optionally carries retrieved passages to ground the response.

        Returns ``{"text": str, "hint_level": int}``.
        """
        hint_level = max(0, min(hint_level, 3))
        messages = build_exam_resolve_prompt(
            question_statement, concepts, hint_level, rag_context
        )
        options = self._generation_options()
        text = await self._llm_collect(messages, options)
        return {"text": text.strip(), "hint_level": hint_level}


