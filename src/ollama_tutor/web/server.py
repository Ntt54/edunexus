"""FastAPI server for the local AI tutor web GUI.

Reuses the UI-agnostic tutor backend:
- OllamaClient (embeddings, generation, health)
- TutorService / LibraryStore / ProgressTracker (see tutor/)
- Config (settings)

Streaming to the browser happens over one WebSocket per client using
NDJSON-style JSON events. The server is a thin transport layer: it never
duplicates indexing/persistence/spaced-repetition logic — it delegates to
the tutor services.

Hardening: uvicorn binds 127.0.0.1 explicitly (see web/__main__.py);
before every WebSocket upgrade and mutating HTTP request the ``Origin`` /
``Host`` headers are validated against a localhost allowlist (any port);
foreign origins are rejected with 403 / connection close.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from pydantic import BaseModel

from ..client import OllamaClient, OllamaConnectionError
from ..config import Config
from ..models import Conversation, Message, MessageRole, OllamaOptions
from ..tutor.assessment import ExamHelpError
from ..tutor.embeddings import NumpyVectorIndex
from ..tutor.prompts import resolve_overrides
from ..tutor.progress import ProgressTracker
from ..tutor.providers.docling_ocr import create_ocr_provider
from ..tutor.providers.gguf_embedding import (
    create_embedding_provider,
    get_default_manager,
)
from ..tutor.providers.hybrid_parser import HybridDocumentParser
from ..tutor.conversations import ConversationService
from ..tutor.service import TutorService
from ..tutor.store import LibraryStore
from ..tutor.voice import VoiceError, WhisperTranscriber
from ..utils.platform import get_config_dir  # re-exported for tests/monkeypatch

STATIC_DIR = Path(__file__).parent / "static"
VUE_DIST_DIR = Path(__file__).parent.parent.parent.parent / "web" / "vue" / "dist"

#: Tool profiles accepted on agent WS frames (specs/003 T019). Omitted,
#: empty or "default" ⇒ legacy full toolset.
VALID_PROFILES = {"plan", "build"}

#: Hostnames allowed in Origin/Host headers (any port). The server only ever
#: binds loopback (web/__main__.py), so non-local authorities are rebinding/
#: CSRF attempts. Extra names may be whitelisted via OLLAMA_WEBGUI_ALLOWED_HOSTS
#: (comma-separated). "testserver" is Starlette's TestClient default Host and
#: is required by the test-suite; it carries no production exposure.
LOCAL_HOST_NAMES = {"localhost", "127.0.0.1", "::1"}
_EXTRA_ALLOWED = {
    h.strip().lower()
    for h in os.environ.get("OLLAMA_WEBGUI_ALLOWED_HOSTS", "").split(",")
    if h.strip()
}
ALLOWED_HOSTS = LOCAL_HOST_NAMES | _EXTRA_ALLOWED | {"testserver"}

#: Mutating HTTP methods guarded by the same-origin check (T024).
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = ""


class ConfigUpdate(BaseModel):
    think: bool | None = None
    verbose: bool | None = None
    last_model: str | None = None
    agent_enabled: bool | None = None
    agent_max_iterations: int | None = None
    agent_max_output_chars: int | None = None
    agent_command_timeout_s: int | None = None
    agent_allowed_root: str | None = None
    agent_auto_approve_commands: bool | None = None
    agent_context_token_budget: int | None = None
    agent_native_tools: bool | None = None


class OptionsUpdate(BaseModel):
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    num_ctx: int | None = None
    num_predict: int | None = None
    repeat_penalty: float | None = None
    seed: int | None = None
    num_batch: int | None = None
    num_thread: int | None = None
    keep_alive: str | int | None = None


class PresetCreate(BaseModel):
    name: str
    model: str = ""
    think: bool = False


class TutorImportRequest(BaseModel):
    subject: str
    path: str | None = None
    fmt: str | None = None
    background: bool = True
    queue: bool = False


class TutorSearchRequest(BaseModel):
    subject: str
    query: str
    k: int = 5


class TutorExerciseRequest(BaseModel):
    concept_id: str
    difficulty: str = "medium"


class TutorAnswerRequest(BaseModel):
    answer: str = ""
    reveal_hint: bool = False


class TutorAnswerAliasRequest(BaseModel):
    exercise_id: str = ""
    answer: str = ""
    reveal_hint: bool = False


class TutorSolutionRequest(BaseModel):
    explicit: bool = False


class TutorSolutionAliasRequest(BaseModel):
    exercise_id: str = ""
    explicit: bool = False


class TutorPathRequest(BaseModel):
    order: list[str] = []


class TutorGradeRequest(BaseModel):
    success: bool = False


class TutorQuizRequest(BaseModel):
    size: int = 5
    kinds: list[str] = ["mcq", "true_false", "open"]


class TutorExamRequest(BaseModel):
    size: int = 10
    time_limit_s: int = 600
    # Phase 5a exam scoping: restrict source material to books belonging to
    # any listed category/corpus (union). Omitted/empty = legacy behavior.
    category_ids: list[int] | None = None
    corpus_ids: list[int] | None = None


class TutorModelsUpdate(BaseModel):
    embedding: str | None = None
    llm: str | None = None


class TutorLabelCreate(BaseModel):
    name: str


class TutorLabelRename(BaseModel):
    name: str


class TutorCategoryMembership(BaseModel):
    category_id: int


class TutorCorpusMembership(BaseModel):
    corpus_id: int


class LogErrorRequest(BaseModel):
    message: str
    stack: str | None = None
    context: str | None = None


class TutorQuizSubmitRequest(BaseModel):
    answers: dict[str, Any] = {}
    hint_requested: bool = False


class TutorConceptRequest(BaseModel):
    name: str
    path_rank: int | None = None
    notion: str = ""


class TutorCompareRequest(BaseModel):
    a: str
    b: str


class TutorProfileRequest(BaseModel):
    domain: str = ""
    level: str = ""
    objective: str = ""
    deadline: str = ""
    available_time: str = ""
    prerequisites: list[str] = []
    competencies: list[str] = []
    explanation_style: str = ""
    activities: list[str] = []
    mastery_criteria: list[str] = []
    constraints: dict[str, Any] = {}
    template_id: str = ""


class TutorGoalRequest(BaseModel):
    goal: str


class TutorLearnerRequest(BaseModel):
    name: str
    avatar: str = ""


class TutorNotebookNoteRequest(BaseModel):
    note: str


class TutorNotebookActionRequest(BaseModel):
    action: str
    params: dict[str, Any] = {}


class TutorAutoClassifyRequest(BaseModel):
    batch_size: int = 25


class ProjectCreate(BaseModel):
    name: str
    root: str


class ExamImportRequest(BaseModel):
    paths: list[str]


class ExamAnalyzeRequest(BaseModel):
    exam_text: str


class ExamResolveRequest(BaseModel):
    question_statement: str
    concepts: list[str] = []
    hint_level: int = 0
    rag_context: str = ""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def resolve_mode(data: dict[str, Any], config: Any) -> str:
    """Resolve the requested run mode from a WS ``chat`` frame (T007).

    Pure function. Precedence:
    1. explicit ``data["mode"]`` ("chat"/"agent") wins;
    2. legacy ``data["agent"]`` bool maps to the mode (D6 compat);
    3. fallback to the config visibility default (``agent.enabled``).

    Note (T018): the config default is only consulted when the client gives
    no explicit signal — an explicit ``mode:"chat"`` never depends on config.
    """
    mode = data.get("mode")
    if isinstance(mode, str) and mode.strip().lower() in {"chat", "agent"}:
        return mode.strip().lower()
    legacy = data.get("agent")
    if isinstance(legacy, bool):
        return "agent" if legacy else "chat"
    return "agent" if bool(getattr(config, "agent_enabled", False)) else "chat"


def _status_str(value: Any) -> str | None:
    """Normalize enum-or-string statuses to their plain string value."""
    if value is None:
        return None
    v = getattr(value, "value", value)
    return v if isinstance(v, str) else None


#: Wire vocabulary for index-status book rows (Phase 6 UX contract):
#: status ∈ "indexing" | "ready" | "error". Store statuses map onto it
#: ("indexed" → "ready", legacy "pending" → "indexing").
_BOOK_STATUS_WIRE = {
    "indexed": "ready",
    "pending": "indexing",
}


def _norm_book_status(status: str | None) -> str:
    """Map a store book status onto the wire vocabulary above."""
    s = status or "indexing"
    return _BOOK_STATUS_WIRE.get(s, s)


def _agent_step_to_dict(step: Any) -> dict[str, Any]:
    """Serialize an AgentStep for the ``agent_attached`` frame (002 contract)."""
    tool_call = None
    if step.tool_call is not None:
        tool_call = {
            "name": step.tool_call.name,
            "args": step.tool_call.args,
            "truncated_result": step.tool_call.truncated_result,
            "duration_ms": step.tool_call.duration_ms,
            "status": _status_str(step.tool_call.status),
        }
    return {
        "index": step.index,
        "type": _status_str(step.type),
        "content": step.content,
        "duration_ms": step.duration_ms,
        "status": _status_str(step.status),
        "tool_call": tool_call,
    }


def _authority_host(authority: str | None) -> str | None:
    """Bare lowercase hostname from a Host/Origin authority (IPv6-aware)."""
    if not authority:
        return None
    try:
        host = urlparse(f"//{authority.strip()}").hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().rstrip(".")


def _origin_allowed(origin: str | None) -> bool:
    """True when an Origin URL points at a loopback host (any port)."""
    if not origin:
        return False
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return (host or "").lower().rstrip(".") in LOCAL_HOST_NAMES


def _host_header_allowed(host_header: str | None) -> bool:
    """True when a Host header authority is on the allowlist."""
    return _authority_host(host_header) in ALLOWED_HOSTS


def _log_error(
    config: Any, source: str, message: str, detail: str = ""
) -> None:
    """Append ``[ISO-8601] [source] message`` (+ detail) to errors.log (T012).

    Writes to ``config.config_dir / "errors.log"`` (utf-8 append, lazy file
    creation). Never raises — logging must not kill a request/stream.

    Dual write (Polish A): also appends to ``get_config_dir() / "errors.log"``
    and to ``data/errors.log`` (project-local fallback) so errors are visible
    both from the global config dir and from the repository data dir.
    """
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] [{source}] {message}\n"
    if detail:
        line += f"{detail}\n"

    # 1) primary: config.config_dir / errors.log
    try:
        log_file = Path(config.config_dir) / "errors.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

    # 2) secondary: global get_config_dir() / errors.log
    try:
        global_log = get_config_dir() / "errors.log"
        # avoid double write when paths are identical
        try:
            same = Path(global_log).resolve() == Path(config.config_dir).resolve() / "errors.log"
        except Exception:
            same = str(global_log) == str(Path(config.config_dir) / "errors.log")
        if not same:
            global_log.parent.mkdir(parents=True, exist_ok=True)
            with global_log.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass

    # 3) tertiary: data/errors.log (project-local, relative to cwd and to config_dir)
    for candidate in (Path("data") / "errors.log", Path(config.config_dir) / "data" / "errors.log"):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def _save_data_url(data_dir: Path, data_url: str) -> Path | None:
    """Persist a base64 data-URL image to disk; returns the path or None."""
    try:
        header, b64 = data_url.split(",", 1)
        ext = ".png"
        if "image/jpeg" in header:
            ext = ".jpg"
        elif "image/webp" in header:
            ext = ".webp"
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{uuid.uuid4().hex[:12]}{ext}"
    path.write_bytes(raw)
    return path


def _conversation_to_dict(conv: Conversation) -> dict[str, Any]:
    return {
        "id": conv.id,
        "title": conv.title,
        "model": conv.model,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "messages": [
            {
                "role": m.role.value,
                "content": m.content,
                "images": m.images,
                "timestamp": m.timestamp,
                "thinking": m.thinking,
            }
            for m in conv.messages
        ],
    }


class ConversationCreate(BaseModel):
    title: str = ""
    subject_id: str


class ConversationRename(BaseModel):
    title: str


class ConversationSourcesPayload(BaseModel):
    book_ids: list[str] = []


class SettingsUpdate(BaseModel):
    """Mise à jour partielle des réglages utilisateur (005-platform-ui-library)."""

    options: dict[str, Any] | None = None
    think: bool | None = None
    socratic: bool | None = None
    level: str | None = None
    top_k: int | None = None
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    embed_batch_size: int | None = None
    max_parallel_embed: int | None = None
    nightly_enabled: bool | None = None
    nightly_start_at: str | None = None
    nightly_stop_at: str | None = None
    nightly_only_on_ac: bool | None = None
    nightly_max_runtime_minutes: int | None = None
    nightly_prepare_enabled: bool | None = None


def create_app(config_dir: Path | None = None) -> FastAPI:
    """Build the web GUI application."""
    app = FastAPI(title="EduNexus")

    # Feature 008 — WS event broadcast registry (T063). Connected /ws/tutor
    # sockets register here so REST endpoints can emit feature events
    # (profile_updated, graph_built, path_generated, program_status,
    # photo_status, notebook_output) to live clients.
    app.state.tutor_ws_clients: set[WebSocket] = set()

    async def _emit_tutor_event(event: str, **payload: Any) -> None:
        """Broadcast a feature event to all connected /ws/tutor clients."""
        frame = {"type": event, **payload}
        dead: list[WebSocket] = []
        for ws in list(app.state.tutor_ws_clients):
            try:
                await ws.send_json(frame)
            except Exception:
                dead.append(ws)
        for ws in dead:
            app.state.tutor_ws_clients.discard(ws)

    config = Config(config_dir=config_dir) if config_dir else Config()
    client = OllamaClient()
    # In production config.config_dir IS get_config_dir() (~/.config/ollama-tui);
    # passing the resolved dir keeps library state next to config.json and
    # lets tests isolate everything under a tmp config_dir.
    # ONE LibraryStore + TutorService per server (004-local-ai-tutor, US1).
    # The tutor service gets its OWN client instance: its embedding calls run
    # inside daemon threads (each with its own event loop), so sharing the
    # app's chat client would cross event-loop boundaries.
    tutor_store = LibraryStore(config.config_dir)
    # B1 multi-fournisseur : choisir le client LLM selon la config
    if config.llm_provider == "openai" and config.llm_base_url:
        from ..tutor.providers.openai_compat import OpenAICompatProvider
        tutor_client = OpenAICompatProvider(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key or None,
        )
    else:
        tutor_client = OllamaClient()
    # Phase 5a provider wiring: the GGUF embedding provider is built ONLY
    # when llama.bin + embed GGUF are configured (explicit local engine).
    # Unconfigured ⇒ None keeps the legacy Ollama path through tutor_client
    # byte-identical (and preserves the client injection seam for tests).
    # The hybrid document parser is built only when the Docling OCR stack is
    # configured. Construction is lazy — no subprocess/network happens here.
    if config.tutor_llama_bin and config.tutor_embed_gguf:
        embedding_provider = create_embedding_provider(config)
    else:
        embedding_provider = None
    ocr_provider = create_ocr_provider(config)
    document_parser = (
        HybridDocumentParser(
            ocr_provider,
            text_threshold=config.tutor_ocr_text_threshold,
            dpi=config.tutor_ocr_dpi,
            pdftoppm_bin=config.tutor_pdftoppm_bin,
        )
        if ocr_provider is not None
        else None
    )
    tutor_service = TutorService(
        tutor_store,
        tutor_client,
        config,
        embedding_provider=embedding_provider,
        document_parser=document_parser,
    )
    # Any ``indexing`` row left by a killed process becomes safely resumable.
    tutor_service.recover_interrupted_indexing()
    # Mastery / gap / path tracker (US4 practice surface).
    tutor_progress = ProgressTracker(tutor_store)
    # Conversations nommées (005-platform-ui-library).
    conversations = ConversationService(tutor_store)

    @app.on_event("startup")
    async def _startup() -> None:
        if config.tutor_nightly_enabled:
            await tutor_service.start_nightly_scheduler()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        try:
            await tutor_service.stop_nightly_scheduler()
        except Exception:
            pass
        # Pause the single persistent queue before tearing down its clients.
        try:
            await asyncio.wait_for(tutor_service.stop_index_queue(), timeout=5)
        except Exception:
            pass
        # Let legacy per-import background indexing finish (bounded) BEFORE
        # tearing down the clients those tasks use.
        pending = [
            t
            for t in getattr(app.state, "tutor_index_tasks", {}).values()
            if not t.done()
        ]
        if pending:
            try:
                await asyncio.wait(pending, timeout=5)
            except Exception:
                pass
        await client.close()
        # Free provider RAM on exit: close provider HTTP clients, then stop
        # any llama-server processes owned by the shared manager.
        for provider in (embedding_provider, ocr_provider):
            if provider is not None and hasattr(provider, "aclose"):
                try:
                    await provider.aclose()
                except Exception:
                    pass
        try:
            await get_default_manager().stop_all()
        except Exception:
            pass
        config.save()

    # ------------------------------------------------------------------
    # Hardening: log + mask unexpected errors (Phase 5a).
    # ------------------------------------------------------------------

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        _log_error(
            config,
            "http",
            f"{request.method} {request.url.path}: {exc}",
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Erreur interne (journalisée dans errors.log)"},
        )

    # ------------------------------------------------------------------
    # Hardening: same-origin guard for mutating HTTP requests and WS upgrades.
    # ------------------------------------------------------------------

    @app.middleware("http")
    async def _same_origin_guard(request: Request, call_next: Any) -> Any:
        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and not _origin_allowed(origin):
                return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
            if not _host_header_allowed(request.headers.get("host")):
                return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        return await call_next(request)

    # ------------------------------------------------------------------
    # Static frontend: / redirects to the tutor UI
    # ------------------------------------------------------------------

    @app.get("/")
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse("/tutor")

    @app.get("/tutor")
    async def tutor_view() -> FileResponse:
        """Serve the new Vue SPA as the default tutor UI."""
        if not config.tutor_enabled:
            raise HTTPException(
                status_code=404,
                detail="Vue tuteur désactivée — active tutor.enabled dans la config",
            )
        index_path = VUE_DIST_DIR / "index.html"
        if not index_path.exists():
            # Fallback to legacy single-file HTML if Vue build not present
            return FileResponse(STATIC_DIR / "tutor.html")
        return FileResponse(index_path)

    @app.get("/tutor-classic")
    async def tutor_classic_view() -> FileResponse:
        """Serve the legacy single-file HTML tutor UI."""
        if not config.tutor_enabled:
            raise HTTPException(
                status_code=404,
                detail="Vue tuteur désactivée — active tutor.enabled dans la config",
            )
        return FileResponse(STATIC_DIR / "tutor.html")

    @app.get("/tutor/assets/{asset_path:path}")
    async def tutor_vue_assets(asset_path: str) -> FileResponse:
        """Serve static assets (JS, CSS) from the Vue build."""
        file_path = VUE_DIST_DIR / "assets" / asset_path
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(file_path)

    @app.post("/api/tutor/import")
    async def tutor_import(request: Request) -> dict[str, Any]:
        ctype = request.headers.get("content-type", "")
        subject: str | None = None
        fmt: str | None = None
        path: str | None = None
        queue_requested = False
        if "multipart/form-data" in ctype:
            form = await request.form()
            subject = form.get("subject")
            fmt = form.get("fmt")
            upload = form.get("file")
            queue_requested = str(form.get("queue", "")).lower() in {"1", "true", "yes", "on"}
            if upload is None:
                _log_error(config, "tutor-import", "import sans fichier")
                raise HTTPException(status_code=400, detail="missing file")
            uploads_dir = config.config_dir / "tutor" / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            raw_filename = str(getattr(upload, "filename", "") or "")
            # Multipart filenames are client-controlled. Normalize both POSIX
            # and Windows separators, then keep only the final component so a
            # value such as ../../outside.pdf cannot escape uploads_dir.
            safe_filename = Path(raw_filename.replace("\\", "/")).name
            if not safe_filename or safe_filename in {".", ".."}:
                _log_error(config, "tutor-import", f"nom de fichier invalide: {raw_filename!r}")
                raise HTTPException(status_code=400, detail="invalid file name")
            dest = uploads_dir / safe_filename
            if dest.exists():
                dest = uploads_dir / (
                    f"{dest.stem}-{uuid.uuid4().hex[:8]}{dest.suffix}"
                )
            dest.write_bytes(await upload.read())
            path = str(dest)
        else:
            data = await request.json()
            subject = data.get("subject")
            path = data.get("path")
            fmt = data.get("fmt")
            queue_requested = bool(data.get("queue", False))
        if not subject or not path:
            _log_error(config, "tutor-import", "subject and path required")
            raise HTTPException(status_code=400, detail="subject and path required")

        # Pre-flight (Phase 6 UX): register the book row synchronously so
        # pre-flight failures (unreadable file, unsupported format) still
        # answer 4xx IMMEDIATELY.
        try:
            subject_id, book = tutor_service.register_import(str(subject), path)
        except FileNotFoundError as exc:
            _log_error(config, "tutor-import", f"Fichier introuvable: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail="Fichier introuvable") from exc
        except ValueError as exc:
            _log_error(config, "tutor-import", f"Format invalide: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail="Formats acceptés .txt,.md,.pdf,.epub") from exc
        except sqlite3.IntegrityError as exc:
            _log_error(config, "tutor-import", f"Intégrité fingerprint: {exc}", traceback.format_exc())
            # Reuse existing book via subject_books instead of 500 (fingerprint UNIQUE)
            try:
                p = Path(str(path))
                suffix = p.suffix.lower()
                if suffix in (".txt", ".md"):
                    try:
                        raw = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        raw = ""
                else:
                    try:
                        raw = p.read_bytes().hex()
                    except Exception:
                        raw = ""
                fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None
                row = None
                if fingerprint:
                    row = tutor_store._conn.execute(
                        "SELECT * FROM books WHERE fingerprint = ?", (fingerprint,)
                    ).fetchone()
                if row is None:
                    # fallback: try by source_path
                    row = tutor_store._conn.execute(
                        "SELECT * FROM books WHERE source_path = ?", (str(path),)
                    ).fetchone()
                if row is not None:
                    from ..tutor.models import Book as _Book

                    existing_book = _Book.from_dict(dict(row))
                    # resolve subject again (register_import already created it)
                    try:
                        sid = tutor_service._resolve_subject(str(subject))
                    except Exception:
                        sid = None
                    if sid is not None:
                        try:
                            tutor_store._conn.execute(
                                "INSERT OR IGNORE INTO subject_books (subject_id, book_id) VALUES (?, ?)",
                                (sid, existing_book.id),
                            )
                            tutor_store._conn.commit()
                        except Exception:
                            pass
                        subject_id, book = sid, existing_book
                    else:
                        raise HTTPException(status_code=400, detail="Fichier déjà importé") from exc
                else:
                    raise HTTPException(status_code=400, detail="Fichier déjà importé") from exc
            except HTTPException:
                raise
            except Exception as e2:
                _log_error(config, "tutor-import", f"reuse fingerprint échec: {e2}", traceback.format_exc())
                raise HTTPException(status_code=400, detail="Fichier déjà importé") from exc

        # Fingerprint dedup: re-importing an already-indexed book is a no-op.
        status = tutor_store.get_book_status(book.id)
        if status == "indexed":
            return {"book_id": book.id, "status": "ready"}

        if queue_requested:
            # Persistent queue mode leaves the row pending and lets the single
            # worker claim it in creation order. Starting here means several
            # rapid imports are accepted without spawning one task per book.
            await tutor_service.start_index_queue()
            return {"book_id": book.id, "status": "pending", "queued": True}

        tasks: dict[str, asyncio.Task] = getattr(
            app.state, "tutor_index_tasks", None
        )
        if tasks is None:
            tasks = app.state.tutor_index_tasks = {}
        live = tasks.get(book.id)
        if live is not None and not live.done():
            # This book is already being indexed by a previous import.
            return {"book_id": book.id, "status": "indexing"}

        # Flip the row to "indexing" BEFORE answering so index-status polling
        # sees it immediately; heavy work continues in a background task.
        tutor_store.mark_indexing(book.id)

        async def _index_run() -> None:
            inner = tutor_service.schedule_index(subject_id, book, path, fmt)
            try:
                await inner
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — never raise unhandled
                tutor_store.set_book_error(
                    book.id, f"{type(exc).__name__}: {exc}"
                )
                _log_error(
                    config,
                    "tutor-index",
                    f"Indexation impossible pour « {book.title} » : {exc}",
                    traceback.format_exc(),
                )
                return
            # _run_index records failures on the book row itself; surface
            # them in errors.log here (post-response failure reporting).
            if tutor_store.get_book_status(book.id) == "error":
                row = tutor_store.get_book(book.id)
                reason = getattr(row, "error", "") or "raison inconnue"
                _log_error(
                    config,
                    "tutor-index",
                    f"Indexation impossible pour « {book.title} » : {reason}",
                )

        run = asyncio.create_task(_index_run())
        # Strong reference on app.state so the task is not garbage-collected
        # mid-flight (asyncio only keeps weak refs).
        tasks[book.id] = run

        def _done(t: asyncio.Task, *, book_id: str = book.id) -> None:
            tasks.pop(book_id, None)

        run.add_done_callback(_done)
        return {"book_id": book.id, "status": "indexing"}

    @app.get("/api/tutor/books")
    async def tutor_books(subject: str | None = None) -> dict[str, Any]:
        if subject:
            subj = next(
                (s for s in tutor_store.list_subjects() if s.name.lower() == subject.lower()),
                None,
            )
            if subj is None:
                raise HTTPException(status_code=404, detail="unknown subject")
            books = tutor_store.list_books(subj.id)
        else:
            books = tutor_store.list_all_books()
        return {"books": [b.to_dict() for b in books]}

    @app.delete("/api/tutor/book/{book_id}")
    async def tutor_delete_book(book_id: str) -> Response:
        tutor_store.delete_book(book_id)
        return Response(status_code=204)

    @app.post("/api/tutor/search")
    async def tutor_search(payload: TutorSearchRequest) -> dict[str, Any]:
        if not payload.query or not payload.query.strip():
            _log_error(config, "tutor-search", "query requis")
            raise HTTPException(status_code=400, detail="query requis")
        if payload.k is None or not isinstance(payload.k, int) or payload.k < 1 or payload.k > 50:
            _log_error(config, "tutor-search", f"k invalide: {payload.k}")
            raise HTTPException(status_code=400, detail="k doit être entre 1 et 50")
        subj = next(
            (s for s in tutor_store.list_subjects() if s.name.lower() == payload.subject.lower()),
            None,
        )
        if subj is None:
            raise HTTPException(status_code=404, detail="unknown subject")
        vectors = await client.embed(tutor_service.model, [payload.query])
        if not vectors:
            return {"results": []}
        import numpy as np

        rows = tutor_store.get_indexed_chunks(
            subj.id, model=tutor_service.model
        )
        idx = NumpyVectorIndex()
        items = []
        meta: dict[str, Any] = {}
        for r in rows:
            if r["embedding"]:
                vec = np.frombuffer(r["embedding"], dtype=np.float32).tolist()
                items.append((r["id"], vec))
                meta[r["id"]] = r
        idx.add(items)
        scored = idx.search(vectors[0], payload.k)
        results = []
        for cid, score in scored:
            r = meta[cid]
            results.append({
                "id": cid,
                "book_id": r["book_id"],
                "text": r["text"],
                "chapter": r["chapter"],
                "section": r["section"],
                "page": r["page"],
                "score": score,
            })
        return {"results": results}

    @app.get("/api/tutor/index-queue")
    async def tutor_index_queue_status() -> dict[str, Any]:
        return tutor_service.index_queue_status()

    @app.post("/api/tutor/index-queue/start")
    async def tutor_index_queue_start(retry_errors: bool = False) -> dict[str, Any]:
        return await tutor_service.start_index_queue(retry_errors=retry_errors)

    @app.post("/api/tutor/index-queue/stop")
    async def tutor_index_queue_stop() -> dict[str, Any]:
        return await tutor_service.stop_index_queue()

    @app.get("/api/tutor/nightly")
    async def tutor_nightly_status() -> dict[str, Any]:
        return tutor_service.nightly_status()

    @app.post("/api/tutor/nightly/start")
    async def tutor_nightly_start() -> dict[str, Any]:
        config.tutor_nightly_enabled = True
        config.save()
        return await tutor_service.start_nightly_scheduler()

    @app.post("/api/tutor/nightly/stop")
    async def tutor_nightly_stop() -> dict[str, Any]:
        config.tutor_nightly_enabled = False
        config.save()
        return await tutor_service.stop_nightly_scheduler()

    @app.post("/api/tutor/maintenance")
    async def tutor_maintenance(request: Request) -> dict[str, Any]:
        body = await request.json()
        return tutor_service.run_maintenance(
            vacuum=bool(body.get("vacuum", False)),
            backup=bool(body.get("backup", True)),
        )

    @app.post("/api/tutor/books/{book_id}/retry")
    async def tutor_retry_book(book_id: str) -> dict[str, Any]:
        book = tutor_store.get_book(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="livre inconnu")
        if book.status != "error":
            raise HTTPException(status_code=409, detail="le livre n'est pas en erreur")
        tutor_store.retry_book(book_id)
        await tutor_service.start_index_queue()
        return {"book_id": book_id, "status": "pending"}

    @app.post("/api/tutor/books/{book_id}/cancel")
    async def tutor_index_queue_cancel(book_id: str) -> dict[str, Any]:
        try:
            return await tutor_service.cancel_queued_book(book_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="livre inconnu") from exc

    @app.get("/api/tutor/pgvector/status")
    async def tutor_pgvector_status() -> dict[str, Any]:
        """Health check for pgvector backend (migration plan step 8)."""
        enabled = bool(getattr(config, "pgvector_enabled", False))
        dsn = getattr(config, "pgvector_dsn", "")
        if not enabled:
            return {"enabled": False, "ok": False, "dsn": dsn, "detail": "pgvector désactivé (SQLite par défaut)"}
        tmp = None
        conn = None
        try:
            # Try connection via LibraryStore helper
            from ..tutor.store import LibraryStore

            tmp = LibraryStore(config.config_dir, pgvector_enabled=True, pgvector_dsn=dsn)
            conn = tmp.get_pg_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                ok = True
                detail = "pgvector opérationnel"
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001
            ok = False
            detail = str(exc)[:300]
        finally:
            if tmp is not None:
                try:
                    # Prefer public close() API; fallback to _conn.close()
                    if hasattr(tmp, "close"):
                        tmp.close()
                    else:
                        tmp._conn.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
                # Also ensure leaking pg conn is closed if exception before assignment
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        return {"enabled": enabled, "ok": ok, "dsn": dsn, "detail": detail}

    @app.get("/api/tutor/index-status")
    async def tutor_index_status() -> dict[str, Any]:
        """Indexing progress for the UI poller (Phase 6 UX contract).

        ``books`` rows are light ``{id, title, status}`` dicts with the wire
        status vocabulary "indexing" | "ready" | "error"; the ``indexing``
        boolean (any book currently indexing) is preserved.
        """
        books = tutor_store.list_all_books()
        rows = [
            {"id": b.id, "title": b.title, "status": _norm_book_status(b.status)}
            for b in books
        ]
        indexing = any(b.status == "indexing" for b in books)
        return {"books": rows, "indexing": indexing}

    @app.get("/api/tutor/subjects")
    async def tutor_subjects() -> dict[str, Any]:
        """List subjects (id + name) for the chat subject switcher (US2)."""
        subjects = tutor_store.list_subjects()
        active = tutor_store.active_subject()
        return {
            "subjects": [{"id": s.id, "name": s.name} for s in subjects],
            "active_id": active.id if active else None,
        }

    # ------------------------------------------------------------------
    # Feature 008 — Profil pédagogique (US1) — thin transport only
    # ------------------------------------------------------------------

    @app.get("/api/tutor/pedagogical-templates")
    async def tutor_pedagogical_templates() -> dict[str, Any]:
        """List predefined pedagogical templates (FR-003)."""
        from ..tutor.profiles import ProfileService
        svc = ProfileService(tutor_store)
        return {"templates": [t.to_dict() for t in svc.list_templates()]}

    @app.get("/api/tutor/subjects/{subject_id}/profile")
    async def tutor_get_profile(subject_id: str) -> dict[str, Any]:
        """Get the pedagogical profile of a subject (FR-005)."""
        from ..tutor.profiles import ProfileService
        svc = ProfileService(tutor_store)
        profile = svc.get_profile(subject_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profil non défini")
        return {"profile": profile.to_dict()}

    @app.put("/api/tutor/subjects/{subject_id}/profile")
    async def tutor_put_profile(subject_id: str, payload: TutorProfileRequest) -> dict[str, Any]:
        """Save the pedagogical profile of a subject (FR-001/FR-005)."""
        from ..tutor.models import SubjectProfile
        from ..tutor.profiles import ProfileService
        svc = ProfileService(tutor_store)
        profile = SubjectProfile(
            subject_id=subject_id,
            domain=payload.domain,
            level=payload.level,
            objective=payload.objective,
            deadline=payload.deadline,
            available_time=payload.available_time,
            prerequisites=payload.prerequisites,
            competencies=payload.competencies,
            explanation_style=payload.explanation_style,
            activities=payload.activities,
            mastery_criteria=payload.mastery_criteria,
            constraints=payload.constraints,
            template_id=payload.template_id,
        )
        try:
            svc.save_profile(profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await _emit_tutor_event("profile_updated", subject_id=subject_id)
        return {"profile": profile.to_dict()}

    @app.post("/api/tutor/subjects/{subject_id}/profile/interpret-goal")
    async def tutor_interpret_goal(subject_id: str, payload: TutorGoalRequest) -> dict[str, Any]:
        """Convert a plain-language goal into pedagogical parameters (FR-004)."""
        from ..tutor.profiles import ProfileService
        svc = ProfileService(tutor_store)
        if not payload.goal.strip():
            raise HTTPException(status_code=400, detail="objectif requis")
        return {"parameters": svc.interpret_goal(payload.goal)}

    # ------------------------------------------------------------------
    # Feature 008 — Graphe de compétences (US2) — thin transport only
    # ------------------------------------------------------------------

    @app.get("/api/tutor/subjects/{subject_id}/graph")
    async def tutor_get_graph(subject_id: str) -> dict[str, Any]:
        """Get the competency graph of a subject (FR-007)."""
        from ..tutor.graph import GraphBuilder
        return GraphBuilder(tutor_store).get_graph(subject_id)

    @app.post("/api/tutor/subjects/{subject_id}/graph/build")
    async def tutor_build_graph(subject_id: str) -> dict[str, Any]:
        """Build/refresh the graph from imported books (FR-007..FR-009)."""
        from ..tutor.graph import GraphBuilder
        result = GraphBuilder(tutor_store).build(subject_id)
        await _emit_tutor_event("graph_built", subject_id=subject_id, **result)
        return result

    @app.post("/api/tutor/graph/nodes/{node_id}/validate")
    async def tutor_validate_node(node_id: str) -> dict[str, Any]:
        """Mark a node as user-confirmed (FR-010)."""
        from ..tutor.graph import GraphBuilder
        return GraphBuilder(tutor_store).validate_node(node_id)

    @app.get("/api/tutor/subjects/{subject_id}/graph/dashboard")
    async def tutor_graph_dashboard(subject_id: str) -> dict[str, Any]:
        """Dashboard aggregation: covered/uncovered/contradictory/unconfirmed (US5)."""
        from ..tutor.graph import GraphBuilder
        return GraphBuilder(tutor_store).dashboard(subject_id)

    # ------------------------------------------------------------------
    # Feature 008 — Parcours explicable (US3) — thin transport only
    # ------------------------------------------------------------------

    @app.post("/api/tutor/subjects/{subject_id}/path/generate")
    async def tutor_generate_path(subject_id: str) -> dict[str, Any]:
        """Generate an explainable learning path from graph + profile (FR-013)."""
        from ..tutor.path_builder import PathBuilder
        result = PathBuilder(tutor_store).generate(subject_id)
        await _emit_tutor_event("path_generated", subject_id=subject_id, **result)
        return result

    @app.put("/api/tutor/subjects/{subject_id}/path")
    async def tutor_reorder_path(subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Reorder / exclude path steps (FR-014)."""
        from ..tutor.path_builder import PathBuilder
        return PathBuilder(tutor_store).reorder(subject_id, payload.get("steps", []))

    # ------------------------------------------------------------------
    # Feature 008 — Adaptation locale (US4) — thin transport only
    # ------------------------------------------------------------------

    @app.get("/api/tutor/subjects/{subject_id}/adaptation/stability")
    async def tutor_stability_portion(subject_id: str) -> dict[str, Any]:
        """Stability portion: objectives, main notion, success criterion (FR-019)."""
        from ..tutor.adaptation import AdaptationService
        return AdaptationService(tutor_store).stability_portion(subject_id)

    @app.post("/api/tutor/subjects/{subject_id}/adaptation/recompute")
    async def tutor_recompute_window(subject_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Recompute only a window of path steps (FR-016)."""
        from ..tutor.adaptation import AdaptationService
        anchor = (payload or {}).get("anchor_step_id")
        return AdaptationService(tutor_store).recompute_window(subject_id, anchor)

    # ------------------------------------------------------------------
    # Feature 008 — Capture de programme par OCR (US6) — thin transport
    # ------------------------------------------------------------------

    @app.post("/api/tutor/subjects/{subject_id}/program/capture")
    async def tutor_capture_program(subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Start a program capture from a photo/PDF path (FR-023)."""
        from ..tutor.program_capture import ProgramCaptureService
        svc = ProgramCaptureService(tutor_store, tutor_service.document_parser)
        result = await svc.capture(
            subject_id,
            str(payload.get("path", "")),
            source_type=str(payload.get("source_type", "photo")),
        )
        await _emit_tutor_event(
            "program_status",
            subject_id=subject_id,
            status=result.get("program", {}).get("status", "processing"),
        )
        return result

    @app.get("/api/tutor/subjects/{subject_id}/program/{program_id}")
    async def tutor_get_program(subject_id: str, program_id: str) -> dict[str, Any]:
        """Get a captured program with its node tree (FR-024)."""
        from ..tutor.program_capture import ProgramCaptureService
        return ProgramCaptureService(tutor_store).get(program_id)

    @app.put("/api/tutor/subjects/{subject_id}/program/{program_id}/nodes/{node_id}")
    async def tutor_correct_program_node(subject_id: str, program_id: str,
                                         node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Correct an OCR node before path generation (FR-027)."""
        from ..tutor.program_capture import ProgramCaptureService
        return ProgramCaptureService(tutor_store).correct_node(
            node_id, str(payload.get("title", "")))

    @app.post("/api/tutor/subjects/{subject_id}/program/{program_id}/confirm")
    async def tutor_confirm_program(subject_id: str, program_id: str) -> dict[str, Any]:
        """Confirm the whole captured program (FR-026)."""
        from ..tutor.program_capture import ProgramCaptureService
        return ProgramCaptureService(tutor_store).confirm(program_id)

    # ------------------------------------------------------------------
    # REST: tutor practice (004-local-ai-tutor, US4) — thin transport only
    # Routes follow contracts/tutor-rest-api.md (subject/exercise-scoped) and
    # also expose the simplified aliases enumerated in task T035.
    # ------------------------------------------------------------------

    def _resolve_tutor_subject(subject_id: str | None) -> str:
        """Resolve a subject id or fall back to the active/first subject."""
        if subject_id:
            return subject_id
        subj = tutor_store.active_subject()
        if subj is None:
            subjects = tutor_store.list_subjects()
            if not subjects:
                _log_error(config, "tutor-resolve", "aucun sujet")
                raise HTTPException(status_code=400, detail="aucun sujet")
            subj = subjects[0]
        return subj.id

    @app.get("/api/tutor/subjects/{subject_id}/concepts")
    async def tutor_list_concepts(subject_id: str) -> dict[str, Any]:
        """List concepts of a subject (powers the practice concept picker)."""
        concepts = tutor_store.list_concepts(subject_id)
        return {"concepts": [c.to_dict() for c in concepts]}

    @app.post("/api/tutor/subjects/{subject_id}/concepts")
    async def tutor_create_concept(subject_id: str, payload: TutorConceptRequest) -> dict[str, Any]:
        """Create/upsert a concept (so the practice view can target notions)."""
        if not payload.name.strip():
            _log_error(config, "tutor-concept", "nom de concept requis")
            raise HTTPException(status_code=400, detail="nom de concept requis")
        try:
            concept = tutor_store.upsert_concept(subject_id, payload.name.strip(), payload.path_rank)
        except KeyError as exc:
            _log_error(config, "tutor-concept", f"concept sujet introuvable: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail="sujet introuvable") from exc
        except ValueError as exc:
            _log_error(config, "tutor-concept", f"concept invalide: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return concept.to_dict()

    @app.post("/api/tutor/exercises")
    async def tutor_create_exercise(payload: TutorExerciseRequest) -> dict[str, Any]:
        """Generate an exercise (no solution field — INVARIANT 3)."""
        if not payload.concept_id or not payload.concept_id.strip():
            _log_error(config, "tutor-exercise", "concept_id requis")
            raise HTTPException(status_code=400, detail="concept_id requis")
        try:
            ex = await tutor_service.generate_exercise(payload.concept_id, payload.difficulty)
        except KeyError as exc:
            _log_error(config, "tutor-exercise", f"concept introuvable: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail="concept introuvable") from exc
        except ValueError as exc:
            _log_error(config, "tutor-exercise", f"exercise invalide: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Never leak the solution through grading/generation (INVARIANT 3).
        return {
            "id": ex.id,
            "subject_id": ex.subject_id,
            "concept_id": ex.concept_id,
            "difficulty": ex.difficulty,
            "statement": ex.statement,
            "hint_level": ex.hint_level,
            "hints": ex.hints,
            "status": ex.status,
        }

    async def _do_grade(exercise_id: str, answer: str, reveal_hint: bool) -> dict[str, Any]:
        if not exercise_id or not exercise_id.strip():
            _log_error(config, "tutor-grade", "exercise_id requis")
            raise HTTPException(status_code=400, detail="exercise_id requis")
        try:
            result = await tutor_service.grade_answer(
                exercise_id, answer, reveal_hint=reveal_hint
            )
        except KeyError as exc:
            _log_error(config, "tutor-grade", f"exercice introuvable: {exc}", traceback.format_exc())
            raise HTTPException(status_code=400, detail="exercice introuvable") from exc
        return {
            "verdict": result.verdict,
            "feedback": result.feedback,
            "hint_level": result.hint_level,
            "hint": result.hint,
        }

    @app.post("/api/tutor/exercises/{exercise_id}/answers")
    async def tutor_grade_answer(exercise_id: str, payload: TutorAnswerRequest) -> dict[str, Any]:
        return await _do_grade(exercise_id, payload.answer, payload.reveal_hint)

    @app.post("/api/tutor/answers")
    async def tutor_grade_answer_alias(payload: TutorAnswerAliasRequest) -> dict[str, Any]:
        if not payload.exercise_id:
            _log_error(config, "tutor-grade", "exercise_id requis (alias)")
            raise HTTPException(status_code=400, detail="exercise_id requis")
        return await _do_grade(payload.exercise_id, payload.answer, payload.reveal_hint)

    async def _do_solution(exercise_id: str, explicit: bool) -> dict[str, Any]:
        # Explicit-only gate (INVARIANT 3): refuse unless the caller asks for
        # the solution explicitly.
        if not explicit:
            raise HTTPException(
                status_code=403,
                detail="La solution ne peut être demandée qu'explicitement.",
            )
        try:
            solution = await tutor_service.request_solution(exercise_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="exercice introuvable")
        return {"solution": solution}

    @app.post("/api/tutor/exercises/{exercise_id}/solution")
    async def tutor_request_solution(exercise_id: str, payload: TutorSolutionRequest) -> dict[str, Any]:
        return await _do_solution(exercise_id, payload.explicit)

    @app.post("/api/tutor/solution")
    async def tutor_request_solution_alias(payload: TutorSolutionAliasRequest) -> dict[str, Any]:
        if not payload.exercise_id:
            _log_error(config, "tutor-solution", "exercise_id requis (alias)")
            raise HTTPException(status_code=400, detail="exercise_id requis")
        return await _do_solution(payload.exercise_id, payload.explicit)

    @app.get("/api/tutor/subjects/{subject_id}/progress")
    @app.get("/api/tutor/progress")
    async def tutor_progress_route(subject_id: str = "") -> dict[str, Any]:
        sid = _resolve_tutor_subject(subject_id)
        rows = tutor_progress.get_progress(sid)
        return {
            "progress": [
                {
                    "concept": r.concept.name,
                    "concept_id": r.concept.id,
                    "score": r.score,
                    "label": r.label,
                    "path_rank": r.path_rank,
                }
                for r in rows
            ]
        }

    @app.get("/api/tutor/subjects/{subject_id}/gaps")
    @app.get("/api/tutor/gaps")
    async def tutor_gaps(subject_id: str = "") -> dict[str, Any]:
        sid = _resolve_tutor_subject(subject_id)
        rows = tutor_progress.get_gaps(sid)
        return {
            "gaps": [
                {
                    "concept": r.concept.name,
                    "concept_id": r.concept.id,
                    "score": r.score,
                    "recent_failures": r.recent_failures,
                }
                for r in rows
            ]
        }

    @app.get("/api/tutor/subjects/{subject_id}/errors")
    @app.get("/api/tutor/errors")
    async def tutor_error_history(
        subject_id: str = "",
        concept_name: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return detailed error history for a subject (US11, T065)."""
        sid = _resolve_tutor_subject(subject_id)
        errors = tutor_service.get_error_history(
            sid, concept_name=concept_name or None, limit=limit
        )
        return {"errors": errors}

    @app.put("/api/tutor/subjects/{subject_id}/path")
    @app.put("/api/tutor/path")
    async def tutor_path(subject_id: str = "", payload: TutorPathRequest = TutorPathRequest()) -> dict[str, Any]:
        sid = _resolve_tutor_subject(subject_id)
        concepts = tutor_progress.reorder_path(sid, payload.order)
        return {
            "concepts": [
                {"id": c.id, "name": c.name, "path_rank": c.path_rank}
                for c in concepts
            ]
        }

    @app.post("/api/tutor/subjects/{subject_id}/auto-path")
    async def tutor_auto_path(subject_id: str) -> dict[str, Any]:
        """Auto-generate a learning path from concepts and diagnostic gaps (US13 / T076).

        Calls the LLM to order concepts by pedagogical dependency, creating a
        LearningPath with PathStep entries. Gaps are prioritised.
        """
        try:
            path = await tutor_service.auto_generate_path(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable ou aucun concept")
        except Exception as exc:
            _log_error(
                config,
                "auto-path",
                f"Génération automatique de parcours impossible : {exc}",
                traceback.format_exc(),
            )
            raise HTTPException(
                status_code=502,
                detail="Le moteur LLM est injoignable — la génération de parcours est indisponible.",
            )
        return path

    # ------------------------------------------------------------------
    # REST: tutor revision (004-local-ai-tutor, US5) — thin transport only
    # Routes follow contracts/tutor-rest-api.md (Revision + Quizzes/Exams).
    # ------------------------------------------------------------------

    @app.post("/api/tutor/subjects/{subject_id}/prepare")
    async def tutor_prepare(subject_id: str) -> dict[str, Any]:
        """Idempotent knowledge preparation (FR-024/FR-034)."""
        try:
            report = await tutor_service.prepare_knowledge(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")
        return report.to_dict()

    @app.get("/api/tutor/subjects/{subject_id}/reviews/due")
    async def tutor_reviews_due(subject_id: str) -> dict[str, Any]:
        """List due flashcards (pure SQL, no LLM — SC-008)."""
        cards = tutor_service.due_reviews(subject_id)
        return {"due": [c.to_dict() for c in cards]}

    @app.post("/api/tutor/reviews/{flashcard_id}/grade")
    async def tutor_grade_review(flashcard_id: str, payload: TutorGradeRequest) -> dict[str, Any]:
        """Grade a flashcard review; walks the D8 ladder."""
        try:
            result = tutor_service.grade_review(flashcard_id, bool(payload.success))
        except KeyError:
            raise HTTPException(status_code=404, detail="flashcard introuvable")
        return result

    @app.post("/api/tutor/subjects/{subject_id}/quizzes")
    async def tutor_create_quiz(subject_id: str, payload: TutorQuizRequest) -> dict[str, Any]:
        """Create a quiz (questions without answers)."""
        try:
            quiz = await tutor_service.create_quiz(subject_id, payload.size, payload.kinds)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet sans notions")
        data = tutor_service.get_quiz(quiz.id)
        if data is None:
            raise HTTPException(status_code=404, detail="quiz introuvable")
        return data

    @app.post("/api/tutor/subjects/{subject_id}/exams")
    async def tutor_create_exam(subject_id: str, payload: TutorExamRequest) -> dict[str, Any]:
        """Create a timed, assistance-free exam (optionally category/corpus-scoped).

        When ``category_ids``/``corpus_ids`` are present, source material is
        restricted to books belonging to ANY listed category/corpus (union);
        the response echoes the effective ``scope``. Omitted/empty = legacy
        behavior.
        """
        try:
            exam = await tutor_service.create_exam(
                subject_id,
                payload.size,
                payload.time_limit_s,
                category_ids=payload.category_ids,
                corpus_ids=payload.corpus_ids,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet sans notions")
        data = tutor_service.get_quiz(exam.id)
        if data is None:
            raise HTTPException(status_code=404, detail="examen introuvable")
        data["scope"] = {
            "category_ids": list(payload.category_ids or []),
            "corpus_ids": list(payload.corpus_ids or []),
        }
        return data

    @app.post("/api/tutor/quizzes/{quiz_id}/submit")
    async def tutor_submit_quiz(quiz_id: str, payload: TutorQuizSubmitRequest) -> dict[str, Any]:
        """Submit answers; corrects, enforces exam rules, updates mastery."""
        try:
            report = await tutor_service.submit_answers(
                quiz_id, payload.answers, hint_requested=payload.hint_requested
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="quiz introuvable")
        except ExamHelpError:
            raise HTTPException(
                status_code=409,
                detail="L'aide est interdite pendant un examen.",
            )
        return report.to_dict()

    @app.get("/api/tutor/quizzes/{quiz_id}")
    async def tutor_get_quiz(quiz_id: str) -> dict[str, Any]:
        """Get a quiz/exam with its questions (answers only once completed)."""
        data = tutor_service.get_quiz(quiz_id)
        if data is None:
            raise HTTPException(status_code=404, detail="quiz introuvable")
        return data

    # ------------------------------------------------------------------
    # REST: US14 — Mode Épreuve (T083): exam document import & resolution
    # ------------------------------------------------------------------

    @app.post("/api/tutor/exam/import")
    async def exam_import(payload: ExamImportRequest) -> dict[str, Any]:
        """Import exam document(s) by file paths and return parsed text (T083)."""
        if not payload.paths:
            raise HTTPException(status_code=400, detail="paths requis")
        try:
            exam_text = tutor_service.parse_exam_document(payload.paths)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"exam_text": exam_text}

    @app.post("/api/tutor/exam/analyze")
    async def exam_analyze(payload: ExamAnalyzeRequest) -> dict[str, Any]:
        """Analyze exam OCR text and return extracted questions (T083)."""
        if not payload.exam_text.strip():
            raise HTTPException(status_code=400, detail="exam_text requis")
        questions = await tutor_service.analyze_exam(payload.exam_text)
        return {"questions": questions}

    @app.post("/api/tutor/exam/questions/{question_id}/resolve")
    async def exam_resolve_question(
        question_id: str, payload: ExamResolveRequest
    ) -> dict[str, Any]:
        """Resolve an exam question: full answer or progressive hint (T083)."""
        result = await tutor_service.resolve_exam_question(
            payload.question_statement,
            payload.concepts,
            hint_level=payload.hint_level,
            rag_context=payload.rag_context,
        )
        return result

    # ------------------------------------------------------------------
    # REST: tutor session memory & continuity (004-local-ai-tutor, US6) — T045
    # Thin transport: delegates to TutorService. No LLM calls involved.
    # ------------------------------------------------------------------

    @app.post("/api/tutor/sessions/{session_id}/close")
    async def tutor_close_session(session_id: str) -> dict[str, Any]:
        """Close a session and persist its SessionSummary (FR-028)."""
        try:
            summary = tutor_service.close_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session introuvable")
        return summary.to_dict()

    @app.get("/api/tutor/subjects/{subject_id}/resume")
    async def tutor_resume(subject_id: str) -> dict[str, Any]:
        """Assemble a resume briefing with zero model calls (FR-029)."""
        try:
            briefing = tutor_service.resume_briefing(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")
        return briefing.to_dict()

    @app.get("/api/tutor/subjects/{subject_id}/sessions")
    async def tutor_list_sessions(subject_id: str) -> dict[str, Any]:
        """List tutoring sessions for a subject with their summaries (US6)."""
        sessions = tutor_store.list_sessions(subject_id)
        out = []
        for s in sessions:
            d = s.to_dict()
            summary = tutor_store.get_session_summary(s.id)
            d["summary"] = summary.to_dict() if summary is not None else None
            out.append(d)
        return {"sessions": out}

    # ------------------------------------------------------------------
    # REST: tutor knowledge tools (004-local-ai-tutor, US7) — T049, FR-031/32/33/34/35
    # Thin transport: locate/rank-books/map are pure retrieval/data (no LLM);
    # compare delegates to TutorService.ask(mode="compare") as a streamed
    # NDJSON response (FR-033). Glossary is a pure list read.
    # ------------------------------------------------------------------

    @app.get("/api/tutor/subjects/{subject_id}/locate")
    async def tutor_locate(subject_id: str, notion: str = "") -> dict[str, Any]:
        """Locate a notion across the subject's books (FR-031).

        Pure keyword scoring over the chunks — no embedding and no LLM call.
        Returns grouped book/chapter/page rows.
        """
        if not notion.strip():
            _log_error(config, "tutor-locate", "notion requis")
            raise HTTPException(status_code=400, detail="notion requis")
        try:
            rows = tutor_service.retriever.locate(subject_id, notion.strip())
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")
        return {"results": rows}

    @app.get("/api/tutor/subjects/{subject_id}/rank-books")
    async def tutor_rank_books(subject_id: str, notion: str = "") -> dict[str, Any]:
        """Rank the subject's books by relevance to a notion (FR-032).

        Pure keyword scoring — aggregates per-chunk scores per book; no LLM
        call.
        """
        if not notion.strip():
            _log_error(config, "tutor-rank", "notion requis")
            raise HTTPException(status_code=400, detail="notion requis")
        try:
            ranked = tutor_service.retriever.rank_books(
                subject_id, notion.strip()
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")
        return {"results": ranked}

    @app.post("/api/tutor/subjects/{subject_id}/compare")
    async def tutor_compare(subject_id: str, payload: TutorCompareRequest) -> StreamingResponse:
        """Compare a notion across the subject's books (FR-033).

        Delegates to ``TutorService.ask(mode="compare")`` and streams the
        synthesis as NDJSON frames (sources → delta* → end), keeping the same
        wire contract as the ``/ws/tutor`` ask path.
        """
        notion = (payload.notion or "").strip()
        if not notion:
            _log_error(config, "tutor-compare", "notion requis")
            raise HTTPException(status_code=400, detail="notion requis")
        try:
            subject = tutor_store._get_subject(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")

        async def _frame_stream():
            async for frame in tutor_service.ask(
                subject.name, notion, mode="compare"
            ):
                yield (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")

        return StreamingResponse(
            _frame_stream(), media_type="application/x-ndjson"
        )

    @app.get("/api/tutor/subjects/{subject_id}/glossary")
    async def tutor_glossary(subject_id: str) -> dict[str, Any]:
        """List the subject's glossary terms (FR-034). Pure read, no LLM."""
        try:
            terms = tutor_store.list_glossary(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")
        return {"terms": [t.to_dict() for t in terms]}

    @app.get("/api/tutor/subjects/{subject_id}/glossary/{term}/explain")
    async def tutor_explain_term(subject_id: str, term: str) -> StreamingResponse:
        """On-demand explanation of a glossary term (FR-034).

        Streams NDJSON frames from the ask pipeline scoped to the term's
        provenance chunks. No separate LLM call for the definition itself —
        the stored glossary definition is surfaced as a `definition` frame.
        """
        try:
            tutor_store._get_subject(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")

        async def _frame_stream() -> AsyncIterator[bytes]:
            async for frame in tutor_service.glossary_explain(subject_id, term):
                yield (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")

        return StreamingResponse(_frame_stream(), media_type="application/x-ndjson")

    @app.get("/api/tutor/subjects/{subject_id}/map")
    async def tutor_knowledge_map(subject_id: str) -> dict[str, Any]:
        """Return the knowledge map {nodes, edges} from stored relations (FR-035).

        Pure data assembly — no LLM call.
        """
        try:
            graph = tutor_service.build_knowledge_map(subject_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="sujet introuvable")
        return graph

    # ------------------------------------------------------------------
    # REST: tutor admin (Phase 5a) — engine/models/categories/corpora/
    # membership + frontend error logging. Thin transport over config +
    # LibraryStore; no LLM calls.
    # ------------------------------------------------------------------

    @app.get("/api/tutor/engine")
    async def tutor_engine() -> dict[str, Any]:
        """Report which local engines are configured (UI capability probe)."""
        llama_bin = bool(config.tutor_llama_bin)
        return {
            "embedding": (
                "gguf-local"
                if llama_bin and config.tutor_embed_gguf
                else "ollama"
            ),
            "ocr": bool(llama_bin and config.tutor_docling_gguf),
        }

    @app.post("/api/tutor/test-connection")
    async def tutor_test_connection() -> dict[str, Any]:
        """Probe the configured provider (Ollama or remote) for reachability."""
        provider = config.llm_provider
        base_url = config.llm_base_url
        api_key = config.llm_api_key

        if provider == "openai" and base_url:
            from ..tutor.providers.openai_compat import OpenAICompatProvider
            probe = OpenAICompatProvider(
                base_url=base_url, api_key=api_key or None
            )
            try:
                ok = await probe.check_health()
                models = await probe.list_models() if ok else []
                return {
                    "ok": ok,
                    "provider": "openai",
                    "base_url": base_url,
                    "model_count": len(models),
                    "models": [m.name for m in models[:20]],
                    "message": (
                        f"{len(models)} modèle(s) disponible(s)"
                        if ok
                        else "Serveur inaccessible"
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "provider": "openai",
                    "message": str(exc)[:200],
                }
            finally:
                await probe.close()
        else:
            try:
                models = await tutor_client.list_models()
                return {
                    "ok": True,
                    "provider": "ollama",
                    "model_count": len(models),
                    "models": [m.name for m in models[:20]],
                    "message": f"{len(models)} modèle(s) disponible(s)",
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "provider": "ollama",
                    "message": str(exc)[:200],
                }

    async def _tutor_models_payload() -> dict[str, Any]:
        # Toujours lister les modèles Ollama locaux (groupe "ollama" en premier)
        # ET les modèles du fournisseur cloud configuré, pour pouvoir basculer
        # entre les deux depuis la liste déroulante.
        # NB : quand llm_provider == "openai", tutor_client est un
        # OpenAICompatProvider — il faut donc un client Ollama dédié pour
        # lister les modèles locaux.
        ollama_names: list[str] = []
        try:
            from ..client import OllamaClient
            ollama = OllamaClient()
            try:
                ollama_names = [m.name for m in await ollama.list_models()]
            finally:
                await ollama.close()
        except Exception:
            ollama_names = []  # Ollama hors ligne : on garde le cloud.

        cloud_names: list[str] = []
        if config.llm_provider == "openai" and config.llm_base_url:
            from ..tutor.providers.openai_compat import OpenAICompatProvider
            probe = OpenAICompatProvider(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key or None,
            )
            try:
                cloud_names = [m.name for m in await probe.list_models()]
            except Exception:
                cloud_names = []
            finally:
                await probe.close()

        # Fusion sans doublons : Ollama d'abord, puis cloud.
        names = list(dict.fromkeys(ollama_names + cloud_names))
        return {
            "embedding": names,
            "llm": names,
            # Sources séparées pour que l'UI regroupe correctement les modèles
            # Ollama (même ceux contenant "/" comme ibm/granite-embedding:…)
            # sous le groupe "ollama", distincts des modèles cloud.
            "sources": {
                "ollama": ollama_names,
                "cloud": cloud_names,
            },
            "current": {
                "embedding": config.tutor_embedding_model,
                "llm": config.tutor_model,
            },
        }

    @app.get("/api/tutor/models")
    async def tutor_models_get() -> dict[str, Any]:
        return await _tutor_models_payload()

    @app.put("/api/tutor/models")
    async def tutor_models_put(payload: TutorModelsUpdate) -> dict[str, Any]:
        if payload.embedding is not None:
            if not isinstance(payload.embedding, str) or not payload.embedding.strip():
                raise HTTPException(
                    status_code=400, detail="embedding doit être une chaîne non vide"
                )
            config.tutor_embedding_model = payload.embedding.strip()
            tutor_service.set_embedding_model(config.tutor_embedding_model)
        if payload.llm is not None:
            if not isinstance(payload.llm, str) or not payload.llm.strip():
                raise HTTPException(
                    status_code=400, detail="llm doit être une chaîne non vide"
                )
            config.tutor_model = payload.llm.strip()
        return await _tutor_models_payload()

    def _category_rows() -> list[dict[str, Any]]:
        rows = []
        for cat in tutor_store.list_categories():
            rows.append({
                "id": cat["id"],
                "name": cat["name"],
                "book_count": len(tutor_store.list_books_by_category(cat["id"])),
            })
        return rows

    def _corpus_rows() -> list[dict[str, Any]]:
        rows = []
        for corpus in tutor_store.list_corpora():
            rows.append({
                "id": corpus["id"],
                "name": corpus["name"],
                "book_count": len(tutor_store.list_books_by_corpus(corpus["id"])),
            })
        return rows

    @app.get("/api/tutor/categories")
    async def tutor_categories_list() -> dict[str, Any]:
        return {"categories": _category_rows()}

    @app.post("/api/tutor/categories")
    async def tutor_categories_create(payload: TutorLabelCreate) -> dict[str, Any]:
        try:
            cat = tutor_store.create_category(payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"category": {"id": cat["id"], "name": cat["name"]}}

    @app.post("/api/tutor/categories/auto-classify")
    async def tutor_categories_auto_classify(
        payload: TutorAutoClassifyRequest | None = None,
    ) -> dict[str, Any]:
        """Classify every book title into categories via the tutor LLM.

        One LLM call per batch of ``batch_size`` titles (default 25). Malformed
        batches are reported in ``failed_batches``; an unreachable engine
        answers 502 with a clear French detail. Empty library ⇒ 200 with the
        ``"aucun livre"`` message and no model call.
        """
        batch_size = payload.batch_size if payload is not None else 25
        try:
            return await tutor_service.auto_classify_categories(batch_size)
        except Exception as exc:  # noqa: BLE001 — transport maps to 502
            _log_error(
                config,
                "auto-classify",
                f"Classification automatique impossible : {exc}",
                traceback.format_exc(),
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Le moteur LLM est injoignable — la classification "
                    "automatique est indisponible pour le moment."
                ),
            )

    @app.post("/api/tutor/categories/{category_id}/rename")
    async def tutor_categories_rename(
        category_id: int, payload: TutorLabelRename
    ) -> dict[str, Any]:
        try:
            cat = tutor_store.rename_category(category_id, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="catégorie inconnue")
        return {"category": {"id": cat["id"], "name": cat["name"]}}

    @app.delete("/api/tutor/categories/{category_id}")
    async def tutor_categories_delete(category_id: int) -> dict[str, Any]:
        try:
            deleted = tutor_store.delete_category(category_id)
        except KeyError:
            deleted = False
        return {"deleted": deleted}

    @app.get("/api/tutor/corpora")
    async def tutor_corpora_list() -> dict[str, Any]:
        return {"corpora": _corpus_rows()}

    @app.post("/api/tutor/corpora")
    async def tutor_corpora_create(payload: TutorLabelCreate) -> dict[str, Any]:
        try:
            corpus = tutor_store.create_corpus(payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"corpus": {"id": corpus["id"], "name": corpus["name"]}}

    @app.post("/api/tutor/corpora/{corpus_id}/rename")
    async def tutor_corpora_rename(
        corpus_id: int, payload: TutorLabelRename
    ) -> dict[str, Any]:
        try:
            corpus = tutor_store.rename_corpus(corpus_id, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="corpus inconnu")
        return {"corpus": {"id": corpus["id"], "name": corpus["name"]}}

    @app.delete("/api/tutor/corpora/{corpus_id}")
    async def tutor_corpora_delete(corpus_id: int) -> dict[str, Any]:
        try:
            deleted = tutor_store.delete_corpus(corpus_id)
        except KeyError:
            deleted = False
        return {"deleted": deleted}

    # --- Membership: books <-> categories / corpora ---

    @app.put("/api/tutor/books/{book_id}/categories")
    async def tutor_book_add_category(
        book_id: str, payload: TutorCategoryMembership
    ) -> dict[str, Any]:
        try:
            added = tutor_store.add_book_to_category(book_id, payload.category_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="livre ou catégorie inconnu")
        return {"added": added}

    @app.delete("/api/tutor/books/{book_id}/categories/{category_id}")
    async def tutor_book_remove_category(
        book_id: str, category_id: int
    ) -> dict[str, Any]:
        if tutor_store.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="livre inconnu")
        removed = tutor_store.remove_book_from_category(book_id, category_id)
        return {"removed": removed}

    @app.get("/api/tutor/books/{book_id}/categories")
    async def tutor_book_categories(book_id: str) -> dict[str, Any]:
        try:
            cats = tutor_store.list_categories_for_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="livre inconnu")
        return {"categories": cats}

    @app.put("/api/tutor/books/{book_id}/corpora")
    async def tutor_book_add_corpus(
        book_id: str, payload: TutorCorpusMembership
    ) -> dict[str, Any]:
        try:
            added = tutor_store.add_book_to_corpus(book_id, payload.corpus_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="livre ou corpus inconnu")
        return {"added": added}

    @app.delete("/api/tutor/books/{book_id}/corpora/{corpus_id}")
    async def tutor_book_remove_corpus(
        book_id: str, corpus_id: int
    ) -> dict[str, Any]:
        if tutor_store.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="livre inconnu")
        removed = tutor_store.remove_book_from_corpus(book_id, corpus_id)
        return {"removed": removed}

    @app.get("/api/tutor/books/{book_id}/corpora")
    async def tutor_book_corpora(book_id: str) -> dict[str, Any]:
        try:
            corpora = tutor_store.list_corpora_for_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="livre inconnu")
        return {"corpora": corpora}

    # --- Frontend error logging ---

    @app.post("/api/log-error")
    async def log_error(payload: LogErrorRequest) -> dict[str, Any]:
        """Persist a frontend-reported error through the shared helper."""
        _log_error(
            config,
            payload.context or "frontend",
            payload.message,
            payload.stack or "",
        )
        return {"logged": True}

    # ------------------------------------------------------------------
    # WebSocket: streaming chat + agent with confirmation round-trip
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # REST: provenance d'embedding & ré-indexation (005-suite)
    # ------------------------------------------------------------------

    @app.get("/api/tutor/stale-books")
    async def stale_books(subject_id: str) -> dict[str, Any]:
        model = config.tutor_embedding_model
        return {
            "model": model,
            "stale": tutor_store.stale_books(subject_id, model),
        }

    @app.post("/api/tutor/books/{book_id}/reindex")
    async def book_reindex(book_id: str) -> dict[str, Any]:
        try:
            return await tutor_service.reindex_book(book_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Livre inconnu") from exc

    # ------------------------------------------------------------------
    # REST: conversations nommées (005-platform-ui-library)
    # ------------------------------------------------------------------

    @app.get("/api/tutor/conversations")
    async def conversations_list() -> dict[str, Any]:
        convs = []
        for c in conversations.list():
            convs.append({
                "id": c["id"],
                "title": c.get("title") or "",
                "subject_id": c.get("subject_id"),
                "subject_name": c.get("subject_name"),
                "updated_at": c.get("updated_at"),
                "started_at": c.get("started_at"),
                "message_count": c.get("message_count", 0),
                "active_sources": len(
                    tutor_store.get_conversation_source_ids(c["id"])
                ),
            })
        return {"conversations": convs}

    @app.post("/api/tutor/conversations")
    async def conversations_create(payload: ConversationCreate) -> dict[str, Any]:
        try:
            sess = conversations.create(payload.subject_id, title=payload.title)
        except Exception as exc:  # espace inconnu
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"conversation": {
            "id": sess.id,
            "title": sess.title or payload.title,
            "subject_id": sess.subject_id,
        }}

    @app.get("/api/tutor/conversations/{conversation_id}")
    async def conversation_get(conversation_id: str) -> dict[str, Any]:
        sess = tutor_store.get_tutoring_session(conversation_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="Conversation inconnue")
        frames = tutor_store.get_session_transcript(conversation_id)
        messages = [
            {"role": str(f.get("role", "")), "text": str(f.get("text", ""))}
            for f in frames
            if isinstance(f, dict) and f.get("role")
        ]
        return {
            "conversation": {
                "id": sess.id,
                "title": sess.title or "",
                "subject_id": sess.subject_id,
            },
            "messages": messages,
        }

    @app.patch("/api/tutor/conversations/{conversation_id}")
    async def conversations_rename(
        conversation_id: str, payload: ConversationRename
    ) -> dict[str, Any]:
        try:
            conversations.rename(conversation_id, payload.title)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Conversation inconnue"
            ) from exc
        sess = tutor_store.get_tutoring_session(conversation_id)
        return {"conversation": {
            "id": conversation_id,
            "title": payload.title,
            "subject_id": sess.subject_id if sess else None,
        }}

    @app.delete("/api/tutor/conversations/{conversation_id}")
    async def conversations_delete(conversation_id: str) -> dict[str, Any]:
        return {"deleted": conversations.delete(conversation_id)}

    @app.get("/api/tutor/conversations/{conversation_id}/sources")
    async def conversation_sources_list(conversation_id: str) -> dict[str, Any]:
        ids = conversations.sources(conversation_id)
        books = []
        for bid in ids:
            b = tutor_store.get_book(bid)
            books.append({"id": bid, "title": (b.title if b else bid)})
        return {"book_ids": ids, "books": books}

    @app.put("/api/tutor/conversations/{conversation_id}/sources")
    async def conversation_sources_set(
        conversation_id: str, payload: ConversationSourcesPayload
    ) -> dict[str, Any]:
        try:
            n = conversations.set_sources(conversation_id, payload.book_ids)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Conversation inconnue"
            ) from exc
        return {"active": n}

    # ------------------------------------------------------------------
    # Feature 008 — Photo de conversation (US7) — thin transport
    # ------------------------------------------------------------------

    @app.post("/api/tutor/conversations/{conversation_id}/photo")
    async def conversation_photo_import(conversation_id: str,
                                        payload: dict[str, Any]) -> dict[str, Any]:
        """Import a photo into a conversation (FR-031)."""
        from ..tutor.conversation_photo import ConversationPhotoService
        svc = ConversationPhotoService(tutor_store, tutor_service.document_parser)
        result = await svc.import_photo(
            conversation_id, str(payload.get("path", "")))
        await _emit_tutor_event(
            "photo_status",
            conversation_id=conversation_id,
            status=result.get("confirmation_status", "pending"),
        )
        return result

    @app.post("/api/tutor/conversation-photos/{photo_id}/confirm")
    async def conversation_photo_confirm(photo_id: str) -> dict[str, Any]:
        """Confirm a conversation photo (FR-031/FR-032)."""
        from ..tutor.conversation_photo import ConversationPhotoService
        return ConversationPhotoService(tutor_store).confirm(photo_id)

    @app.get("/api/tutor/conversation-photos/{photo_id}")
    async def conversation_photo_get(photo_id: str) -> dict[str, Any]:
        """Get a conversation photo."""
        from ..tutor.conversation_photo import ConversationPhotoService
        return ConversationPhotoService(tutor_store).get(photo_id)

    # ------------------------------------------------------------------
    # Feature 008 — Multi-utilisateur familial (US9) — thin transport
    # ------------------------------------------------------------------

    @app.get("/api/tutor/learners")
    async def learners_list() -> dict[str, Any]:
        """List all learner profiles (FR-038)."""
        from ..tutor.learners import LearnerService
        return LearnerService(tutor_store).list()

    @app.post("/api/tutor/learners")
    async def learners_create(payload: TutorLearnerRequest) -> dict[str, Any]:
        """Create a learner profile (FR-038)."""
        from ..tutor.learners import LearnerService
        return LearnerService(tutor_store).create(payload.name, avatar=payload.avatar)

    @app.post("/api/tutor/learners/{learner_id}/activate")
    async def learners_activate(learner_id: str) -> dict[str, Any]:
        """Select a learner; returns its scoped subjects (FR-039)."""
        from ..tutor.learners import LearnerService
        try:
            return LearnerService(tutor_store).activate(learner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Apprenant inconnu") from exc

    @app.delete("/api/tutor/learners/{learner_id}")
    async def learners_delete(learner_id: str) -> dict[str, Any]:
        """Delete a learner and cascade its data (FR-039 edge case)."""
        from ..tutor.learners import LearnerService
        try:
            return LearnerService(tutor_store).delete(learner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Apprenant inconnu") from exc

    # ------------------------------------------------------------------
    # Feature 008 — Carnet de matière (US8) — thin transport only
    # ------------------------------------------------------------------

    def _notebook_service():
        """Build a NotebookService wired to the tutor LLM when available."""
        from ..tutor.notebook import NotebookService
        llm = getattr(tutor_service, "_llm_client", None)
        return NotebookService(tutor_store, llm=llm)

    @app.get("/api/tutor/subjects/{subject_id}/notebook")
    async def notebook_get(subject_id: str) -> dict[str, Any]:
        """Return the subject notebook with sources and outputs (FR-034/FR-035)."""
        return _notebook_service().get(subject_id)

    @app.post("/api/tutor/subjects/{subject_id}/notebook/notes")
    async def notebook_add_note(
        subject_id: str, payload: TutorNotebookNoteRequest
    ) -> dict[str, Any]:
        """Add a personal note (FR-032)."""
        try:
            return _notebook_service().add_note(subject_id, payload.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tutor/subjects/{subject_id}/notebook/actions")
    async def notebook_run_action(
        subject_id: str, payload: TutorNotebookActionRequest
    ) -> dict[str, Any]:
        """Execute a notebook RAG action (FR-033)."""
        try:
            result = await _notebook_service().run_action(
                subject_id, payload.action, payload.params
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _emit_tutor_event(
            "notebook_output",
            subject_id=subject_id,
            output_id=result.get("output", {}).get("id"),
            kind=result.get("output", {}).get("kind"),
        )
        return result

    @app.delete("/api/tutor/notebook-outputs/{output_id}")
    async def notebook_delete_output(output_id: str) -> dict[str, Any]:
        """Delete a notebook output (FR-035)."""
        return _notebook_service().delete_output(output_id)

    # ------------------------------------------------------------------
    # REST: Lesson discussion centrée (Feature 009, US1) — thin transport
    # ------------------------------------------------------------------

    @app.post("/api/tutor/path-steps/{step_id}/discussion")
    async def lesson_discussion_create(step_id: str, request: Request) -> dict[str, Any]:
        """Create or return existing lesson discussion for a path step.

        Learner identity via ``X-Learner-Id`` header or ``learner_id`` query param.
        """
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id") or ""
        learner_id = learner_id.strip()
        if not learner_id:
            raise HTTPException(status_code=400, detail="learner_id requis (header X-Learner-Id ou query param)")
        from ..tutor.lesson_discussion import LessonDiscussionService
        svc = LessonDiscussionService(tutor_store)
        try:
            disc = svc.get_or_create_discussion(step_id, learner_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Étape inconnue")
        except Exception as exc:
            _log_error(config, "lesson-discussion-create", f"create discussion step {step_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Création de la discussion impossible (voir errors.log)")
        return {"discussion": disc.to_dict()}

    @app.get("/api/tutor/lesson-discussions/{discussion_id}")
    async def lesson_discussion_get(discussion_id: str) -> dict[str, Any]:
        """Return discussion with messages, generated_contents, exercise_attempts (FR-010)."""
        from ..tutor.lesson_discussion import LessonDiscussionService
        svc = LessonDiscussionService(tutor_store)
        try:
            payload = svc.get_discussion(discussion_id)
        except Exception as exc:
            _log_error(config, "lesson-discussion-get", f"get discussion {discussion_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Lecture de la discussion impossible (voir errors.log)")
        if payload is None:
            raise HTTPException(status_code=404, detail="Discussion inconnue")
        return payload

    @app.post("/api/tutor/lesson-discussions/{discussion_id}/generate-course")
    async def lesson_generate_course(discussion_id: str, request: Request) -> dict[str, Any]:
        """Generate a full course for the lesson (FR-004/FR-015)."""
        from ..tutor.lesson_discussion import LessonDiscussionService
        # learner_id optional — validate if provided
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id") or None
        if learner_id is not None:
            learner_id = learner_id.strip() or None
        svc = LessonDiscussionService(tutor_store, tutor_service=tutor_service)
        try:
            content = svc.generate_course(discussion_id, learner_id=learner_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Discussion inconnue")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except Exception as exc:
            _log_error(config, "lesson-course", f"generate-course {discussion_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Génération du cours impossible (voir errors.log)")
        return {"content": content}

    @app.post("/api/tutor/lesson-discussions/{discussion_id}/generate-summary")
    async def lesson_generate_summary(discussion_id: str, request: Request) -> dict[str, Any]:
        """Generate a condensed summary (FR-005/FR-015) — independent if no course."""
        from ..tutor.lesson_discussion import LessonDiscussionService
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id") or None
        if learner_id is not None:
            learner_id = learner_id.strip() or None
        svc = LessonDiscussionService(tutor_store, tutor_service=tutor_service)
        try:
            content = svc.generate_summary(discussion_id, learner_id=learner_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Discussion inconnue")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except Exception as exc:
            _log_error(config, "lesson-summary", f"generate-summary {discussion_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Génération de la synthèse impossible (voir errors.log)")
        return {"content": content}

    @app.post("/api/tutor/lesson-discussions/{discussion_id}/exercises")
    async def lesson_generate_exercises(discussion_id: str, request: Request) -> dict[str, Any]:
        """Generate 3–5 exercises for the lesson (FR-006)."""
        from ..tutor.lesson_discussion import LessonDiscussionService
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id") or None
        if learner_id is not None:
            learner_id = learner_id.strip() or None
        svc = LessonDiscussionService(tutor_store, tutor_service=tutor_service)
        try:
            attempt = svc.generate_exercises(discussion_id, learner_id=learner_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Discussion inconnue")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except Exception as exc:
            _log_error(config, "lesson-exercises", f"generate-exercises {discussion_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Génération des exercices impossible (voir errors.log)")
        return {"attempt": attempt}

    @app.post("/api/tutor/lesson-discussions/{discussion_id}/exercises/{attempt_id}/submit")
    async def lesson_submit_exercises(discussion_id: str, attempt_id: str, request: Request) -> dict[str, Any]:
        """Evaluate exercise answers, compute score, update status (FR-007/FR-008)."""
        from ..tutor.lesson_discussion import LessonDiscussionService
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id") or None
        if learner_id is not None:
            learner_id = learner_id.strip() or None
        try:
            body = await request.json()
        except Exception:
            body = {}
        answers = body.get("answers", body) if isinstance(body, dict) else {}
        # allow direct mapping or nested {"answers": {...}}
        if isinstance(answers, dict) and "answers" in answers and isinstance(answers["answers"], dict):
            answers = answers["answers"]
        if not isinstance(answers, dict):
            answers = {}
        svc = LessonDiscussionService(tutor_store, tutor_service=tutor_service)
        try:
            result = svc.submit_exercises(discussion_id, attempt_id, answers, learner_id=learner_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Discussion ou tentative inconnue")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            _log_error(config, "lesson-submit", f"submit {discussion_id}/{attempt_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Évaluation impossible (voir errors.log)")
        return {"attempt": result}

    async def _lesson_complete_impl(discussion_id: str, request: Request) -> dict[str, Any]:
        from ..tutor.lesson_discussion import LessonDiscussionService
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id") or None
        if learner_id is not None:
            learner_id = learner_id.strip() or None
        svc = LessonDiscussionService(tutor_store, tutor_service=tutor_service)
        try:
            res = svc.complete_manual(discussion_id, learner_id=learner_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Discussion inconnue")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except Exception as exc:
            _log_error(config, "lesson-complete", f"complete {discussion_id}: {exc}", traceback.format_exc())
            raise HTTPException(status_code=500, detail="Validation manuelle impossible (voir errors.log)")
        return res

    @app.post("/api/tutor/lesson-discussions/{discussion_id}/complete")
    async def lesson_complete(discussion_id: str, request: Request) -> dict[str, Any]:
        """Force completion regardless of score (FR-008)."""
        return await _lesson_complete_impl(discussion_id, request)

    @app.post("/api/tutor/lesson-discussions/{discussion_id}/complete-manual")
    async def lesson_complete_manual(discussion_id: str, request: Request) -> dict[str, Any]:
        """Alias for manual completion (FR-008)."""
        return await _lesson_complete_impl(discussion_id, request)

    # ------------------------------------------------------------------
    # REST: Learning paths — Parcours (Feature 006 — adaptive learning)
    # ------------------------------------------------------------------

    def _active_subject_id() -> str | None:
        """Return the selected subject id, if one exists in this store."""
        active = tutor_store.active_subject()
        return active.id if active is not None else None

    @app.post("/api/tutor/paths")
    async def create_path(request: Request) -> dict[str, Any]:
        body = await request.json()
        subject_id = body.get("subject_id") or _active_subject_id()
        if not subject_id:
            raise HTTPException(status_code=400, detail="subject_id requis")
        title = str(body.get("title", "")).strip()
        if not title:
            raise HTTPException(status_code=400, detail="title requis")
        if tutor_store.get_subject(subject_id) is None:
            raise HTTPException(status_code=404, detail="Sujet inconnu")
        return tutor_service.create_path(
            subject_id, title, body.get("description", "")
        )

    def _enrich_steps_with_discussion(steps: list[dict[str, Any]], learner_id: str | None) -> list[dict[str, Any]]:
        """Add ``discussion_id`` per step via lesson_discussions lookup filtered by learner_id."""
        if not learner_id:
            for s in steps:
                s["discussion_id"] = None
            return steps
        for s in steps:
            try:
                row = tutor_store._conn.execute(
                    "SELECT id FROM lesson_discussions WHERE path_step_id = ? AND learner_id = ? LIMIT 1",
                    (s.get("id"), learner_id),
                ).fetchone()
                s["discussion_id"] = row["id"] if row is not None else None
            except Exception:
                s["discussion_id"] = None
        return steps

    @app.get("/api/tutor/paths")
    async def list_paths(request: Request, subject_id: str | None = None) -> dict[str, Any]:
        sid = subject_id or request.query_params.get("subject_id") or _active_subject_id()
        if not sid:
            raise HTTPException(status_code=400, detail="subject_id requis")
        if tutor_store.get_subject(sid) is None:
            raise HTTPException(status_code=404, detail="Sujet inconnu")
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id")
        learner_id = learner_id.strip() if isinstance(learner_id, str) and learner_id.strip() else None
        paths = tutor_service.list_paths(sid)
        # Enrich each path with steps including status + discussion_id and progress counts
        for p in paths:
            steps = [s.to_dict() for s in tutor_store.list_path_steps(p["id"])]
            _enrich_steps_with_discussion(steps, learner_id)
            p["steps"] = steps
            completed = sum(1 for s in steps if s.get("status") == "completed")
            total = len(steps)
            p["progress_count"] = f"{completed}/{total}" if total else "0/0"
            p["completed"] = completed
            p["total"] = total
            if total:
                p["progress"] = round(completed / total * 100, 1)
            else:
                p["progress"] = 0.0
        return {"paths": paths}

    @app.get("/api/tutor/paths/{path_id}")
    async def get_path(request: Request, path_id: str) -> dict[str, Any]:
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id")
        learner_id = learner_id.strip() if isinstance(learner_id, str) and learner_id.strip() else None
        path = tutor_service.get_path(path_id)
        if path is None:
            raise HTTPException(404, "Parcours inconnu")
        steps = path.get("steps") or []
        _enrich_steps_with_discussion(steps, learner_id)
        path["steps"] = steps
        completed = sum(1 for s in steps if s.get("status") == "completed")
        total = len(steps)
        path["progress_count"] = f"{completed}/{total}" if total else "0/0"
        path["completed"] = completed
        path["total"] = total
        return path

    @app.get("/api/tutor/path-steps")
    async def list_path_steps(request: Request, path_id: str | None = None) -> dict[str, Any]:
        learner_id = request.headers.get("x-learner-id") or request.query_params.get("learner_id")
        learner_id = learner_id.strip() if isinstance(learner_id, str) and learner_id.strip() else None
        pid = path_id or request.query_params.get("path_id")
        if not pid:
            raise HTTPException(status_code=400, detail="path_id requis (query param path_id)")
        if tutor_store.get_learning_path(pid) is None:
            raise HTTPException(status_code=404, detail="Parcours inconnu")
        steps = [s.to_dict() for s in tutor_store.list_path_steps(pid)]
        _enrich_steps_with_discussion(steps, learner_id)
        return {"steps": steps, "path_id": pid}

    @app.put("/api/tutor/paths/{path_id}")
    async def update_path(path_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        path = tutor_service.update_path(
            path_id,
            title=body.get("title"),
            description=body.get("description"),
            status=body.get("status"),
        )
        if path is None:
            raise HTTPException(404, "Parcours inconnu")
        return path

    @app.delete("/api/tutor/paths/{path_id}")
    async def delete_path(path_id: str) -> dict[str, Any]:
        if not tutor_service.delete_path(path_id):
            raise HTTPException(404, "Parcours inconnu")
        return {"ok": True}

    @app.post("/api/tutor/paths/{path_id}/steps")
    async def add_path_step(path_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        activity_type = str(body.get("activity_type", "")).strip()
        activity_id = str(body.get("activity_id", "")).strip()
        if not activity_type or not activity_id:
            raise HTTPException(400, "activity_type et activity_id requis")
        valid = {"concept", "quiz", "exercise", "flashcard_review", "reading"}
        if activity_type not in valid:
            raise HTTPException(400, f"activity_type doit être dans {valid}")
        return tutor_service.add_path_step(
            path_id, activity_type, activity_id, body.get("title", "")
        )

    @app.put("/api/tutor/paths/{path_id}/steps/reorder")
    async def reorder_path_steps(path_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        step_ids = body.get("step_ids", [])
        if not step_ids:
            raise HTTPException(400, "step_ids requis")
        return {"steps": tutor_service.reorder_path_steps(path_id, step_ids)}

    @app.post("/api/tutor/paths/steps/{step_id}/complete")
    async def complete_path_step(step_id: str) -> dict[str, Any]:
        step = tutor_service.complete_path_step(step_id)
        if step is None:
            raise HTTPException(404, "Étape inconnue")
        return step

    @app.delete("/api/tutor/paths/steps/{step_id}")
    async def delete_path_step(step_id: str) -> dict[str, Any]:
        if not tutor_service.delete_path_step(step_id):
            raise HTTPException(404, "Étape inconnue")
        return {"ok": True}

    @app.post("/api/tutor/subjects/{subject_id}/path/generate-from-books")
    async def generate_path_from_books(subject_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        book_ids = body.get("book_ids", [])
        if not book_ids:
            raise HTTPException(400, "book_ids requis")
        if tutor_store.get_subject(subject_id) is None:
            raise HTTPException(404, "Sujet inconnu")
        result = await tutor_service.generate_path_from_books(subject_id, book_ids)
        return result

    @app.post("/api/tutor/subjects/{subject_id}/path/from-program")
    async def path_from_program(subject_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        program_id = body.get("program_id", "")
        if not program_id:
            raise HTTPException(400, "program_id requis")
        if tutor_store.get_subject(subject_id) is None:
            raise HTTPException(404, "Sujet inconnu")
        result = tutor_service.path_from_program(subject_id, program_id)
        return result

    # ------------------------------------------------------------------
    # REST: Domain classification (Feature 006 — adaptive learning)
    # ------------------------------------------------------------------

    @app.get("/api/tutor/subjects/{subject_id}/domain")
    async def get_domain(subject_id: str) -> dict[str, Any]:
        return {"domain": tutor_service.get_subject_domain(subject_id)}

    @app.put("/api/tutor/subjects/{subject_id}/domain")
    async def set_domain(subject_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        domain = str(body.get("domain", "generique")).strip()
        valid = {"programmation", "mathematiques", "sciences", "langues", "generique"}
        if domain not in valid:
            raise HTTPException(400, f"domain doit être dans {valid}")
        tutor_service.set_subject_domain(subject_id, domain)
        return {"domain": domain}

    @app.post("/api/tutor/subjects/{subject_id}/classify")
    async def classify_subject(subject_id: str) -> dict[str, Any]:
        domain = await tutor_service.classify_subject(subject_id)
        return {"domain": domain}

    @app.post("/api/tutor/books/{book_id}/summary")
    async def summarize_book(book_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        chapter = body.get("chapter")
        return await tutor_service.summarize_book(book_id, chapter)

    @app.post("/api/tutor/subjects/{subject_id}/revision-sheet")
    async def revision_sheet(subject_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        book_id = body.get("book_id")
        chapter = body.get("chapter")
        return tutor_service.generate_revision_sheet(subject_id, book_id, chapter)

    # ------------------------------------------------------------------
    # REST: diagnostic initial / quiz de positionnement (US3 / T024)
    # ------------------------------------------------------------------

    @app.post("/api/tutor/subjects/{subject_id}/diagnostic")
    async def start_diagnostic(subject_id: str) -> dict[str, Any]:
        return tutor_service.start_diagnostic(subject_id)

    @app.post("/api/tutor/diagnostic/{session_id}/answer")
    async def submit_diagnostic_answer(session_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        answer = body.get("answer", "")
        return tutor_service.submit_diagnostic_answer(session_id, answer)

    @app.get("/api/tutor/diagnostic/{session_id}/result")
    async def get_diagnostic_result(session_id: str) -> dict[str, Any]:
        return tutor_service.get_diagnostic_result(session_id)

    # ------------------------------------------------------------------
    # REST: réglages utilisateur (005-platform-ui-library)
    # ------------------------------------------------------------------

    @app.get("/api/tutor/settings")
    async def settings_get() -> dict[str, Any]:
        return {
            "options": config.options.to_dict(),
            "tutor": {
                "think": config.tutor_think,
                "socratic": config.tutor_socratic,
                "level": config.tutor_level,
                "top_k": config.tutor_top_k,
                "llm_provider": config.llm_provider,
                "llm_base_url": config.llm_base_url,
                "llm_api_key": config.llm_api_key,
                "embed_batch_size": config.tutor_embed_batch_size,
                "max_parallel_embed": config.tutor_max_parallel_embed,
                "nightly_enabled": config.tutor_nightly_enabled,
                "nightly_start_at": config.tutor_nightly_start_at,
                "nightly_stop_at": config.tutor_nightly_stop_at,
                "nightly_only_on_ac": config.tutor_nightly_only_on_ac,
                "nightly_max_runtime_minutes": config.tutor_nightly_max_runtime_minutes,
                "nightly_prepare_enabled": config.tutor_nightly_prepare_enabled,
            },
        }

    @app.put("/api/tutor/settings")
    async def settings_update(payload: SettingsUpdate) -> dict[str, Any]:
        if payload.options is not None:
            merged = config.options.to_dict()
            merged.update(payload.options)
            try:
                config.options = OllamaOptions(**merged)
            except TypeError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Option inconnue : {exc}"
                ) from exc
        try:
            if payload.think is not None:
                config.tutor_think = payload.think
            if payload.socratic is not None:
                config.tutor_socratic = payload.socratic
            if payload.level is not None:
                config.tutor_level = payload.level
            if payload.top_k is not None:
                config.tutor_top_k = payload.top_k
            if payload.llm_provider is not None:
                config.llm_provider = payload.llm_provider
            if payload.llm_base_url is not None:
                config.llm_base_url = payload.llm_base_url
            if payload.llm_api_key is not None:
                config.llm_api_key = payload.llm_api_key
            if payload.embed_batch_size is not None:
                config.tutor_embed_batch_size = payload.embed_batch_size
            if payload.max_parallel_embed is not None:
                config.tutor_max_parallel_embed = payload.max_parallel_embed
            if payload.nightly_enabled is not None:
                config.tutor_nightly_enabled = payload.nightly_enabled
            if payload.nightly_start_at is not None:
                config.tutor_nightly_start_at = payload.nightly_start_at
            if payload.nightly_stop_at is not None:
                config.tutor_nightly_stop_at = payload.nightly_stop_at
            if payload.nightly_only_on_ac is not None:
                config.tutor_nightly_only_on_ac = payload.nightly_only_on_ac
            if payload.nightly_max_runtime_minutes is not None:
                config.tutor_nightly_max_runtime_minutes = payload.nightly_max_runtime_minutes
            if payload.nightly_prepare_enabled is not None:
                config.tutor_nightly_prepare_enabled = payload.nightly_prepare_enabled
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config.save()  # persistance immédiate (préférence utilisateur)
        if config.tutor_nightly_enabled:
            await tutor_service.start_nightly_scheduler()
        else:
            await tutor_service.stop_nightly_scheduler()
        return await settings_get()

    @app.post("/api/tutor/restart")
    async def restart_server() -> dict[str, Any]:
        """Schedule a server restart via os.execv (same process image)."""
        import signal

        async def _do_restart() -> None:
            await asyncio.sleep(0.3)  # allow response to be sent
            os.execv(sys.executable, [sys.executable] + sys.argv)

        asyncio.ensure_future(_do_restart())
        return {"ok": True, "message": "Redémarrage en cours…"}

    # ------------------------------------------------------------------
    # REST: learner profile / gamification (US15)
    # ------------------------------------------------------------------

    @app.get("/api/tutor/profile")
    async def tutor_profile() -> dict[str, Any]:
        """Return the learner profile with XP, streak and badges (US15)."""
        return {"profile": tutor_store.get_learner_profile()}

    # ------------------------------------------------------------------
    # WebSocket: tutor grounded Q&A (004-local-ai-tutor, US2)
    # ------------------------------------------------------------------

    @app.websocket("/ws/tutor")
    async def ws_tutor_endpoint(ws: WebSocket) -> None:
        # T024: same-origin check BEFORE upgrading the socket (mirrors /ws).
        origin = ws.headers.get("origin")
        if (origin is not None and not _origin_allowed(origin)) or not _host_header_allowed(
            ws.headers.get("host")
        ):
            await ws.close(code=1008)
            return

        # Gated on config.tutor_enabled (mirrors GET /tutor).
        if not config.tutor_enabled:
            await ws.close(code=1008)
            return

        await ws.accept()
        app.state.tutor_ws_clients.add(ws)

        async def send(payload: dict[str, Any]) -> None:
            await ws.send_json(payload)

        async def safe_send(payload: dict[str, Any]) -> None:
            """Send used by the background run; a dropped socket must not kill
            the server-side run (T011 keep-alive pattern)."""
            try:
                await send(payload)
            except Exception:
                pass

        def _resolve_subject(data: dict[str, Any]):
            """Resolve the active subject from the frame (id or name) or the
            store's active subject; returns a ``Subject`` or ``None``."""
            ref = data.get("subject_id") or data.get("subject")
            if ref:
                ref_s = str(ref)
                for s in tutor_store.list_subjects():
                    if s.id == ref_s or s.name.lower() == ref_s.lower():
                        return s
            subject = tutor_store.active_subject()
            if subject is not None:
                return subject
            subjects = tutor_store.list_subjects()
            if subjects:
                return subjects[0]
            return None

        cancel_event = asyncio.Event()
        run_task: asyncio.Task | None = None

        def _run_active() -> bool:
            return run_task is not None and not run_task.done()

        async def _drive_tutor_ask(
            subject_name: str,
            data: dict[str, Any],
            run_cancel: asyncio.Event,
        ) -> None:
            """Forward TutorService.ask frames to the socket (mapping the
            service's ``delta``/``reasoning`` vocabulary onto the wire
            ``content_delta``/``thinking_delta`` frames).

            Resolves the per-request socratic/level/think toggles (defaulting to
            config values, D10) and echoes the resolved values back on the
            ``start`` and ``end`` frames so the UI can reflect the effective
            mode (US3, T029).
            """
            # Resolve toggles: config defaults, then in-conversation overrides
            # parsed from the question text (FR-014).
            think = bool(data.get("think", config.tutor_think))
            socratic = (
                config.tutor_socratic
                if data.get("socratic") is None
                else bool(data.get("socratic"))
            )
            level = data.get("level") or config.tutor_level
            level, socratic = resolve_overrides(
                data.get("question", ""), level, socratic
            )
            try:
                await send({
                    "type": "start",
                    "run_id": uuid.uuid4().hex,
                    "mode": "ask",
                    "think": think,
                    "socratic": socratic,
                    "level": level,
                })
                async for frame in tutor_service.ask(
                    subject_name,
                    data.get("question", ""),
                    model=data.get("model"),
                    think=think,
                    socratic=socratic,
                    level=level,
                    session_id=data.get("session_id"),
                    cancel=run_cancel,
                    mode=data.get("mode", "ask"),
                    conversation_id=data.get("conversation_id"),
                    book_ids=(
                        [str(b) for b in data["book_ids"]]
                        if isinstance(data.get("book_ids"), list)
                        else None
                    ),
                    term=data.get("term"),
                ):
                    ftype = frame.get("type")
                    if ftype == "sources":
                        await safe_send({"type": "sources", "sources": frame["sources"]})
                    elif ftype == "definition":
                        await safe_send({
                            "type": "definition",
                            "term": frame.get("term"),
                            "definition": frame.get("definition"),
                        })
                    elif ftype == "delta":
                        await safe_send({"type": "content_delta", "text": frame["text"]})
                    elif ftype == "reasoning":
                        await safe_send({"type": "thinking_delta", "text": frame["text"]})
                    elif ftype == "stats":
                        await safe_send({
                            "type": "stats",
                            "prompt_tokens": frame.get("prompt_tokens", 0),
                            "generated_tokens": frame.get("generated_tokens", 0),
                            "tok_s": frame.get("tok_s", 0.0),
                        })
                    elif ftype == "citation_warnings":
                        # US10 — T060: surface citation validation warnings to the client.
                        await safe_send({
                            "type": "citation_warnings",
                            "warnings": frame.get("warnings", []),
                            "valid_citations": frame.get("valid_citations", []),
                        })
                    elif ftype == "error":
                        await safe_send({
                            "type": "error",
                            "message": frame.get("message", ""),
                            "code": frame.get("code"),
                        })
                    elif ftype == "end":
                        await safe_send({
                            "type": "end",
                            "status": frame.get("status", "done"),
                            "session_id": frame.get("session_id"),
                            "think": think,
                            "socratic": socratic,
                            "level": level,
                        })
                        return
            except Exception as e:  # noqa: BLE001 — always close the run cleanly
                _log_error(
                    config,
                    "ws-tutor",
                    f"Web tutor internal error: {e}",
                    traceback.format_exc(),
                )
                await safe_send({
                    "type": "error",
                    "message": f"Erreur interne : {e}",
                })
                await safe_send({"type": "end", "status": "error", "session_id": None})

        try:
            while True:
                data = await ws.receive_json()
                msg_type = data.get("type", "")

                if msg_type == "cancel":
                    cancel_event.set()
                    await send({"type": "cancelled"})
                    continue

                if msg_type == "transcribe":
                    # US8 / T052: local whisper transcription of a spoken question.
                    if _run_active():
                        await send({
                            "type": "error",
                            "code": "busy",
                            "message": "Une opération est déjà en cours.",
                        })
                        continue
                    transcriber = WhisperTranscriber(
                        config.tutor_whisper_binary, config.tutor_whisper_model
                    )
                    if not transcriber.available:
                        await send({
                            "type": "error",
                            "code": "voice_disabled",
                            "message": "Reconnaissance vocale non configurée.",
                        })
                        continue
                    audio_b64 = data.get("audio", "")
                    try:
                        raw = base64.b64decode(audio_b64, validate=True)
                    except (ValueError, binascii.Error):
                        await send({
                            "type": "error",
                            "code": "invalid_audio",
                            "message": "Audio invalide.",
                        })
                        continue
                    if len(raw) > 10 * 1024 * 1024:
                        await send({
                            "type": "error",
                            "code": "invalid_audio",
                            "message": "Audio trop volumineux.",
                        })
                        continue
                    tmp_dir = os.path.join(config.config_dir, "tutor", "tmp")
                    os.makedirs(tmp_dir, exist_ok=True)
                    wav_path = os.path.join(tmp_dir, f"voice_{uuid.uuid4().hex}.wav")
                    with open(wav_path, "wb") as fh:
                        fh.write(raw)
                    try:
                        transcript = await transcriber.transcribe_wav(wav_path)
                    except VoiceError as ve:
                        await send({
                            "type": "error",
                            "code": "transcribe_failed",
                            "message": str(ve),
                        })
                    else:
                        await send({"type": "transcript", "text": transcript})
                    finally:
                        try:
                            os.remove(wav_path)
                        except OSError:
                            pass
                    continue

                if msg_type != "ask":
                    continue

                if _run_active():
                    # Invariant: exactly one active run per connection.
                    await send({
                        "type": "error",
                        "code": "busy",
                        "message": "Une question est déjà en cours.",
                    })
                    continue

                subject = _resolve_subject(data)
                if subject is None:
                    # Fallback: auto-create default subject when DB is empty
                    try:
                        subjects = tutor_store.list_subjects()
                        if subjects:
                            subject = subjects[0]
                        else:
                            try:
                                subject = tutor_store.create_subject("Général")
                            except ValueError:
                                # race / case-insensitive duplicate
                                subjects = tutor_store.list_subjects()
                                subject = subjects[0] if subjects else None
                            if subject is not None:
                                try:
                                    tutor_store.select_subject(subject.id)
                                except Exception:
                                    pass
                    except Exception:
                        subject = None
                    if subject is None:
                        await send({
                            "type": "error",
                            "code": "no_subject",
                            "message": "Aucun sujet actif pour le tuteur.",
                        })
                        continue

                cancel_event.clear()
                run_task = asyncio.ensure_future(
                    _drive_tutor_ask(subject.name, data, cancel_event)
                )
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001 — log unexpected socket-loop errors
            _log_error(
                config,
                "ws-tutor",
                f"Web tutor socket error: {e}",
                traceback.format_exc(),
            )
            try:
                await safe_send({
                    "type": "error",
                    "message": f"Erreur interne : {e}",
                })
                await safe_send({"type": "end", "status": "error", "session_id": None})
            except Exception:
                pass
        finally:
            # A dropped socket cancels the in-flight run so it does not leak.
            cancel_event.set()
            app.state.tutor_ws_clients.discard(ws)

    return app
