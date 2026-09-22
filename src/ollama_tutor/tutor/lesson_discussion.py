"""Lesson discussion service — Feature 009 (US1).

UI-agnostic (no fastapi/textual). Centralises discussion creation,
status transition not_started→in_progress and RAG filtering by notion.

Delegates LLM generation to ``TutorService`` when available but keeps
fallback deterministic path for offline tests.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import difflib
import re
from typing import Any

from .models import LessonDiscussion, SourceReference, _now_iso
from .sandbox import RunResult, run_python
from .store import LibraryStore, normalize_notion_key, sanitize_notion_id


#: Honest-fallback header mention (required in offline generated content).
_OFFLINE_MENTION = "généré hors-ligne depuis les extraits"

#: Stall budget for one SSE course stream: CPU courses run ~4–6 min, so a
#: short per-chunk timeout would kill legitimate slow generations. Applied
#: per received chunk (live deltas keep flowing), not as an overall cap.
LESSON_STREAM_TIMEOUT_S = 420.0

#: Excerpt hygiene (prod: promos, legal notices, cover pages and TOCs
#: became COURSE ITEMS, and the same excerpt repeated 3x in the render).
#: Pure-Python stdlib signals scored per excerpt; selection-only (never
#: touches extraction/indexation).
_URL_RE = re.compile(r"https?://|www\.|t\.me|telegram\.me", re.IGNORECASE)
_MESSAGING_RE = re.compile(r"t\.me|telegram|whatsapp", re.IGNORECASE)
_CTA_RE = re.compile(
    r"cliqu\w*|rejoign\w*|abonn\w*|promo\b|offre exclusive|\blike[sz]?\b"
    r"|partage[rz]\b|concours|youtube|tiktok|instagram|facebook|discord",
    re.IGNORECASE,
)
_LEGAL_RE = re.compile(
    r"\bisbn\b|tous droits|copyright|©|mentions?\s+légales?|dépôt légal"
    r"|achevé d'imprimer",
    re.IGNORECASE,
)
_COVER_RE = re.compile(
    r"page de garde|quatrième de couverture", re.IGNORECASE
)
_TOC_RE = re.compile(r"\bsommaire\b|table des matières", re.IGNORECASE)

#: Junk threshold: an excerpt scoring >= this is excluded from lessons.
_JUNK_THRESHOLD = 3


def _junk_score(text: Any) -> int:
    """Junk score of an excerpt (higher = more parasitic)."""
    t = str(text or "").strip()
    if not t:
        return 2  # empty slot, never a course item — but not "junk" either
    score = 0
    if _MESSAGING_RE.search(t):
        score += 3  # promo messaging alone disqualifies
    elif _URL_RE.search(t):
        score += 2
    if _CTA_RE.search(t):
        score += 2
    if _LEGAL_RE.search(t):
        score += 3
    if _COVER_RE.search(t):
        score += 4
    if _TOC_RE.search(t):
        score += 3
    if len(t) < 25:
        score += 2  # too thin to be a course item on its own
    return score


def is_junk_excerpt(text: Any) -> bool:
    """True when an excerpt is parasitic (promo/URL, legal, cover, TOC).

    Pure stdlib, never raises (weird input → False, i.e. kept).
    """
    try:
        return _junk_score(text) >= _JUNK_THRESHOLD
    except Exception:
        return False


def _norm_excerpt(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _dedupe_indices(texts: list[str]) -> list[int]:
    """Indices of first occurrences (exact normalized, then near-identical).

    Near-duplicates (difflib ratio ≥ 0.92 on whitespace-normalized text,
    similar lengths) keep the FIRST occurrence, order preserved. Bounded:
    beyond 80 texts only exact dedup runs (CPU guard).
    """
    seen: dict[str, int] = {}
    order: list[int] = []
    keys: list[str] = []
    for i, t in enumerate(texts):
        key = _norm_excerpt(t)
        if not key or key in seen:
            continue
        seen[key] = i
        order.append(i)
        keys.append(key)
    if len(order) <= 80:
        final: list[int] = []
        final_keys: list[str] = []
        for i, key in zip(order, keys):
            dupe = False
            for fk in final_keys:
                if abs(len(key) - len(fk)) > max(16, int(max(len(key), len(fk)) * 0.1)):
                    continue
                if difflib.SequenceMatcher(None, key, fk).ratio() >= 0.92:
                    dupe = True
                    break
            if not dupe:
                final.append(i)
                final_keys.append(key)
        return final
    return order


def dedupe_excerpts(texts: list[str]) -> list[str]:
    """Drop exact and near-identical excerpts (first kept, order preserved)."""
    if not texts:
        return []
    return [texts[i] for i in _dedupe_indices(list(texts))]


def select_lesson_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter lesson chunks: drop junk, prefer chapters/sections, dedupe.

    - Junk excerpts (promo, legal, cover, TOC) are excluded.
    - Chunks carrying chapter/section metadata rank before bare ones
      (cover pages lose when real content exists), stable order otherwise.
    - Near-identical texts are deduplicated (first kept).
    - GUARANTEE: non-empty input ⇒ non-empty output (all-filtered keeps
      the least-junk chunk); empty input ⇒ [] (callers' contract kept).
    Never raises.
    """
    try:
        if not chunks:
            return []
        scored = [
            (c, _junk_score(c.get("text") if isinstance(c, dict) else ""))
            for c in chunks
        ]
        kept = [c for c, s in scored if s < _JUNK_THRESHOLD]
        if kept:
            pool = kept
        else:
            best = min(range(len(scored)), key=lambda i: (scored[i][1], i))
            pool = [scored[best][0]]
        # Prefer chapters/sections over bare cover-page chunks.
        pool = sorted(
            pool,
            key=lambda c: (
                0
                if (
                    isinstance(c, dict)
                    and (str(c.get("chapter") or "").strip() or str(c.get("section") or "").strip())
                )
                else 1
            ),
        )
        texts = [
            c.get("text") if isinstance(c, dict) else "" for c in pool
        ]
        idx = _dedupe_indices([str(t or "") for t in texts])
        out = [pool[i] for i in idx]
        return out or pool[:1]
    except Exception:
        return list(chunks) if chunks else []


#: Minimal French stopwords so RAG filtering keeps topical keywords only.
#: Without this, 2-letter words like « en » match nearly every chunk and
#: unrelated excerpts (invoices, legal notices) leak into the lesson.
_NOTION_STOPWORDS = frozenset({
    "les", "des", "une", "est", "sont", "dans", "pour", "avec", "sur",
    "aux", "ces", "cette", "plus", "tout", "tous", "toute", "toutes",
    "comme", "entre", "sans", "sous", "par", "pas", "que", "qui",
    "dont", "leur", "leurs", "notre", "votre", "mais", "donc", "car",
    "the", "and", "for",
})


def _offline_header(kind_label: str) -> str:
    """Header marking deterministic offline content (honest fallback)."""
    return f"> {kind_label} {_OFFLINE_MENTION} (LLM indisponible).\n\n"


#: Fenced ```python blocks (closed only; untagged/other languages ignored).
_CODEBLOCK_RE = re.compile(
    r"^[ \t]*```[ \t]*python[ \t]*\r?$([\s\S]*?)^[ \t]*```[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)

#: Max validated blocks per course (CPU guard: ≤12 × 3 s).
_CODECHECK_MAX_BLOCKS = 12

#: Per-block sandbox timeout (seconds). Never exceeded (spec ceiling).
_CODECHECK_TIMEOUT_S = 3.0


def extract_python_blocks(content: str, max_blocks: int = _CODECHECK_MAX_BLOCKS) -> list[str]:
    """Extract closed ```python fence bodies (other languages ignored).

    Pure stdlib regex; unclosed fences are skipped. Capped at
    ``max_blocks`` (first ones win, document order).
    """
    if not content or not isinstance(content, str):
        return []
    return [m.group(1) for m in _CODEBLOCK_RE.finditer(content)][:max(0, int(max_blocks))]


def _verdict_for_block(index: int, result: RunResult, timeout: float) -> dict[str, Any]:
    """Short per-block verdict ({index, ok, error}) from a RunResult."""
    if result.blocked:
        lines = [ln for ln in (result.stderr or "").strip().splitlines() if ln.strip()]
        detail = lines[0] if lines else "politique de sécurité"
        return {"index": index, "ok": False, "error": f"bloqué-sécurité: {detail}"[:160]}
    if result.timed_out:
        return {"index": index, "ok": False, "error": f"timeout après {timeout:g}s"}
    if result.exit_code != 0:
        lines = [ln for ln in (result.stderr or "").strip().splitlines() if ln.strip()]
        detail = lines[-1] if lines else f"exit {result.exit_code}"
        return {"index": index, "ok": False, "error": detail[:160]}
    return {"index": index, "ok": True, "error": None}


def validate_lesson_codeblocks(
    blocks: list[str], timeout: float = _CODECHECK_TIMEOUT_S
) -> dict[str, Any]:
    """Validate lesson ```python blocks through the sandbox (sync wrapper).

    Each block runs via ``run_python(code, timeout=…)`` with safety ACTIVE
    (``skip_safety`` never set): verdicts ``{index, ok, error}`` with a
    short error summary (security-block / timeout / exit / message).
    ``timeout`` is clamped to 3 s (spec ceiling). Sync contexts call this
    directly; when a loop is already running (FastAPI route) the runs
    execute in a dedicated single thread with its own loop. Never raises
    on block failures (only wraps them); returns
    ``{"blocks": [...], "checked_at": iso}``.
    """
    timeout = min(float(timeout or _CODECHECK_TIMEOUT_S), _CODECHECK_TIMEOUT_S)
    codes = [b for b in (blocks or []) if isinstance(b, str) and b.strip()]

    async def _all() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, code in enumerate(codes):
            try:
                res = await run_python(code, timeout=timeout)
            except Exception as exc:  # RunnerError only (oversize); never student code
                out.append({"index": i, "ok": False, "error": f"runner: {exc}"[:160]})
                continue
            out.append(_verdict_for_block(i, res, timeout))
        return out

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        results = asyncio.run(_all())
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            results = pool.submit(lambda: asyncio.run(_all())).result()
    return {"blocks": results, "checked_at": _now_iso()}


#: Actionable remedy shown with every lesson generation failure.
_LLM_RETRY_HINT = (
    "Réessayez plus tard ou choisissez un modèle local (Ollama) "
    "dans Réglages — la bascule est automatique."
)

#: HTTP status embedded in ``OpenAIClientError`` messages
#: (« OpenAI API error 401: … » — the exception carries no status attr).
_API_ERROR_STATUS_RE = re.compile(r"\bAPI error (\d{3})\b")


def _chain(exc: BaseException) -> list[BaseException]:
    """Exception chain (cause/context), outermost first, cycle-safe."""
    out: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        nxt = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        cur = nxt if isinstance(nxt, BaseException) else None
    return out


def _code_for_status(status: int) -> str | None:
    """Stable code for an HTTP status (None = no opinion)."""
    if status == 401 or status == 404:
        return "model_unknown"  # bad key / model absent from catalog
    if status == 408 or 500 <= status <= 599:
        return "upstream_timeout"  # gateway-side failure, retry later
    if 400 <= status <= 499:
        return "generic"
    return None


def _classify_llm_error(exc: BaseException) -> str:
    """Stable error code for an LLM failure (never raises).

    Walks the chain root-cause first: typed HTTP status (``.status`` —
    ``SyncChatHTTPError``/``OllamaAPIError``), embedded status
    (``OpenAIClientError`` carries none — « OpenAI API error 401… »),
    transport errors by type name (``ConnectError``) or message (« Cannot
    connect… », « timed out… » — no hard httpx import), router
    « Modèle inconnu ». Falls back to ``generic``.
    """
    for err in reversed(_chain(exc)):
        status = getattr(err, "status", None)
        if isinstance(status, int):
            code = _code_for_status(status)
            if code is not None:
                return code
        name = type(err).__name__
        msg = str(err) or ""
        low = msg.lower()
        m = _API_ERROR_STATUS_RE.search(msg)
        if m:
            code = _code_for_status(int(m.group(1)))
            if code is not None:
                return code
        if name == "LLMRoutingError" and "inconnu" in low:
            return "model_unknown"
        if name == "ConnectError" or "cannot connect" in low:
            return "provider_unreachable"
        if (
            name in ("TimeoutError", "TimeoutException", "ConnectTimeout",
                     "ReadTimeout", "WriteTimeout", "PoolTimeout")
            or "timed out" in low
        ):
            return "upstream_timeout"
    return "generic"


class LessonDiscussionService:
    """Service for lesson-centred discussions (Feature 009, US1)."""

    def __init__(self, store: LibraryStore, tutor_service: Any | None = None) -> None:
        self.store = store
        self.tutor_service = tutor_service

    # ------------------------------------------------------------------
    # Discussion lifecycle
    # ------------------------------------------------------------------

    def get_or_create_discussion(self, path_step_id: str, learner_id: str) -> LessonDiscussion:
        """Return existing discussion for (path_step_id, learner_id) or create it.

        On first open the linked ``PathStep`` moves ``not_started`` → ``in_progress``.
        Raises ``KeyError`` if the path step does not exist.
        """
        discussion = self.store.get_or_create_lesson_discussion(path_step_id, learner_id)
        step = self.store.get_path_step(path_step_id)
        if step is not None and step.status == "not_started":
            self.store.update_path_step_status(step.id, "in_progress")
            # refresh discussion? status unchanged
        return discussion

    def get_discussion(self, discussion_id: str) -> dict[str, Any] | None:
        """Return full discussion payload or None if unknown.

        Payload: {discussion, messages, generated_contents, exercise_attempts}
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            return None
        messages = self.store.list_lesson_messages(discussion_id)
        # Convert sources in messages to dict form
        serialised_msgs = []
        for m in messages:
            serialised_msgs.append({
                "id": m.get("id"),
                "discussion_id": m.get("discussion_id"),
                "role": m.get("role"),
                "content": m.get("content"),
                "sources": [s.to_dict() if isinstance(s, SourceReference) else s for s in (m.get("sources") or [])],
                "created_at": m.get("created_at"),
            })
        contents = [c.to_dict() for c in self.store.list_generated_contents(discussion_id)]
        attempts = [a.to_dict() for a in self.store.list_exercise_attempts(discussion_id)]
        return {
            "discussion": disc.to_dict(),
            "messages": serialised_msgs,
            "generated_contents": contents,
            "exercise_attempts": attempts,
        }

    # ------------------------------------------------------------------
    # RAG filtered ask (FR-002 / FR-003)
    # ------------------------------------------------------------------

    def _resolve_notion(self, disc: Any) -> str:
        """Effective lesson notion — never a filename (root fix).

        Sanitizes ``disc.notion_id`` (leftover extension + server dedup
        suffix) so old rows created before the store-level fix are also
        repaired at generation time. When the sanitized notion merely
        repeats a book title of the subject (normalized comparison on both
        sides: extension, dedup suffix, separators, case), falls back to
        the step (concept) title instead.
        """
        notion = sanitize_notion_id(getattr(disc, "notion_id", "") or "")
        step = None
        try:
            step = self.store.get_path_step(getattr(disc, "path_step_id", "") or "")
        except Exception:
            step = None
        step_title = (step.title or "").strip() if step is not None else ""
        if not notion:
            return step_title or "notion"
        subject_id = getattr(disc, "subject_id", "") or ""
        if subject_id:
            try:
                book_keys = {
                    normalize_notion_key(b.title)
                    for b in self.store.list_books(subject_id)
                }
            except Exception:
                book_keys = set()
            book_keys.discard("")
            if normalize_notion_key(notion) in book_keys:
                if step_title and normalize_notion_key(step_title) not in book_keys:
                    return step_title
        return notion

    def _book_title(self, book_id: str) -> str:
        """Readable book title for a book id (never raises, never leaks ids)."""
        try:
            book = self.store.get_book(book_id)
        except Exception:
            return ""
        if book is None:
            return ""
        return (getattr(book, "title", "") or "").strip()

    def _llm_model(self) -> str | None:
        """Effective LLM model name behind the tutor_service hook.

        Reads ``tutor_service.config.tutor_model`` (the model
        ``generate_lesson_text`` actually sends). ``None`` when the hook
        path is unavailable — callers persist NULL (offline fallback).
        Never raises.
        """
        try:
            cfg = getattr(self.tutor_service, "config", None)
            name = getattr(cfg, "tutor_model", None)
            name = str(name).strip() if name is not None else ""
            return name or None
        except Exception:
            return None

    def _llm_provider(self) -> str | None:
        """Active LLM provider name behind the tutor_service hook.

        Reads ``tutor_service.config.llm_provider``. ``None`` when
        unknown (e.g. no tutor_service) — error events carry nulls
        rather than invented names. Never raises.
        """
        try:
            cfg = getattr(self.tutor_service, "config", None)
            name = getattr(cfg, "llm_provider", None)
            name = str(name).strip() if name is not None else ""
            return name or None
        except Exception:
            return None

    def _lesson_error_event(
        self, code: str, message: str, hint: str | None = None
    ) -> dict[str, Any]:
        """Structured SSE error event (Constitution VI — observability).

        ``error`` keeps the human-readable FR message (current frontend
        compat); ``code`` is stable for UI branching; ``provider``/``model``
        name the failing backend (null when unknown); ``hint`` is the
        actionable remedy. Never raises.
        """
        return {
            "error": message,
            "code": code,
            "provider": self._llm_provider(),
            "model": self._llm_model(),
            "hint": hint if hint else _LLM_RETRY_HINT,
        }

    def _generation_failure_message(self, code: str, step: str, exc: Any) -> str:
        """FR message naming provider + model + step for ``code``.

        ``step`` is « cours » or « réponse ». ``generic`` appends the raw
        technical detail (existing contract: the raw text stays visible).
        Never raises.
        """
        try:
            provider = self._llm_provider() or "le fournisseur configuré"
            model = self._llm_model() or "le modèle configuré"
            if code == "upstream_empty":
                return (
                    f"Le fournisseur « {provider} » a coupé la connexion "
                    f"sans répondre pour le modèle « {model} » pendant la "
                    f"génération {step} (0 contenu reçu)."
                )
            if code == "upstream_timeout":
                return (
                    f"Le fournisseur « {provider} » n'a pas répondu à temps "
                    f"pour le modèle « {model} » pendant la génération "
                    f"{step} (délai dépassé)."
                )
            if code == "model_unknown":
                return (
                    f"Modèle « {model} » inconnu du fournisseur « {provider} » "
                    f"pendant la génération {step}. Choisissez un modèle de "
                    f"la liste Réglages — la bascule est automatique."
                )
            if code == "provider_unreachable":
                return (
                    f"Fournisseur « {provider} » injoignable pour le modèle "
                    f"« {model} » pendant la génération {step} (connexion "
                    f"impossible)."
                )
            detail = str(exc).strip() if exc is not None else ""
            return (
                f"La génération {step} a échoué (fournisseur "
                f"« {provider} », modèle « {model} »)."
                + (f" Détail technique : {detail}" if detail else "")
            )
        except Exception:
            return f"La génération {step} a échoué."

    def _preflight_error_event(self, exc: Any) -> dict[str, Any]:
        """Structured event for a failed lesson pre-flight (duck-typed).

        Uses the preflight's own ``code``/message/``hint`` when present
        (``LessonPreflightError``), else maps like any LLM failure. Never
        raises, never imports the service layer (no cycle).
        """
        try:
            code = getattr(exc, "code", None) or _classify_llm_error(exc)
            message = str(exc).strip()
            hint = getattr(exc, "hint", None)
            if not message:
                message = self._generation_failure_message(code, "du cours", exc)
            return self._lesson_error_event(code, message, hint)
        except Exception:
            return {"error": str(exc) or "Génération impossible"}

    def _sources_footer(self, chunks: list[dict[str, Any]]) -> str:
        """Readable « Sources : … » footer (book TITLES, never raw ids).

        Resolves each chunk's ``book_id`` to its title via the store
        (:meth:`_book_title`). Unknown books are skipped silently — a raw
        id must never leak into rendered content.
        """
        titles: list[str] = []
        seen: set[str] = set()
        for c in chunks[:6]:
            bid = str(c.get("book_id", "") or "")
            if not bid or bid in seen:
                continue
            seen.add(bid)
            title = self._book_title(bid)
            if title and title not in titles:
                titles.append(title)
        if not titles:
            return ""
        return "Sources : " + ", ".join(titles) + "."

    def _notion_keywords(self, path_step_id: str) -> list[str]:
        """Keywords derived from the step title / activity_id for RAG filtering."""
        step = self.store.get_path_step(path_step_id)
        if step is None:
            return []
        title = (step.title or step.activity_id or "").lower()
        # Sanitize a leftover filename (extension + dedup suffix) so hex
        # junk never becomes a keyword.
        title = sanitize_notion_id(title) or title
        # split on non-alphanum, keep topical tokens (>=3 chars, no
        # stopwords): 2-letter words like « en » match nearly every chunk
        # and pull unrelated excerpts (invoices, legal notices) in.
        import re
        tokens = re.findall(r"[a-zàâéèêôû0-9]{3,}", title.lower())
        tokens = [t for t in tokens if t not in _NOTION_STOPWORDS]
        # fallback to title words
        if not tokens and title.strip():
            tokens = [w for w in title.split() if len(w) >= 3 and w not in _NOTION_STOPWORDS]
        return list(dict.fromkeys(tokens))  # dedup preserve order

    def _filtered_chunks(self, subject_id: str, keywords: list[str]) -> list[dict[str, Any]]:
        """Chunks filtered to those matching notion keywords (case-insensitive).

        Survivors go through :func:`select_lesson_chunks` (junk excluded,
        chapters preferred, near-dupes merged) so prompts AND rendered
        fallbacks never embed promos, legal notices, cover pages or
        tripled excerpts. Empty keyword matches still return [] (callers'
        contract preserved); the never-empty guarantee applies once chunks
        exist.

        RAG off (embeddings désactivés, sentinelles ""/disabled/none/off) :
        renvoie [] immédiatement — les chunks déjà vectorisés AVANT la
        bascule ne doivent PAS alimenter les extraits LLM des leçons
        (sinon citations livres + « Sources » malgré le toggle). Quand
        ``tutor_service`` est None, on n'a pas l'information RAG : on
        conserve le comportement historique (getattr → False, pas de crash).
        """
        if getattr(self.tutor_service, "is_embedding_disabled", False):
            return []
        if not subject_id:
            return []
        chunks = self.store.get_indexed_chunks(subject_id)
        if not keywords:
            return select_lesson_chunks(chunks[:10])
        kw_lower = [k.lower() for k in keywords]
        filtered = []
        for c in chunks:
            text = (c.get("text") or "").lower()
            chapter = (c.get("chapter") or "").lower()
            section = (c.get("section") or "").lower()
            hay = f"{text} {chapter} {section}"
            if any(k in hay for k in kw_lower):
                filtered.append(c)
        # If nothing matches, return empty (caller will surface message / empty sources)
        return select_lesson_chunks(filtered)

    def _sources_from_chunks(self, chunks: list[dict[str, Any]]) -> list[SourceReference]:
        seen: set[str] = set()
        sources: list[SourceReference] = []
        for c in chunks[:5]:
            bid = str(c.get("book_id", ""))
            if not bid or bid in seen:
                continue
            seen.add(bid)
            sources.append(SourceReference(
                book_id=bid,
                chapter=str(c.get("chapter") or ""),
                page=c.get("page"),
                excerpt=(c.get("text") or "")[:200],
                confidence=0.8,
            ))
        return sources

    def ask_notion(self, discussion_id: str, question: str, learner_id: str) -> dict[str, Any]:
        """Ask a question scoped to the lesson notion (FR-003).

        Uses RAG filtered by notion keywords. Persists both user and assistant
        messages. Returns {answer, sources}.
        Raises KeyError if discussion unknown, ValueError if learner mismatch.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        if not question.strip():
            raise ValueError("question required")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        sources = self._sources_from_chunks(chunks)

        # Persist user message
        self.store.add_lesson_message(discussion_id, "user", question.strip(), sources=[])

        notion = self._resolve_notion(disc)
        # Try the sync LLM hook first (short targeted answer grounded in the
        # filtered excerpts, plus provider thinking when supplied); on
        # failure/None ONLY, keep the deterministic echo as the offline
        # fallback (existing format preserved, thinking "").
        answer, thinking = self._try_llm_text_with_thinking(
            "lesson_answer", notion, chunks, question=question.strip()
        )
        is_fallback = False
        if not answer:
            is_fallback = True
            thinking = ""
            answer = f"Réponse sur « {notion} » : {question.strip()}"
            if sources:
                # Readable source (book title via store), never a raw id.
                source_title = self._book_title(sources[0].book_id)
                if source_title:
                    answer += f"\n\nSource : {source_title}"
        if not isinstance(thinking, str):
            thinking = ""
        self.store.add_lesson_message(discussion_id, "assistant", answer, sources=sources)
        return {
            "answer": answer,
            "sources": [s.to_dict() for s in sources],
            "fallback": is_fallback,
            "thinking": thinking,
        }

    def iter_ask_tokens(
        self, discussion_id: str, question: str, learner_id: str | None = None
    ):
        """Sync generator yielding front-ready SSE dicts for a lesson ask.

        Order: ``{"thinking": ...}``* (when supplied), ``{"delta": ...}``*,
        then ``{"done": {"done": True, "fallback": False, ...}}``. Provider
        failure yields ``{"error": ...}`` (explicit, never silent) and
        persists nothing — like :meth:`stream_course`. Invalid discussion
        / learner / question also yields ``{"error"}`` (the route runs its
        404/403/400 pre-flight first). Persists user + assistant messages
        exactly like :meth:`ask_notion` on success. Sync generator — never
        touches asyncio, zero fastapi in tutor/.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            yield {"error": "Discussion inconnue"}
            return
        if learner_id is not None and disc.learner_id != learner_id:
            yield {"error": "learner_id mismatch"}
            return
        if not (question or "").strip():
            yield {"error": "question requise"}
            return
        question = question.strip()
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        sources = self._sources_from_chunks(chunks)
        notion = self._resolve_notion(disc)
        # Fail-fast pre-flight (Constitution VI) — sync generator, never
        # touches asyncio: only a sync-capable preflight runs here; an
        # async one (TutorService.preflight_lesson_model) is covered by
        # stream_course. Missing method (service=None, fakes) ⇒ skip,
        # current behavior unchanged.
        _preflight = (
            getattr(self.tutor_service, "preflight_lesson_model", None)
            if self.tutor_service is not None
            else None
        )
        if callable(_preflight) and not asyncio.iscoroutinefunction(_preflight):
            try:
                _preflight()
            except Exception as exc:
                yield self._preflight_error_event(exc)
                return
        self.store.add_lesson_message(discussion_id, "user", question, sources=[])
        hook: Any = (
            getattr(self.tutor_service, "generate_lesson_text_stream", None)
            if self.tutor_service is not None
            else None
        )
        if not callable(hook):
            yield self._lesson_error_event(
                "llm_unavailable", "LLM indisponible pour la réponse"
            )
            return
        excerpts = [(c.get("text") or "")[:500] for c in chunks[:6]]
        parts: list[str] = []
        thinkings: list[str] = []
        try:
            for kind, text in hook("lesson_answer", notion, excerpts, question=question):
                if kind == "thinking":
                    if isinstance(text, str) and text.strip():
                        thinkings.append(text)
                        yield {"thinking": text}
                elif kind == "token":
                    if isinstance(text, str) and text:
                        parts.append(text)
                        yield {"delta": text}
        except Exception as exc:
            code = _classify_llm_error(exc)
            yield self._lesson_error_event(
                code, self._generation_failure_message(code, "de la réponse", exc)
            )
            return
        answer = "".join(parts)
        if not answer.strip():
            yield self._lesson_error_event(
                "upstream_empty",
                self._generation_failure_message("upstream_empty", "de la réponse", None),
            )
            return
        self.store.add_lesson_message(discussion_id, "assistant", answer, sources=sources)
        yield {
            "done": True,
            "fallback": False,
            "thinking": "".join(thinkings),
            "sources": [s.to_dict() for s in sources],
        }

    # ------------------------------------------------------------------
    # Génération cours & synthèse (FR-004 / FR-005 / FR-015 — US2)
    # ------------------------------------------------------------------

    def generate_course(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Génère un cours complet 800–1200 mots ancré dans les sources (FR-004/FR-015).

        RAG via ``get_indexed_chunks`` filtré par notion. Persiste en
        ``GeneratedLessonContent`` ``kind=lesson_course``. Retourne le dict
        sérialisé du contenu généré.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        sources = self._sources_from_chunks(chunks)
        confidence = 0.85 if sources else 0.0
        notion = self._resolve_notion(disc)
        # Try LLM via tutor_service if available, else honest offline fallback.
        content = self._course_fallback(notion, chunks, keywords)
        is_fallback = True
        # If tutor_service provides sync generate, attempt (best-effort)
        if self.tutor_service is not None:
            try:
                maybe = self._try_llm_course(notion, chunks)
                if maybe and _word_count(maybe) >= 800:
                    content = maybe
                    is_fallback = False
            except Exception:
                pass
        if is_fallback:
            content = _offline_header("Cours") + content
        # Ensure 800–1200 words
        content = _ensure_word_range(content, 800, 1200, notion, chunks)
        # Sandbox codecheck (course only): validate ```python blocks; the
        # content is ALWAYS kept verbatim (never silently dropped) — only
        # the report rides along (None when no block to check).
        code_blocks = extract_python_blocks(content)
        validation = validate_lesson_codeblocks(code_blocks) if code_blocks else None
        obj = self.store.add_generated_content(
            discussion_id, "lesson_course", content, sources=sources, confidence=confidence,
            model=self._llm_model() if not is_fallback else None,
            validation=validation,
        )
        result = obj.to_dict()
        result["fallback"] = is_fallback
        return result

    async def stream_course(
        self, discussion_id: str, learner_id: str | None = None
    ) -> Any:
        """Stream a full course as ``delta``/``done``/``error`` events (SSE backend).

        Same grounding as :meth:`generate_course` (sanitized notion, filtered
        chunks, sources, confidence). Live LLM deltas are yielded as
        ``{"delta": text}``; when the stream completes, the full text is
        persisted like ``generate_course`` and ``{"done": True, "fallback":
        False}`` is yielded. On LLM failure (missing hook, exception, stall
        beyond ``LESSON_STREAM_TIMEOUT_S`` per chunk, empty answer),
        ``{"error": ...}`` is yielded INSTEAD and nothing is persisted —
        never generic disguised text.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        sources = self._sources_from_chunks(chunks)
        confidence = 0.85 if sources else 0.0
        notion = self._resolve_notion(disc)
        # Fail-fast pre-flight (Constitution VI) : unknown model or
        # unreachable provider surfaces NOW with code model_unknown /
        # provider_unreachable — no 91 s of silence. Missing method
        # (service=None, fakes, offline paths) ⇒ skip, current behavior
        # unchanged (llm_unavailable below, never blocks offline fallback).
        _preflight = (
            getattr(self.tutor_service, "preflight_lesson_model", None)
            if self.tutor_service is not None
            else None
        )
        if callable(_preflight):
            try:
                await _preflight()
            except Exception as exc:
                yield self._preflight_error_event(exc)
                return
        streamer = (
            getattr(self.tutor_service, "stream_lesson_text", None)
            if self.tutor_service is not None
            else None
        )
        if not callable(streamer):
            yield self._lesson_error_event(
                "llm_unavailable", "LLM indisponible pour la génération du cours"
            )
            return
        excerpts = [(c.get("text") or "")[:500] for c in chunks[:6]]
        parts: list[str] = []
        got_any = False  # trace every received delta: clean EOF with 0
        # content and a truly-empty answer are observably identical
        # gateway-side — both yield upstream_empty with the same message.
        try:
            it: Any = streamer("lesson_course", notion, excerpts)
            while True:
                try:
                    delta = await asyncio.wait_for(
                        it.__anext__(), timeout=LESSON_STREAM_TIMEOUT_S
                    )
                except StopAsyncIteration:
                    break
                if delta:
                    got_any = True
                    parts.append(delta)
                    yield {"delta": delta}
        except asyncio.TimeoutError:
            yield self._lesson_error_event(
                "upstream_timeout",
                f"Le fournisseur « {self._llm_provider() or 'le fournisseur configuré'} » "
                f"n'a pas répondu à temps pour le modèle "
                f"« {self._llm_model() or 'le modèle configuré'} » pendant la "
                f"génération du cours (délai {int(LESSON_STREAM_TIMEOUT_S)} s).",
            )
            return
        except Exception as exc:
            code = _classify_llm_error(exc)
            yield self._lesson_error_event(
                code, self._generation_failure_message(code, "du cours", exc)
            )
            return
        content = "".join(parts)
        if not content.strip():
            yield self._lesson_error_event(
                "upstream_empty",
                self._generation_failure_message("upstream_empty", "du cours", None),
            )
            return
        self.store.add_generated_content(
            discussion_id, "lesson_course", content, sources=sources, confidence=confidence,
            model=self._llm_model(),
        )
        yield {"done": True, "fallback": False}

    def generate_summary(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Génère une synthèse 150–250 mots (FR-005/FR-015).

        Si un cours existe déjà, résume le cours ; sinon RAG direct depuis les
        sources filtrées. Persiste ``kind=lesson_summary``.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        notion = self._resolve_notion(disc)
        # Check for existing course
        existing = [c for c in self.store.list_generated_contents(discussion_id) if c.kind == "lesson_course"]
        sources: list[SourceReference]
        confidence: float
        is_fallback = True
        if existing:
            course = existing[-1]
            sources = list(course.sources) if course.sources else []
            confidence = float(course.confidence) if course.confidence else 0.8
            chunks = self._filtered_chunks(disc.subject_id, keywords)
            if not sources:
                sources = self._sources_from_chunks(chunks)
                confidence = 0.8 if sources else 0.0
            # Readable titles for the summary head (never raw ids).
            course_titles: list[str] = []
            for s in sources:
                t = self._book_title(s.book_id)
                if t and t not in course_titles:
                    course_titles.append(t)
            # Derive summary from course — pad using course content itself + chunks
            content = self._summary_from_course(notion, course.content, source_titles=course_titles)
            if self.tutor_service is not None:
                try:
                    maybe = self._try_llm_summary(notion, chunks)
                    if maybe and 150 <= _word_count(maybe) <= 250:
                        content = maybe
                        is_fallback = False
                except Exception:
                    pass
            if is_fallback:
                content = _offline_header("Synthèse") + content
            content = _ensure_word_range(content, 150, 250, notion, chunks, course_content=course.content)
        else:
            chunks = self._filtered_chunks(disc.subject_id, keywords)
            sources = self._sources_from_chunks(chunks)
            confidence = 0.8 if sources else 0.0
            content = self._summary_fallback(notion, chunks, keywords)
            if self.tutor_service is not None:
                try:
                    maybe = self._try_llm_summary(notion, chunks)
                    if maybe and 150 <= _word_count(maybe) <= 250:
                        content = maybe
                        is_fallback = False
                except Exception:
                    pass
            if is_fallback:
                content = _offline_header("Synthèse") + content
            content = _ensure_word_range(content, 150, 250, notion, chunks)
        obj = self.store.add_generated_content(
            discussion_id, "lesson_summary", content, sources=sources, confidence=confidence,
            model=self._llm_model() if not is_fallback else None,
        )
        result = obj.to_dict()
        result["fallback"] = is_fallback
        return result

    # -- deterministic helpers ------------------------------------------------

    def _course_fallback(self, notion: str, chunks: list[dict[str, Any]], keywords: list[str]) -> str:
        # Use real chunk texts, cycling if few chunks, for lexical diversity
        if chunks:
            excerpt_lines = []
            for c in chunks[:6]:
                txt = (c.get("text", "") or "").strip()
                if txt:
                    # keep up to 200 chars per excerpt to preserve diversity
                    excerpt_lines.append(f"- {txt[:200]}")
            excerpts = "\n".join(excerpt_lines) if excerpt_lines else f"- Contenu sur {notion} : notions voisines et exemples contextuels."
        else:
            excerpts = f"- Contenu sur {notion} : notions voisines et exemples contextuels (aucun extrait indexé)."
        base = (
            f"# Cours : {notion}\n\n"
            f"## 1. Définition\n"
            f"La notion « {notion} » désigne un concept central. Elle se définit comme l'élément de base permettant de structurer la compréhension. "
            f"Dans le contexte de cette leçon, {notion} intervient dès les premiers exemples et conditionne la suite de l'apprentissage. "
            f"Les sources suivantes ancrent la définition :\n{excerpts}\n\n"
            f"## 2. Pourquoi c'est important\n"
            f"Comprendre {notion} permet de lire, écrire et raisonner sur des exemples concrets. Sans {notion}, les constructions plus avancées restent fragiles. "
            f"C'est une brique de base que l'on retrouve dans chaque programme et chaque exercice de validation.\n\n"
            f"## 3. Exemples détaillés\n"
            f"Exemple 1 — déclaration : `x = 3` associe la valeur 3 à la variable x. Exemple 2 — réaffectation : `x = x + 1`. "
            f"Exemple 3 — usage dans une boucle : `for i in range(5): print(i)` où i parcourt les valeurs. "
            f"Chaque exemple illustre la portée, la durée de vie et la mutabilité liée à {notion}. "
            f"{self._sources_footer(chunks) or 'Sources : extraits rattachés à cette leçon.'}\n\n"
            f"## 4. Cas d'usage\n"
            f"On utilise {notion} pour stocker un état, compter des itérations, mémoriser un résultat intermédiaire, ou paramétrer une fonction. "
            f"Cas d'usage typique : calcul d'une somme, suivi d'un score, configuration d'un algorithme.\n\n"
            f"## 5. Bonnes pratiques et erreurs courantes\n"
            f"Nommer clairement, éviter les noms à une lettre hors boucle, initialiser avant usage, ne pas confondre affectation et comparaison. "
            f"Erreur fréquente : utiliser une variable non définie ou écraser une valeur utile.\n\n"
            f"## 6. Points clés à retenir\n"
            f"Retenez la définition, trois exemples, deux cas d'usage et deux erreurs à éviter autour de {notion}. "
            f"Ces points forment le socle évalué lors des exercices.\n\n"
            f"## 7. Pour aller plus loin\n"
            f"Relisez les extraits sources, refaites les exemples à la main, puis testez votre compréhension avec la synthèse et les exercices.\n"
        )
        return base

    def _summary_from_course(
        self,
        notion: str,
        course_content: str,
        source_titles: list[str] | None = None,
    ) -> str:
        words = course_content.split()
        # Take first ~120 words then reframe as bullet summary, keep varied
        head = " ".join(words[:120])
        # Extract a few distinct sentences from course for diversity
        import re
        sents = re.split(r"(?<=[.!?])\s+", course_content.strip())
        varied = " ".join(s.strip() for s in sents[2:5] if s.strip())[:300]
        # Titles up front (never raw ids): the tail may be trimmed to fit
        # the 150–250 word range, the head always survives.
        titles_line = ""
        if source_titles:
            titles_line = "Sources : " + ", ".join(source_titles) + ".\n\n"
        return (
            f"# Synthèse : {notion}\n\n"
            f"{titles_line}"
            f"Points clés : {head}\n\n"
            f"Éléments repris du cours : {varied}\n\n"
            f"- Définition : {notion} est la brique de base vue en cours.\n"
            f"- Exemples : déclaration, réaffectation, usage en boucle.\n"
            f"- Cas d'usage : état, compteur, paramètre.\n"
            f"- Erreurs : nom ambigu, usage avant initialisation.\n"
            f"Cette synthèse reprend le cours sans le remplacer ; relisez le cours complet pour les détails et sources.\n"
        )

    def _summary_fallback(self, notion: str, chunks: list[dict[str, Any]], keywords: list[str]) -> str:
        if chunks:
            excerpts = " ".join((c.get("text", "") or "").strip()[:120] for c in chunks[:4] if (c.get("text") or "").strip())
            if not excerpts:
                excerpts = f"contenu sur {notion}"
        else:
            excerpts = f"contenu sur {notion}"
        # Build from real excerpts rather than fixed filler
        footer = self._sources_footer(chunks)
        return (
            f"# Synthèse : {notion}\n\n"
            f"Résumé concis de « {notion} » directement depuis les sources : {excerpts}. "
            f"Points clés : définition, exemples de déclaration et d'usage, cas d'usage comme compteur ou stockage d'état, "
            f"et erreurs courantes à éviter (nommage, initialisation). "
            f"Sources mobilisées : {excerpts[:200]}. "
            + (f"{footer} " if footer else "")
            + f"Cette synthèse permet de raviver la mémoire sans relire le cours complet.\n"
        )

    def _try_llm_text(
        self,
        kind: str,
        notion: str,
        chunks: list[dict[str, Any]],
        question: str | None = None,
    ) -> str | None:
        """Best-effort sync LLM call through an optional tutor_service hook.

        The hook is ``tutor_service.generate_lesson_text(kind, notion,
        excerpts, question=...)`` (sync, returns ``str``). Missing hook,
        failing hook or empty result → ``None`` (the caller uses the honest
        offline fallback). ``TutorService.ask`` is async and is never
        attempted from this sync path.
        """
        if self.tutor_service is None:
            return None
        fn = getattr(self.tutor_service, "generate_lesson_text", None)
        if not callable(fn):
            return None
        try:
            excerpts = [(c.get("text") or "")[:500] for c in chunks[:6]]
            if question is None:
                result = fn(kind, notion, excerpts)
            else:
                result = fn(kind, notion, excerpts, question=question)
        except Exception:
            return None
        if isinstance(result, str) and result.strip():
            return result
        return None

    def _try_llm_text_with_thinking(
        self,
        kind: str,
        notion: str,
        chunks: list[dict[str, Any]],
        question: str | None = None,
    ) -> tuple[str | None, str]:
        """Best-effort sync LLM call returning ``(text, thinking)``.

        Same hook as :meth:`_try_llm_text` but requests provider reasoning
        via ``return_thinking=True``. Hooks ignoring that kwarg (TypeError)
        fall back to the plain call with ``""`` thinking. Any failure →
        ``(None, "")`` (caller uses the honest offline fallback).
        """
        if self.tutor_service is None:
            return None, ""
        fn = getattr(self.tutor_service, "generate_lesson_text", None)
        if not callable(fn):
            return None, ""
        try:
            excerpts = [(c.get("text") or "")[:500] for c in chunks[:6]]
            if question is None:
                result = fn(kind, notion, excerpts, return_thinking=True)
            else:
                result = fn(kind, notion, excerpts, question=question, return_thinking=True)
        except TypeError:
            # Legacy/custom hook without the kwarg: plain call, no thinking.
            try:
                plain_excerpts = [(c.get("text") or "")[:500] for c in chunks[:6]]
                if question is None:
                    result = fn(kind, notion, plain_excerpts)
                else:
                    result = fn(kind, notion, plain_excerpts, question=question)
            except Exception:
                return None, ""
            if isinstance(result, str) and result.strip():
                return result, ""
            if isinstance(result, tuple) and result and isinstance(result[0], str) and result[0].strip():
                return result[0], ""
            return None, ""
        except Exception:
            return None, ""
        if isinstance(result, tuple) and len(result) >= 1:
            text, thinking = result[0], (result[1] if len(result) > 1 else "")
            if isinstance(text, str) and text.strip():
                return text, (thinking if isinstance(thinking, str) else "")
            return None, ""
        if isinstance(result, str) and result.strip():
            return result, ""
        return None, ""

    def _try_llm_course(self, notion: str, chunks: list[dict[str, Any]]) -> str | None:
        # Best-effort sync LLM call if tutor_service exposes the sync hook.
        return self._try_llm_text("lesson_course", notion, chunks)

    def _try_llm_summary(self, notion: str, chunks: list[dict[str, Any]]) -> str | None:
        # Best-effort sync LLM call if tutor_service exposes the sync hook.
        return self._try_llm_text("lesson_summary", notion, chunks)

    # ------------------------------------------------------------------
    # Exercices (FR-006 / FR-007 / FR-008 — US3)
    # ------------------------------------------------------------------

    def _question_types_for_subject(self, subject_id: str) -> list[str]:
        """Types adaptés au PedagogicalTemplate de la matière (FR-006)."""
        if not subject_id:
            return ["mcq", "open", "true_false"]
        profile = self.store.get_subject_profile(subject_id)
        if profile is None:
            return ["mcq", "open", "true_false"]
        # Prefer template activities if template_id set
        acts: list[str] = []
        if profile.template_id:
            try:
                for t in self.store.list_pedagogical_templates():
                    if t.id == profile.template_id:
                        acts = list(t.activities)
                        break
            except Exception:
                acts = []
        if not acts:
            acts = list(profile.activities or [])
        if not acts:
            return ["mcq", "open", "true_false"]
        # map activities keywords to question types
        mapping: list[tuple[str, str]] = [
            ("code à trous", "code_fill"),
            ("compléter", "code_fill"),
            ("parsons", "reorder"),
            ("réordonner", "reorder"),
            ("débogage", "debug"),
            ("qcm", "mcq"),
            ("quiz", "mcq"),
            ("vrai", "true_false"),
            ("vrai/faux", "true_false"),
            ("problème", "open"),
            ("exemples résolus", "open"),
            ("schéma", "open"),
            ("démonstration", "open"),
        ]
        types: list[str] = []
        for act in acts:
            al = act.lower()
            for kw, tp in mapping:
                if kw in al and tp not in types:
                    types.append(tp)
        if not types:
            return ["mcq", "open", "true_false"]
        # Ensure diversity: add defaults if only one type
        for d in ["mcq", "open", "true_false"]:
            if d not in types and len(types) < 3:
                types.append(d)
        return types

    def generate_exercises(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Génère 3–5 exercices ciblés sur la notion (FR-006).

        Persiste un ``LessonExerciseAttempt`` avec ``score=0`` et retourne
        le dict sérialisé. Chaque appel régénère de nouvelles questions
        (évite apprentissage par cœur).
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        notion = self._resolve_notion(disc)
        types = self._question_types_for_subject(disc.subject_id)
        # 3–5 questions — deterministic 4 but vary between 3-5 on regeneration
        # Use count of existing attempts to rotate between 3,4,5
        existing = len(self.store.list_exercise_attempts(discussion_id))
        # cycle 4,5,3,4 ...
        cycle = [4, 5, 3]
        n = cycle[existing % len(cycle)]
        n = max(3, min(5, n))
        questions: list[dict[str, Any]] = []
        import uuid as _uuid
        for i in range(n):
            qtype = types[i % len(types)]
            qid = _uuid.uuid4().hex[:8]
            excerpt = (chunks[i % len(chunks)].get("text", "")[:80] if chunks else f"contenu sur {notion}")
            if qtype == "mcq":
                correct = f"Réponse A sur {notion}"
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} (QCM) — {excerpt} : quelle affirmation est correcte ?",
                    "options": [correct, f"Réponse B sur {notion}", f"Réponse C sur {notion}"],
                    "answer": correct,
                    "explanation": f"La bonne réponse est '{correct}' d'après les sources sur {notion}.",
                })
            elif qtype == "true_false":
                ans = "true" if i % 2 == 0 else "false"
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} (Vrai/Faux) — « {excerpt} » est-ce vrai ?",
                    "answer": ans,
                    "explanation": f"Réponse attendue : {ans}. Source : {excerpt[:40]}",
                })
            elif qtype in ("code_fill", "reorder", "debug"):
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} ({qtype}) — Complétez le code lié à {notion} : `x = ___` (exemple : {excerpt[:30]})",
                    "answer": "x = 1",
                    "explanation": f"Attendu : x = 1 (exemple sur {notion}).",
                })
            else:
                # open / generic
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} (ouverte) — Expliquez {notion} avec un exemple tiré de : {excerpt}",
                    "answer": f"explication sur {notion}",
                    "explanation": f"On attend une explication de {notion} avec exemple.",
                })
        attempt = self.store.add_exercise_attempt(discussion_id, questions, answers=[], score=0.0, feedback="", passed=False)
        return attempt.to_dict()

    def submit_exercises(self, discussion_id: str, attempt_id: str, answers: dict[str, Any], learner_id: str | None = None) -> dict[str, Any]:
        """Évalue les réponses, calcule le score et met à jour le statut (FR-007/FR-008).

        ``answers`` : mapping ``question_id -> réponse donnée`` (str).
        Retourne le dict de l'attempt mis à jour (avec score/passed/feedback).
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        attempt = self.store.get_exercise_attempt(attempt_id)
        if attempt is None or attempt.discussion_id != discussion_id:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        questions = attempt.questions or []
        total = len(questions) if questions else 0
        if total == 0:
            raise ValueError("attempt has no questions")
        correct = 0
        per_q_feedback: list[dict[str, Any]] = []
        normalised_answers: list[dict[str, Any]] = []
        for q in questions:
            qid = str(q.get("id", ""))
            expected = str(q.get("answer", "")).strip()
            given_raw = answers.get(qid, "") if isinstance(answers, dict) else ""
            given = str(given_raw).strip() if given_raw is not None else ""
            # case-insensitive comparison, trimmed
            is_correct = given.lower() == expected.lower() and given != ""
            # For mcq, also allow matching option text case-insensitively
            if is_correct:
                correct += 1
            normalised_answers.append({"question_id": qid, "given": given, "correct": is_correct, "expected": expected})
            per_q_feedback.append({
                "question_id": qid,
                "type": q.get("type", ""),
                "statement": q.get("statement", ""),
                "given": given,
                "expected": expected,
                "correct": is_correct,
                "explanation": q.get("explanation", ""),
            })
        score = correct / total if total else 0.0
        passed = score >= 0.6
        feedback_str = __import__("json").dumps(per_q_feedback, ensure_ascii=False)
        # Update row via direct SQL
        import json as _json
        self.store._conn.execute(
            "UPDATE lesson_exercise_attempts SET answers = ?, score = ?, feedback = ?, passed = ? WHERE id = ?",
            (_json.dumps(normalised_answers, ensure_ascii=False), float(score), feedback_str, int(passed), attempt_id),
        )
        self.store._conn.commit()
        # Update path step status
        if passed:
            try:
                self.store.update_path_step_status(disc.path_step_id, "completed")
            except Exception:
                pass
        else:
            # ensure stays in_progress (do not revert completed)
            step = self.store.get_path_step(disc.path_step_id)
            if step is not None and step.status == "not_started":
                try:
                    self.store.update_path_step_status(disc.path_step_id, "in_progress")
                except Exception:
                    pass
        updated = self.store.get_exercise_attempt(attempt_id)
        assert updated is not None
        d = updated.to_dict()
        # Expose per-question feedback for API/UI convenience
        d["per_question"] = per_q_feedback
        d["correct_count"] = correct
        d["total"] = total
        return d

    def complete_manual(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Force le passage à ``completed`` quel que soit le score (FR-008)."""
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        self.store.update_path_step_status(disc.path_step_id, "completed")
        step = self.store.get_path_step(disc.path_step_id)
        return {"status": step.status if step else "completed", "path_step_id": disc.path_step_id}


def _word_count(text: str) -> int:
    return len(text.split())


def _split_blocks(text: str) -> list[str]:
    """Split lesson text into atomic blocks (never split further).

    Paragraphs (blank-line separated), contiguous `|` table lines merged
    into ONE block, and ``` fences kept whole (an unclosed fence at the
    end becomes its own block — callers re-close it).
    """
    if not text or not text.strip():
        return []
    blocks: list[str] = []
    buf: list[str] = []
    table: list[str] = []
    in_fence = False

    def flush_buf() -> None:
        if buf:
            chunk = "\n".join(buf).strip()
            if chunk:
                blocks.append(chunk)
            buf.clear()

    def flush_table() -> None:
        if table:
            blocks.append("\n".join(table))
            table.clear()

    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            if in_fence:
                buf.append(line)
                flush_buf()
                in_fence = False
            else:
                flush_buf()
                flush_table()
                buf.append(line)
                in_fence = True
            continue
        if in_fence:
            buf.append(line)
            continue
        if not s:
            flush_table()
            flush_buf()
            continue
        if s.startswith("|"):
            flush_buf()
            table.append(line)
            continue
        flush_table()
        buf.append(line)
    if in_fence:
        # Unclosed fence: keep as one block, callers re-close it.
        flush_buf()
    else:
        flush_table()
        flush_buf()
    return blocks


def _close_open_fence(text: str) -> str:
    """Append a closing fence when ``` count is odd (orphan fence)."""
    if text.count("```") % 2 == 1:
        return text.rstrip() + "\n```"
    return text


def _truncate_to_blocks(text: str, high: int) -> str:
    """Keep whole blocks up to *high* words (never cut intra-block).

    Accumulates entire blocks while they fit; a single oversize block is
    kept whole rather than cut (documented best-effort: bounds are met
    "au mieux", high wins over low on coarse granularity). Re-closes an
    orphan trailing fence.
    """
    blocks = _split_blocks(text)
    out: list[str] = []
    n = 0
    for b in blocks:
        w = _word_count(b)
        if n + w <= high or not out:
            out.append(b)
            n += w
        else:
            break
    return _close_open_fence("\n\n".join(out))


def _norm_block(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _pad_to_low(text: str, low: int, pool: list[str]) -> str:
    """Pad with pool blocks up to *low* words (deduped first, cycled after).

    Phase 1 adds only blocks not already present (normalized substring);
    phase 2 cycles (assumed repetition to honor the min bound). Re-closes
    an orphan trailing fence. Empty pool ⇒ text unchanged.
    """
    if not pool:
        return text
    full = _norm_block(text)
    for cand in pool:
        if _word_count(text) >= low:
            break
        nc = _norm_block(cand)
        if nc and nc not in full:
            text += "\n\n" + cand.strip()
            full = _norm_block(text)
    idx = 0
    while _word_count(text) < low:
        text += "\n\n" + pool[idx % len(pool)].strip()
        idx += 1
        if idx > 500:  # safety
            break
    return _close_open_fence(text)


def _pad_for_diversity(text: str, high: int, pool: list[str], floor: int = 100) -> str:
    """Add distinct unseen pool blocks to lift lexical diversity (< high).

    Single pass, no repetition (diversity must not blow the max bound).
    """
    if not pool:
        return text
    full = _norm_block(text)
    for cand in pool:
        words = text.split()
        if len(set(w.lower() for w in words)) >= floor:
            break
        if _word_count(text) >= high:
            break
        nc = _norm_block(cand)
        if nc and nc not in full:
            text += "\n\n" + cand.strip()
            full = _norm_block(text)
    return _close_open_fence(text)


def _build_padding_pool(notion: str, chunks: list[dict[str, Any]], course_content: str | None = None) -> list[str]:
    """Pool of varied excerpts for padding — real chunk texts preferred."""
    pool: list[str] = []
    if course_content:
        # split course into sentences/paragraphs for varied reuse
        import re
        sents = re.split(r"(?<=[.!?])\s+", course_content.strip())
        for s in sents:
            s = s.strip()
            if len(s.split()) >= 6:
                pool.append(s)
        if pool:
            return pool
    if chunks:
        for c in chunks:
            t = (c.get("text") or "").strip()
            if t:
                # keep whole chunk but split if very long
                if len(t) > 400:
                    # split into ~80 char pieces for variety
                    import re
                    parts = re.split(r"(?<=[.!?])\s+", t)
                    for p in parts:
                        p = p.strip()
                        if len(p.split()) >= 5:
                            pool.append(p)
                        if len(pool) >= 12:
                            break
                else:
                    pool.append(t)
            if len(pool) >= 12:
                break
    if pool:
        return pool
    # fallback — varied notion sentences (not a single fixed phrase)
    return [
        f"Approfondissement de {notion} : mécanismes internes, illustrations concrètes et liens avec les chapitres connexes.",
        f"Exercice guidé sur {notion} : identifier les éléments clés, formuler une hypothèse et la vérifier par un exemple exécutable.",
        f"Point méthodologique autour de {notion} : structurer la démarche, choisir les bons outils et éviter les confusions fréquentes.",
        f"Application pratique de {notion} : étude de cas, variantes d'implémentation et interprétation des résultats observés.",
        f"Synthèse intermédiaire sur {notion} : articulation avec les notions voisines, mise en perspective et repères pour la révision.",
        f"Retour sur {notion} : erreurs typiques, stratégies de débogage et bonnes pratiques de nommage et de test.",
        f"Extension sur {notion} : généralisation, limites et pistes pour aller plus loin dans le chapitre suivant.",
    ]


def _ensure_word_range(text: str, low: int, high: int, notion: str, chunks: list[dict[str, Any]], course_content: str | None = None) -> str:
    # Pool hygiene: fence fragments would render as orphan fences — the
    # final re-close keeps counts even, but whole fences stay whole only
    # when kept out of sentence-split padding.
    pool = [p for p in _build_padding_pool(notion, chunks, course_content) if "```" not in p]
    cnt = _word_count(text)
    if low <= cnt <= high:
        # lexical diversity guard: ensure at least 100 distinct tokens when padded range is large
        if low >= 800:
            words = text.split()
            if len(set(w.lower() for w in words)) < 100 and chunks:
                # distinct unseen blocks only (never blow the max bound)
                text = _pad_for_diversity(text, high, pool)
                if _word_count(text) > high:
                    text = _truncate_to_blocks(text, high)
        return text
    if cnt < low:
        # pad with whole blocks (deduped first, cycled only if needed)
        text = _pad_to_low(text, low, pool)
        if _word_count(text) > high:
            text = _truncate_to_blocks(text, high)
        # final diversity check for large targets
        if low >= 800:
            uniq = len(set(w.lower() for w in text.split()))
            if uniq < 100:
                text = _pad_for_diversity(text, high, pool)
                if _word_count(text) > high:
                    text = _truncate_to_blocks(text, high)
        return text
    # cnt > high: whole blocks only, never cut mid-table/mid-fence.
    return _truncate_to_blocks(text, high)
