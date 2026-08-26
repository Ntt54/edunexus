"""Subject-scoped semantic retrieval for the local AI tutor (research D7/D11).

Builds a per-subject in-memory cosine index (reusing ``NumpyVectorIndex`` from
``embeddings.py``) over the chunks stored in ``LibraryStore``, embeds the
question through the Ollama client, and returns the top-``k`` chunks scoring at
or above a floor (default 0.25). Retrieved passages are assembled into
``<=6000``-character context blocks, each prefixed with a mechanical citation
``[Livre X — chapitre Y, p. Z]`` so the prompt layer can demand exact citations
(SC-004).

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import numpy as np
from typing import Any

from .store import LibraryStore
import re

from .vector import NumpyVectorIndex, ScoredChunk

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


class Retriever:
    """Lazy per-subject cosine retriever over ``LibraryStore`` chunks."""

    def __init__(
        self,
        store: LibraryStore,
        client,
        model: str,
        floor: float = DEFAULT_FLOOR,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self.store = store
        self.client = client
        self.model = model
        self.floor = floor
        self.max_context_chars = max_context_chars
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
            if len(out) >= k:
                break
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

