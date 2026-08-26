"""Subject-scoped semantic retrieval for the local AI tutor (research D7/D11).

Builds a per-subject in-memory cosine index (reusing ``NumpyVectorIndex`` from
``embeddings.py``) over the chunks stored in ``LibraryStore``, embeds the
question through the Ollama client, and returns the top-``k`` chunks scoring at
or above a floor (default 0.25). Retrieved passages are assembled into
``<=6000``-character context blocks, each prefixed with a mechanical citation
``[Livre X — chapitre Y, p. Z]`` so the prompt layer can demand exact citations
(SC-004).

US5 — Recherche hybride BM25 + sémantique :
Adds ``BM25Index``, ``reciprocal_rank_fusion`` and ``retrieve_hybrid`` to
combine lexical (BM25) and semantic (cosine) retrieval via Reciprocal Rank
Fusion.  ``retrieve_hybrid`` is the single entry-point for hybrid search,
returning enriched dicts with per-method scores for transparency.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import math
import numpy as np
from typing import Any

from .store import LibraryStore
import re

from .vector import NumpyVectorIndex, ScoredChunk
from .reranker import SimpleReranker

# Retrieval floor (research D11): chunks below this cosine score are dropped.
DEFAULT_FLOOR = 0.25
# Hard cap on assembled context sent to the model (research D11, FR-015).
DEFAULT_MAX_CONTEXT_CHARS = 6000


def assemble_context_blocks(chunks: list[ScoredChunk], max_chars: int = DEFAULT_MAX_CONTEXT_CHARS) -> str:
    """Join retrieved chunks into ``<=max_chars`` prefixed context blocks.

    Each block is prefixed ``[Livre X — chapitre Y, p. Z]`` (omitting the
    chapter/page segment when metadata is missing) so citations are mechanical.
    Blocks are appended in score order until the cap is reached.
    """
    parts: list[str] = []
    total = 0
    for c in chunks:
        bits = [f"Livre {c.book_title}"]
        if c.chapter:
            bits.append(f"chapitre {c.chapter}")
        if c.page is not None:
            bits.append(f"p. {c.page}")
        prefix = "[" + " — ".join(bits) + "]"
        block = f"{prefix}\n{c.text}"
        # Always include at least the first block even if it alone exceeds cap.
        if parts and total + len(block) + 2 > max_chars:
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


# Minimal French/English stopwords so keyword scoring focuses on content terms
# (research D10: locate/rank are pure keyword scoring, NO embedding/LLM call).
_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "est", "sont",
    "que", "qui", "quoi", "dans", "pour", "par", "sur", "avec", "au", "aux",
    "ce", "cet", "cette", "ces", "son", "sa", "ses", "leur", "leurs", "il",
    "elle", "ils", "elles", "nous", "vous", "je", "tu", "the", "a", "an",
    "and", "is", "are", "of", "to", "in", "for", "with", "on", "by", "that",
    "this", "these", "those", "at", "from", "as", "be", "was", "were",
}


def _tokenize(notion: str) -> list[str]:
    """Lowercase content terms (len >= 3, non-stopword) from a notion query."""
    if not notion:
        return []
    terms = re.split(r"[^a-z0-9àâäéèêëïîôöùûüç]+", notion.lower(), flags=re.IGNORECASE)
    out: list[str] = []
    for t in terms:
        t = t.strip().lower()
        if len(t) >= 3 and t not in _STOPWORDS:
            out.append(t)
    return out


def _keyword_score(text: str, terms: list[str]) -> float:
    """Pure keyword score: summed term occurrences, length-normalized.

    No embedding, no LLM — just lexical overlap so locate/rank stay offline
    and instant (research D10 / FR-031/FR-032).
    """
    if not text or not terms:
        return 0.0
    low = text.lower()
    raw = 0.0
    for term in terms:
        # Count whole-word-ish occurrences (substring is acceptable for stems).
        raw += low.count(term)
    if raw == 0.0:
        return 0.0
    words = low.split()
    denom = max(1, len(words))
    # Scale so a few strong hits stand out without unbounded growth.
    return (raw / denom) * 10.0


# ------------------------------------------------------------------
# US5 — BM25 lexical index (T033)
# ------------------------------------------------------------------

# BM25 tuning constants (Okapi BM25 defaults).
_BM25_K1 = 1.5
_BM25_B = 0.75


def _bm25_tokenize(text: str) -> list[str]:
    """Lowercase tokens stripped of punctuation (>= 2 chars).

    Simpler than ``_tokenize`` — no stopword removal because BM25 IDF
    already down-weights very common terms.
    """
    if not text:
        return []
    # Split on any non-alphanumeric / non-accented-letter character.
    tokens = re.split(r"[^a-z0-9àâäéèêëïîôöùûüç]+", text.lower())
    return [t for t in tokens if len(t) >= 2]


class BM25Index:
    """Lightweight BM25 index built from chunk dicts.

    Pure-stdlib, no external dependencies — suitable for offline, low-spec
    machines (≤ 8 GB RAM, research D7).
    """

    def __init__(self, chunks: list[dict]) -> None:
        """Build index from chunk dicts (each must have ``id`` and ``text``)."""
        self._chunks = chunks
        self._n = len(chunks)
        # {chunk_id: [tokens]}
        self._doc_tokens: dict[str, list[str]] = {}
        # {chunk_id: {term: tf}}
        self._tf: dict[str, dict[str, int]] = {}
        # {term: df} — how many documents contain the term
        self._df: dict[str, int] = {}
        # Inverted index: only score documents containing a query term.
        self._postings: dict[str, list[str]] = {}
        # Average document length (in tokens).
        self._avgdl = 0.0

        total_len = 0
        for ch in chunks:
            cid = ch["id"]
            tokens = _bm25_tokenize(ch.get("text", ""))
            self._doc_tokens[cid] = tokens
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self._tf[cid] = tf
            total_len += len(tokens)
            for t in set(tokens):
                self._df[t] = self._df.get(t, 0) + 1
                self._postings.setdefault(t, []).append(cid)

        self._avgdl = total_len / max(1, self._n)

    def _idf(self, term: str) -> float:
        """Compute IDF for *term* using the standard BM25 formula."""
        df = self._df.get(term, 0)
        return math.log((self._n - df + 0.5) / (df + 0.5) + 1.0)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """Return top-k ``(chunk_id, score)`` pairs for the query."""
        q_tokens = _bm25_tokenize(query)
        if not q_tokens or not self._chunks:
            return []

        scores: dict[str, float] = {}
        for term in q_tokens:
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            for cid in self._postings.get(term, []):
                tf_map = self._tf[cid]
                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue
                dl = len(self._doc_tokens[cid])
                denom = tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * dl / max(1.0, self._avgdl))
                scores[cid] = scores.get(cid, 0.0) + idf * (tf * (_BM25_K1 + 1.0)) / denom

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:k]


# ------------------------------------------------------------------
# US5 — Reciprocal Rank Fusion (T034)
# ------------------------------------------------------------------

def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked lists with Reciprocal Rank Fusion.

    ``score(d) = Σ 1 / (k + rank_i(d))`` where ``rank_i`` is 1-indexed.

    Returns a single list of ``(chunk_id, fused_score)`` sorted descending
    by fused score.
    """
    agg: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_idx, (cid, _score) in enumerate(ranked):
            agg[cid] = agg.get(cid, 0.0) + 1.0 / (k + rank_idx + 1)
    fused = sorted(agg.items(), key=lambda x: x[1], reverse=True)
    return fused


# ------------------------------------------------------------------
# US5 — Hybrid retrieval entry-point (T035)
# ------------------------------------------------------------------

async def retrieve_hybrid(
    subject_id: str,
    question: str,
    store: "LibraryStore",
    client: Any,
    model: str,
    k: int = 5,
    book_ids: list[str] | None = None,
    reranker: SimpleReranker | None = None,
) -> list[dict[str, Any]]:
    """Hybrid BM25 + cosine retrieval fused via RRF.

    1. Cosine (semantic) search via the existing ``Retriever``.
    2. BM25 (lexical) search over all subject chunks.
    3. Fuse both ranked lists with ``reciprocal_rank_fusion``.
    4. *(US12)* When a *reranker* is supplied, apply post-retrieval
       reranking on the fused result list before returning.
    5. Return top-*k* results as dicts with transparency keys:
       ``chunk_id, text, score (fused), cosine_score, bm25_score``.
    """
    # --- Build a temporary Retriever for the cosine path ---
    retr = Retriever(store=store, client=client, model=model)

    # --- Ranked list 1: cosine (semantic) ---
    cosine_chunks = await retr.retrieve(subject_id, question, k=k * 3, book_ids=book_ids)
    cosine_ranked: list[tuple[str, float]] = [
        (sc.chunk_id, sc.score) for sc in cosine_chunks
    ]
    cosine_map: dict[str, float] = {cid: sc for cid, sc in cosine_ranked}
    cosine_meta: dict[str, ScoredChunk] = {sc.chunk_id: sc for sc in cosine_chunks}

    # --- Ranked list 2: BM25 (lexical) ---
    # Use all subject chunks (not just embedded ones) so BM25 sees everything.
    all_rows = store.get_subject_chunks(subject_id)
    scope = {str(b) for b in book_ids} if book_ids is not None else None
    if scope is not None:
        all_rows = [r for r in all_rows if str(r["book_id"]) in scope]

    bm25_index = BM25Index(all_rows)
    bm25_raw = bm25_index.search(query=question, k=k * 3)
    # Map raw BM25 scores to a 0..1 range for transparency (normalise by max).
    bm25_max = bm25_raw[0][1] if bm25_raw else 1.0
    bm25_ranked: list[tuple[str, float]] = [
        (cid, sc / bm25_max if bm25_max > 0 else 0.0) for cid, sc in bm25_raw
    ]
    bm25_map: dict[str, float] = dict(bm25_ranked)

    # Pre-fetch book titles for enriched output.
    titles: dict[str, str] = {}
    for b in store.list_books(subject_id):
        titles[b.id] = b.title

    # --- Fuse with RRF ---
    fused = reciprocal_rank_fusion([cosine_ranked, bm25_ranked])

    # --- Assemble candidate result dicts ---
    # Let the reranker inspect a broader pool; without it, keep the cheaper
    # top-k path. This follows the standard retrieve-then-rerank design.
    candidate_k = k * 3 if reranker is not None else k
    fused_max = fused[0][1] if fused else 1.0
    results: list[dict[str, Any]] = []
    for cid, fsc in fused[:candidate_k]:
        # Try cosine meta first, fall back to raw row.
        meta_row = cosine_meta.get(cid)
        if meta_row is not None:
            text = meta_row.text
            book_id = meta_row.book_id
        else:
            # Find in all_rows
            row_match = next((r for r in all_rows if r["id"] == cid), None)
            if row_match is None:
                continue
            text = row_match.get("text", "")
            book_id = str(row_match["book_id"])

        results.append({
            "chunk_id": cid,
            "text": text,
            "score": round(fsc / fused_max, 4) if fused_max > 0 else 0.0,
            "cosine_score": round(cosine_map.get(cid, 0.0), 4),
            "bm25_score": round(bm25_map.get(cid, 0.0), 4),
        })

    # --- US12: optional post-retrieval reranking ---
    if reranker is not None and results:
        # Convert dicts → ScoredChunk so the reranker can operate on them.
        sc_candidates: list[ScoredChunk] = []
        for d in results:
            sc_candidates.append(ScoredChunk(
                chunk_id=d["chunk_id"],
                book_id="",
                book_title="",
                chapter=None,
                page=None,
                score=d["score"],
                text=d["text"],
            ))
        reranked = reranker.rerank(question, sc_candidates, top_k=k)
        # Rebuild the result dicts preserving per-method scores.
        sc_index = {d["chunk_id"]: d for d in results}
        results = []
        for sc in reranked:
            orig = sc_index.get(sc.chunk_id)
            if orig is not None:
                results.append(orig)

    return results


class Retriever:
    """Lazy per-subject cosine retriever over ``LibraryStore`` chunks."""

    def __init__(
        self,
        store: LibraryStore,
        client,
        model: str,
        floor: float = DEFAULT_FLOOR,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        reranker: SimpleReranker | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.model = model
        self.floor = floor
        self.max_context_chars = max_context_chars
        self.reranker = reranker
        # subject_id -> (NumpyVectorIndex, {chunk_id: row}, {book_id: title})
        self._cache: dict[str, tuple[NumpyVectorIndex, dict[str, dict], dict[str, str]]] = {}

    def _index_for(self, subject_id: str) -> tuple[NumpyVectorIndex, dict[str, dict], dict[str, str]]:
        if subject_id not in self._cache:
            idx = NumpyVectorIndex()
            # Provenance : seuls les chunks embeddés par LE modèle courant
            # entrent dans l'index (005-suite — empêche le mélange de
            # vecteurs incompatibles entre modèles d'embedding).
            rows = self.store.get_indexed_chunks(subject_id, model=self.model)
            items: list[tuple[str, list[float]]] = []
            meta: dict[str, dict] = {}
            for r in rows:
                emb = r.get("embedding")
                if emb:
                    vec = np.frombuffer(emb, dtype=np.float32).tolist()
                    items.append((r["id"], vec))
                    meta[r["id"]] = r
            idx.add(items)
            titles: dict[str, str] = {}
            for b in self.store.list_books(subject_id):
                titles[b.id] = b.title
            self._cache[subject_id] = (idx, meta, titles)
        return self._cache[subject_id]

    def invalidate(self, subject_id: str) -> None:
        """Drop the cached index for a subject (called after writes)."""
        self._cache.pop(subject_id, None)

    def set_model(self, model: str) -> None:
        """Switch embedding model and invalidate all model-bound indexes."""
        if model != self.model:
            self.model = model
            self._cache.clear()

    async def retrieve(
        self,
        subject_id: str,
        question: str,
        k: int,
        book_ids: list[str] | None = None,
    ) -> list[ScoredChunk]:
        """Embed ``question`` and return the top-``k`` scored chunks (>= floor).

        ``book_ids`` restreint le périmètre aux livres indiqués (sources
        actives d'une conversation, 005-platform-ui-library) : la recherche
        élargit temporairement son rayon pour compenser le filtrage, puis
        coupe à ``k``. ``None`` = illimité (comportement historique).
        """
        idx, meta, titles = self._index_for(subject_id)
        vectors = await self.client.embed(self.model, [question])
        if not vectors or not vectors[0]:
            return []
        scope = {str(b) for b in book_ids} if book_ids is not None else None
        search_k = max(k, 24) * 3 if scope is not None else k
        scored = idx.search(vectors[0], search_k, floor=self.floor)
        out: list[ScoredChunk] = []
        # When reranking, over-fetch so the reranker has a broader pool to
        # reorder; otherwise collect exactly *k* candidates.
        fetch_k = k * 3 if self.reranker is not None else k
        for cid, score in scored:
            r = meta[cid]
            book_id = str(r["book_id"])
            if scope is not None and book_id not in scope:
                continue
            out.append(ScoredChunk(
                chunk_id=cid,
                book_id=book_id,
                book_title=titles.get(book_id, book_id),
                chapter=r.get("chapter"),
                page=r.get("page"),
                score=score,
                text=r["text"],
            ))
            if len(out) >= fetch_k:
                break

        # --- US12: optional post-retrieval reranking ---
        if self.reranker is not None and out:
            out = self.reranker.rerank(question, out, top_k=k)

        return out

    def assemble_context(self, chunks: list[ScoredChunk], max_chars: int | None = None) -> str:
        """Assemble retrieved chunks into a bounded context string."""
        return assemble_context_blocks(chunks, max_chars or self.max_context_chars)

    # ------------------------------------------------------------------
    # Knowledge navigation (US7 / T046, FR-031/FR-032) — PURE keyword
    # scoring, NO embedding and NO LLM call (research D10).
    # ------------------------------------------------------------------

    def locate(
        self, subject_id: str, notion: str, top_n: int = 10
    ) -> list[dict[str, Any]]:
        """Locate a notion across the subject's chunks (book/chapter/page).

        Returns the top-``top_n`` scored chunks as dicts
        ``{book, book_id, chapter, page, score, text}`` ordered by descending
        keyword score. Pure lexical scoring — never touches the model.
        """
        terms = _tokenize(notion)
        if not terms:
            return []
        rows = self.store.get_subject_chunks(subject_id)
        titles: dict[str, str] = {}
        for b in self.store.list_books(subject_id):
            titles[b.id] = b.title
        scored: list[dict[str, Any]] = []
        for r in rows:
            score = _keyword_score(r.get("text", ""), terms)
            if score <= 0.0:
                continue
            scored.append({
                "book": titles.get(str(r["book_id"]), str(r["book_id"])),
                "book_id": str(r["book_id"]),
                "chapter": r.get("chapter"),
                "page": r.get("page"),
                "score": round(float(score), 4),
                "text": r.get("text", ""),
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_n]

    def rank_books(
        self, subject_id: str, notion: str, top_n: int = 10
    ) -> list[dict[str, Any]]:
        """Aggregate locate scores per book (FR-032).

        Returns ``[{book, score}]`` sorted by descending aggregate score —
        the books most relevant to the notion. Pure data, no LLM.
        """
        located = self.locate(subject_id, notion, top_n=top_n * 5)
        agg: dict[str, float] = {}
        for c in located:
            agg[c["book"]] = agg.get(c["book"], 0.0) + c["score"]
        ranked = [
            {"book": book, "score": round(float(s), 4)} for book, s in agg.items()
        ]
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked[:top_n]

    async def retrieve_per_book(
        self, subject_id: str, question: str, k_per_book: int = 2
    ) -> dict[str, list[ScoredChunk]]:
        """Embed ``question`` and return top-``k_per_book`` chunks per book.

        Used by compare mode (T047): each book contributes its own most
        relevant passages so the synthesis can cite every source. Falls back to
        ``{}`` when no embedding/vector is available.
        """
        idx, meta, titles = self._index_for(subject_id)
        vectors = await self.client.embed(self.model, [question])
        if not vectors or not vectors[0]:
            return {}
        # Over-fetch then trim per book so each book gets a fair share.
        over_k = k_per_book * max(1, len(titles)) * 2
        scored = idx.search(vectors[0], over_k, floor=self.floor)
        by_book: dict[str, list[ScoredChunk]] = {}
        for cid, score in scored:
            r = meta[cid]
            bid = str(r["book_id"])
            by_book.setdefault(bid, []).append(ScoredChunk(
                chunk_id=cid,
                book_id=bid,
                book_title=titles.get(bid, bid),
                chapter=r.get("chapter"),
                page=r.get("page"),
                score=score,
                text=r["text"],
            ))
        result: dict[str, list[ScoredChunk]] = {}
        for bid, chunks in by_book.items():
            chunks.sort(key=lambda c: c.score, reverse=True)
            result[bid] = chunks[:k_per_book]
        return result


# ------------------------------------------------------------------
# US10 — Citation validation (T058, FR-004 SC-004)
# ------------------------------------------------------------------

# Patterns that match citation references in LLM responses.
# Covers the mechanical ``[Livre X — ...]`` format emitted by
# ``assemble_context_blocks``, English ``[Book X ...]``, and
# markdown-bold variants like ``**[Livre X]**``.
# The full match (including prefix) is returned for transparent reporting.
_CITATION_RE = re.compile(
    r"(?:\*\*)?"           # optional markdown bold opener
    r"\["                  # opening bracket
    r"(?:Livre|Book)\s+"   # standard prefix (FR/EN)
    r"[^]]+"               # citation body (everything until ']')
    r"\]"                  # closing bracket
    r"(?:\*\*)?",          # optional markdown bold closer
    re.IGNORECASE,
)


def _normalise_citation(raw: str) -> str:
    """Strip brackets, whitespace, trailing punctuation and lower-case for comparison.

    The input can be a full citation like ``[Livre Chimie Générale — p. 12]``
    or just the inner body.  The output is a plain lowercase string that can
    be compared against normalised source keys.
    """
    s = raw.strip().strip("[]").rstrip(".,;:]").strip().lower()
    # Normalise long dashes (em-dash, en-dash) to a single " — " for matching.
    s = re.sub(r"\s*[-–—]\s*", " — ", s)
    # Collapse multiple spaces.
    s = re.sub(r"\s{2,}", " ", s)
    return s


def _source_key(source: dict[str, Any]) -> str:
    """Build a normalised lookup key from a source dict.

    A source dict typically has ``book``, optionally ``chapter`` and ``page``.
    The key is ``"livre x — chapitre y — p. z"`` (lower-cased) so it matches
    what ``_normalise_citation`` produces from LLM output.
    """
    book = str(source.get("book", "")).strip().lower()
    parts = [f"livre {book}"]
    chapter = source.get("chapter")
    if chapter:
        parts.append(f"chapitre {str(chapter).lower()}")
    page = source.get("page")
    if page is not None:
        parts.append(f"p. {page}")
    return " — ".join(parts)


def _book_only_key(source: dict[str, Any]) -> str:
    """Return just the lowered book title for partial matching."""
    return str(source.get("book", "")).strip().lower()


def validate_citations(
    response_text: str,
    available_sources: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Validate citation references in *response_text* against *available_sources*.

    Extracts ``[Livre X ...]`` / ``[Book X ...]`` patterns (including
    markdown-bold wrappers) from the response and checks each against the
    source list.  A citation is **valid** when its book name matches the book
    of an available source; **invalid** otherwise.

    Returns ``(valid_citations, invalid_citations)`` — each is a list of the
    raw citation strings as they appeared in the text.

    This function is UI-framework-free (no textual / fastapi imports).
    """
    if not response_text:
        return [], []

    matches = _CITATION_RE.findall(response_text)
    if not matches:
        return [], []

    # Build lookup sets from available sources.
    book_keys = {_book_only_key(s) for s in available_sources}
    full_keys = {_source_key(s) for s in available_sources}

    valid: list[str] = []
    invalid: list[str] = []

    for citation_text in matches:
        norm = _normalise_citation(citation_text)
        # Check 1 — exact full-key match (book + chapter + page).
        # Check 2 — book-only match (looser, but still a known source).
        if norm in full_keys or norm in book_keys:
            valid.append(citation_text)
        else:
            # Try partial: does the normalised body contain any source book name?
            matched_book = any(
                bk and bk in norm for bk in book_keys if bk
            )
            if matched_book:
                valid.append(citation_text)
            else:
                invalid.append(citation_text)

    return valid, invalid

