"""Tests for US5 — BM25 index, Reciprocal Rank Fusion, and hybrid retrieval.

Pure offline tests: no Ollama daemon, no textual/fastapi imports.
"""

from __future__ import annotations

import math
import pytest

from src.ollama_tutor.tutor.retrieval import (
    BM25Index,
    reciprocal_rank_fusion,
    _bm25_tokenize,
    _BM25_K1,
    _BM25_B,
)


# ------------------------------------------------------------------
# T033 — BM25Index
# ------------------------------------------------------------------


class TestBM25Index:
    """Unit tests for the BM25Index class."""

    SAMPLE_DOCS = [
        {"id": "c1", "text": "La photosynthèse convertit le CO2 en glucose et oxygène."},
        {"id": "c2", "text": "Le coeur pompe le sang dans tout le corps humain."},
        {"id": "c3", "text": "La photosynthèse est essentielle pour la vie sur Terre."},
        {"id": "c4", "text": "Les atomes sont composés de protons, neutrons et électrons."},
        {"id": "c5", "text": "La gravité maintient les planètes en orbite autour du Soleil."},
    ]

    def test_bm25_index_build(self) -> None:
        """Building an index from sample docs should not raise."""
        idx = BM25Index(self.SAMPLE_DOCS)
        assert idx._n == 5
        assert idx._avgdl > 0

    def test_bm25_index_empty(self) -> None:
        """Building from empty list should work, search returns []."""
        idx = BM25Index([])
        assert idx._n == 0
        assert idx.search("anything") == []

    def test_bm25_search_returns_results(self) -> None:
        """Search for a term present in the corpus should return results."""
        idx = BM25Index(self.SAMPLE_DOCS)
        results = idx.search("photosynthèse", k=3)
        assert len(results) > 0
        # Docs c1 and c3 mention "photosynthèse".
        chunk_ids = [cid for cid, _ in results]
        assert "c1" in chunk_ids
        assert "c3" in chunk_ids

    def test_bm25_search_ranking(self) -> None:
        """The document with more occurrences of the query term should score higher."""
        idx = BM25Index(self.SAMPLE_DOCS)
        results = idx.search("photosynthèse", k=5)
        # c3 and c1 both contain the word; c1 has it once, c3 once —
        # but IDF-based scoring should rank them at the top, above docs
        # that don't mention photosynthesis at all.
        chunk_ids = [cid for cid, _ in results]
        assert chunk_ids[0] in ("c1", "c3")
        # Non-photosynthesis docs should score lower or be absent from top-2.
        assert "c4" not in chunk_ids[:2]

    def test_bm25_search_no_match(self) -> None:
        """Searching for a term not in any document returns empty."""
        idx = BM25Index(self.SAMPLE_DOCS)
        results = idx.search("astronaute", k=5)
        assert results == []

    def test_bm25_search_returns_correct_k(self) -> None:
        """Returned results should not exceed k."""
        idx = BM25Index(self.SAMPLE_DOCS)
        results = idx.search("le", k=2)
        assert len(results) <= 2

    def test_bm25_score_non_negative(self) -> None:
        """All BM25 scores should be non-negative."""
        idx = BM25Index(self.SAMPLE_DOCS)
        results = idx.search("coeur", k=10)
        for _, score in results:
            assert score >= 0.0

    def test_bm25_idf_formula(self) -> None:
        """IDF should follow the specified formula: log((N - df + 0.5) / (df + 0.5) + 1)."""
        idx = BM25Index(self.SAMPLE_DOCS)
        # "photosynthèse" appears in 2 out of 5 docs.
        expected_idf = math.log((5 - 2 + 0.5) / (2 + 0.5) + 1.0)
        assert abs(idx._idf("photosynthèse") - expected_idf) < 1e-10


# ------------------------------------------------------------------
# T033 helper — _bm25_tokenize
# ------------------------------------------------------------------


class TestBM25Tokenize:
    """Unit tests for the BM25 tokenizer."""

    def test_basic(self) -> None:
        assert _bm25_tokenize("Hello world!") == ["hello", "world"]

    def test_empty(self) -> None:
        assert _bm25_tokenize("") == []

    def test_punctuation_stripped(self) -> None:
        tokens = _bm25_tokenize("a, b; c.")
        # All single chars are filtered out (len >= 2 required).
        assert tokens == []

    def test_french_accents(self) -> None:
        tokens = _bm25_tokenize("étudiants naïve résumé")
        assert "étudiants" in tokens
        assert "naïve" in tokens
        assert "résumé" in tokens


# ------------------------------------------------------------------
# T034 — Reciprocal Rank Fusion
# ------------------------------------------------------------------


class TestReciprocalRankFusion:
    """Unit tests for reciprocal_rank_fusion."""

    def test_single_list_passthrough(self) -> None:
        """A single list should pass through with adjusted scores."""
        ranked = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
        fused = reciprocal_rank_fusion([ranked], k=60)
        ids = [cid for cid, _ in fused]
        assert ids == ["a", "b", "c"]

    def test_merge_two_lists(self) -> None:
        """Items appearing in both lists should get a higher fused score."""
        list1 = [("a", 1.0), ("b", 0.8), ("c", 0.6)]
        list2 = [("b", 1.0), ("d", 0.9), ("a", 0.5)]
        fused = reciprocal_rank_fusion([list1, list2], k=60)
        fused_map = dict(fused)
        # "a" and "b" appear in both lists -> higher fused scores than
        # items unique to one list (c, d).
        assert fused_map["a"] > fused_map["d"]
        assert fused_map["b"] > fused_map["d"]
        assert fused_map["b"] > fused_map["c"]
        # Verify ranking is deterministic.
        ids = [cid for cid, _ in fused]
        assert ids[0] in ("a", "b")

    def test_disjoint_lists(self) -> None:
        """Items unique to one list should still appear in the fusion."""
        list1 = [("a", 1.0)]
        list2 = [("b", 1.0)]
        fused = reciprocal_rank_fusion([list1, list2], k=60)
        ids = [cid for cid, _ in fused]
        assert "a" in ids
        assert "b" in ids

    def test_empty_lists(self) -> None:
        """Empty input should return empty."""
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[], []]) == []

    def test_rank_matters(self) -> None:
        """Higher-ranked items should get better fused scores."""
        list1 = [("x", 1.0), ("y", 0.5)]
        list2 = [("x", 0.5), ("y", 1.0)]
        fused = reciprocal_rank_fusion([list1, list2], k=1)
        fused_map = dict(fused)
        # x: rank 1 in both lists → 1/(1+1) + 1/(1+1) = 1.0
        # y: rank 2 in both lists → 1/(1+2) + 1/(1+2) = 2/3
        # x should score higher than y.
        assert fused_map["x"] > fused_map["y"]
        assert abs(fused_map["x"] - 1.0) < 1e-10
        assert abs(fused_map["y"] - 2.0 / 3.0) < 1e-10

    def test_fused_scores_positive(self) -> None:
        """All fused scores should be positive."""
        list1 = [("a", 1.0), ("b", 0.5)]
        fused = reciprocal_rank_fusion([list1], k=60)
        for _, sc in fused:
            assert sc > 0.0
