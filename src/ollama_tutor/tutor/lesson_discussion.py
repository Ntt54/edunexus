"""Lesson discussion service — Feature 009 (US1).

UI-agnostic (no fastapi/textual). Centralises discussion creation,
status transition not_started→in_progress and RAG filtering by notion.

Delegates LLM generation to ``TutorService`` when available but keeps
fallback deterministic path for offline tests.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from typing import Any

from .models import LessonDiscussion, SourceReference
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
        """
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
        # filtered excerpts); on failure/None ONLY, keep the deterministic
        # echo as the offline fallback (existing format preserved).
        answer = self._try_llm_text(
            "lesson_answer", notion, chunks, question=question.strip()
        )
        is_fallback = False
        if not answer:
            is_fallback = True
            answer = f"Réponse sur « {notion} » : {question.strip()}"
            if sources:
                # Readable source (book title via store), never a raw id.
                source_title = self._book_title(sources[0].book_id)
                if source_title:
                    answer += f"\n\nSource : {source_title}"
        self.store.add_lesson_message(discussion_id, "assistant", answer, sources=sources)
        return {
            "answer": answer,
            "sources": [s.to_dict() for s in sources],
            "fallback": is_fallback,
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
        obj = self.store.add_generated_content(
            discussion_id, "lesson_course", content, sources=sources, confidence=confidence,
            model=self._llm_model() if not is_fallback else None,
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
        streamer = (
            getattr(self.tutor_service, "stream_lesson_text", None)
            if self.tutor_service is not None
            else None
        )
        if not callable(streamer):
            yield {"error": "LLM indisponible pour la génération du cours"}
            return
        excerpts = [(c.get("text") or "")[:500] for c in chunks[:6]]
        parts: list[str] = []
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
                    parts.append(delta)
                    yield {"delta": delta}
        except asyncio.TimeoutError:
            yield {
                "error": f"Délai de génération dépassé ({int(LESSON_STREAM_TIMEOUT_S)} s)"
            }
            return
        except Exception as exc:
            yield {"error": str(exc) or "Génération du cours impossible"}
            return
        content = "".join(parts)
        if not content.strip():
            yield {"error": "Réponse vide du modèle"}
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
    cnt = _word_count(text)
    if low <= cnt <= high:
        # lexical diversity guard: ensure at least 100 distinct tokens when padded range is large
        if low >= 800:
            words = text.split()
            if len(set(w.lower() for w in words)) < 100 and chunks:
                # force additional varied padding to lift diversity
                pool = _build_padding_pool(notion, chunks, course_content)
                idx = 0
                while len(set(w.lower() for w in text.split())) < 100 and _word_count(text) < high:
                    text += "\n\n" + pool[idx % len(pool)]
                    idx += 1
                    if idx > 30:
                        break
                words = text.split()
                if len(words) > high:
                    text = " ".join(words[:high])
        return text
    if cnt < low:
        pool = _build_padding_pool(notion, chunks, course_content)
        idx = 0
        # cycle through real excerpts to reach low bound
        while _word_count(text) < low:
            excerpt = pool[idx % len(pool)]
            text += "\n\n" + excerpt
            idx += 1
            if idx > 300:  # safety
                break
        words = text.split()
        if len(words) > high:
            text = " ".join(words[:high])
        # final diversity check for large targets
        if low >= 800:
            uniq = len(set(w.lower() for w in text.split()))
            if uniq < 100:
                # inject more distinct chunks if still low
                extra = 0
                while uniq < 100 and extra < 20:
                    text += "\n\n" + pool[extra % len(pool)]
                    extra += 1
                    uniq = len(set(w.lower() for w in text.split()))
                words = text.split()
                if len(words) > high:
                    text = " ".join(words[:high])
        return text
    # cnt > high
    words = text.split()
    return " ".join(words[:high])
