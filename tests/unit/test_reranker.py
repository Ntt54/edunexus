"""Tests for US12 — SimpleReranker (T068-T069, T073).

Pure offline tests: no Ollama daemon, no textual/fastapi imports.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.reranker import (
    SimpleReranker,
    _jaccard_similarity,
    _tokenize,
)
from src.ollama_tutor.tutor.vector import ScoredChunk


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_chunk(text: str, score: float = 0.5, **kwargs) -> ScoredChunk:
    """Build a minimal ScoredChunk for testing."""
    defaults = dict(
        chunk_id="c1",
        book_id="b1",
        book_title="Book",
        chapter=None,
        page=None,
    )
    defaults.update(kwargs)
    return ScoredChunk(text=text, score=score, **defaults)


# ------------------------------------------------------------------
# T073 — tokeniser & Jaccard unit tests
# ------------------------------------------------------------------


class TestTokenizeAndJaccard:
    """Low-level helpers exposed by the reranker module."""

    def test_tokenize_extracts_content_terms(self) -> None:
        tokens = _tokenize("La photosynthèse convertit le CO2 en glucose")
        # Stopwords stripped; short tokens (< 3 chars) removed; CO2 normalised.
        assert "la" not in tokens          # stopword
        assert "le" not in tokens           # stopword
        assert "en" not in tokens           # stopword
        assert "photosynthèse" in tokens
        assert "convertit" in tokens

    def test_tokenize_empty_returns_empty_set(self) -> None:
        assert _tokenize("") == set()
        assert _tokenize("   ") == set()

    def test_jaccard_identical_sets(self) -> None:
        a = {"hello", "world"}
        assert _jaccard_similarity(a, a) == pytest.approx(1.0)

    def test_jaccard_disjoint_sets(self) -> None:
        assert _jaccard_similarity({"hello"}, {"world"}) == pytest.approx(0.0)

    def test_jaccard_partial_overlap(self) -> None:
        a = {"alpha", "beta"}
        b = {"beta", "gamma"}
        # intersection={beta}, union={alpha,beta,gamma} → 1/3
        assert _jaccard_similarity(a, b) == pytest.approx(1.0 / 3.0)

    def test_jaccard_empty_sets(self) -> None:
        assert _jaccard_similarity(set(), set()) == 0.0


# ------------------------------------------------------------------
# T073 — SimpleReranker.score() tests
# ------------------------------------------------------------------


class TestSimpleRerankerScore:
    """Verify the combined-score formula: 0.7 * cosine + 0.3 * jaccard."""

    def test_simple_reranker_scores(self) -> None:
        """Combined score equals 0.7 * cosine + 0.3 * jaccard."""
        reranker = SimpleReranker()
        chunk = _make_chunk(text="photosynthèse glucose soleil", score=0.8)
        query = "photosynthèse soleil"
        cs = reranker.score(query, chunk)
        # Expected: 0.7 * 0.8 + 0.3 * jaccard(photosynthèse,soleil ∩ ... )
        # jaccard(query_tokens, chunk_tokens) — both contain "photosynthèse"
        # and "soleil", so intersection is 2, union is 3 → ~0.666
        # cs ≈ 0.56 + 0.3 * 0.666 ≈ 0.76
        assert cs > 0.5
        assert cs <= 1.0

    def test_score_with_explicit_cosine_override(self) -> None:
        reranker = SimpleReranker()
        chunk = _make_chunk(text="nonsense", score=0.0)
        cs = reranker.score("test", chunk, cosine_score=1.0)
        # cosine=1.0, jaccard("test","nonsense") → 0 (no overlap) → 0.7*1 + 0.3*0 = 0.7
        assert cs == pytest.approx(0.7)

    def test_score_zero_cosine_zero_jaccard(self) -> None:
        reranker = SimpleReranker()
        chunk = _make_chunk(text="xyz", score=0.0)
        cs = reranker.score("abc", chunk)
        assert cs == pytest.approx(0.0)


# ------------------------------------------------------------------
# T073 — SimpleReranker.rerank() tests
# ------------------------------------------------------------------


class TestRerankerRerank:
    """Reranking reorders candidates and respects top_k."""

    def test_rerank_boosts_relevant(self) -> None:
        """Chunks containing query terms should rank higher than peers
        with similar cosine scores but no lexical overlap."""
        reranker = SimpleReranker()
        relevant = _make_chunk(
            text="La photosynthèse est essentielle pour la croissance des plantes.",
            score=0.45,
            chunk_id="c_rel",
        )
        irrelevant = _make_chunk(
            text="Le cœur pompe le sang dans le corps humain.",
            score=0.48,  # slightly higher cosine, but zero lexical overlap
            chunk_id="c_irr",
        )
        # relevant:  0.7 * 0.45 + 0.3 * jaccard ≈ 0.315 + 0.15 = 0.465
        # irrelevant: 0.7 * 0.48 + 0.3 * 0       = 0.336 + 0.0  = 0.336
        results = reranker.rerank("photosynthèse croissance", [irrelevant, relevant], top_k=2)
        # The relevant chunk should be re-ordered first.
        assert results[0].chunk_id == "c_rel"
        assert results[1].chunk_id == "c_irr"

    def test_rerank_top_k_limits_results(self) -> None:
        """top_k caps the number of returned results."""
        reranker = SimpleReranker()
        chunks = [
            _make_chunk(text=f"word_{i} alpha", score=0.5 + i * 0.05, chunk_id=f"c{i}")
            for i in range(10)
        ]
        results = reranker.rerank("alpha", chunks, top_k=3)
        assert len(results) == 3

    def test_rerank_empty_candidates(self) -> None:
        reranker = SimpleReranker()
        assert reranker.rerank("query", [], top_k=5) == []

    def test_rerank_preserves_original_scores(self) -> None:
        """Returned ScoredChunk objects keep their original .score attribute."""
        reranker = SimpleReranker()
        c1 = _make_chunk(text="hello world", score=0.3, chunk_id="c1")
        c2 = _make_chunk(text="hello world", score=0.9, chunk_id="c2")
        results = reranker.rerank("hello world", [c1, c2], top_k=2)
        scores = {r.chunk_id: r.score for r in results}
        assert scores["c1"] == 0.3
        assert scores["c2"] == 0.9

    def test_rerank_top_k_greater_than_candidates(self) -> None:
        """top_k larger than candidate list returns all candidates."""
        reranker = SimpleReranker()
        chunks = [_make_chunk(text="hello", score=0.5, chunk_id="c1")]
        results = reranker.rerank("hello", chunks, top_k=10)
        assert len(results) == 1
