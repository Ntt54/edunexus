"""Post-retrieval reranking for the local AI tutor (US12, T068-T069).

Applies a lightweight reranking pass on top of the initial retrieval results
to improve precision.  Uses a combined score of cosine similarity (semantic
relevance) and Jaccard similarity (lexical overlap) to reorder candidates.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import re
from typing import Any

from .vector import ScoredChunk

# Minimal French/English stopwords shared with retrieval.py.
_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "est", "sont",
    "que", "qui", "quoi", "dans", "pour", "par", "sur", "avec", "au", "aux",
    "ce", "cet", "cette", "ces", "son", "sa", "ses", "leur", "leurs", "il",
    "elle", "ils", "elles", "nous", "vous", "je", "tu", "the", "a", "an",
    "and", "is", "are", "of", "to", "in", "for", "with", "on", "by", "that",
    "this", "these", "those", "at", "from", "as", "be", "was", "were",
}


def _tokenize(text: str) -> set[str]:
    """Extract lowercase content-term tokens (len >= 3, non-stopword)."""
    if not text:
        return set()
    terms = re.split(r"[^a-z0-9àâäéèêëïîôöùûüç]+", text.lower(), flags=re.IGNORECASE)
    return {t.strip() for t in terms if len(t.strip()) >= 3 and t.strip() not in _STOPWORDS}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


class SimpleReranker:
    """Lightweight reranker combining semantic and lexical signals.

    Score formula (T069)::

        combined = 0.7 * cosine_score + 0.3 * jaccard_lexical

    The cosine score is taken directly from the initial retrieval (already
    normalised 0..1 by the vector index).  The Jaccard component measures
    lexical overlap between query tokens and chunk tokens.

    This class is UI-framework-free (no textual/fastapi imports) and uses
    only stdlib + numpy (which is already a project dependency).
    """

    # Weight given to the cosine (semantic) component.
    COSINE_WEIGHT: float = 0.7
    # Weight given to the Jaccard (lexical) component.
    LEXICAL_WEIGHT: float = 0.3

    def score(
        self,
        query: str,
        candidate: ScoredChunk,
        *,
        cosine_score: float | None = None,
    ) -> float:
        """Return the combined reranking score for a single candidate.

        Parameters
        ----------
        query:
            The original user query.
        candidate:
            A ``ScoredChunk`` from the initial retrieval pass.
        cosine_score:
            Optional override; if *None* the ``candidate.score`` attribute
            is used (which holds the cosine similarity from retrieval).
        """
        cs = cosine_score if cosine_score is not None else candidate.score
        query_tokens = _tokenize(query)
        chunk_tokens = _tokenize(candidate.text)
        jaccard = _jaccard_similarity(query_tokens, chunk_tokens)
        return self.COSINE_WEIGHT * cs + self.LEXICAL_WEIGHT * jaccard

    def rerank(
        self,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int = 5,
    ) -> list[ScoredChunk]:
        """Rerank *candidates* and return the top-*k* results.

        Each candidate receives a combined score (cosine + Jaccard lexical).
        Results are returned sorted by descending combined score, limited to
        ``top_k`` items.
        """
        if not candidates:
            return []

        scored: list[tuple[float, ScoredChunk]] = []
        for c in candidates:
            combined = self.score(query, c)
            scored.append((combined, c))

        # Sort descending by combined score.
        scored.sort(key=lambda x: x[0], reverse=True)

        # Return top-k ScoredChunk objects (score attribute is left unchanged
        # so downstream code that reads candidate.score still works).
        return [c for _, c in scored[:top_k]]
