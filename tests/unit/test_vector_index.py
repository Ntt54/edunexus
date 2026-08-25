"""Unit tests for the existing ``NumpyVectorIndex`` (004-local-ai-tutor T019).

Offline: synthetic float vectors, no model, no DB. Verifies cosine ranking,
``k`` limiting, score-floor filtering, subject isolation (an index built only
from subject A's chunks never returns subject B's rows) and invalidation after
writes.
"""

from __future__ import annotations

import pytest

from ollama_tutor.tutor.embeddings import NumpyVectorIndex


def test_cosine_ranking_on_synthetic_vectors() -> None:
    idx = NumpyVectorIndex()
    idx.add([
        ("a", [1.0, 0.0, 0.0]),
        ("b", [0.0, 1.0, 0.0]),
        ("c", [0.0, 0.0, 1.0]),
        ("d", [1.0, 1.0, 0.0]),
    ])
    scored = idx.search([1.0, 0.0, 0.0], k=4)
    ids = [s[0] for s in scored]
    # "a" is identical to the query → score 1.0, must rank first.
    assert ids[0] == "a"
    assert scored[0][1] == pytest.approx(1.0, abs=1e-5)
    # "d" is 45° off → ~0.707, ahead of the orthogonal "b"/"c" (0.0).
    assert "d" in ids[:2]
    assert scored[1][1] == pytest.approx(0.7071, abs=1e-3)


def test_k_limits_number_of_results() -> None:
    idx = NumpyVectorIndex()
    idx.add([
        ("a", [1.0, 0.0, 0.0]),
        ("b", [0.0, 1.0, 0.0]),
        ("c", [0.0, 0.0, 1.0]),
    ])
    assert len(idx.search([1.0, 0.0, 0.0], k=1)) == 1
    assert len(idx.search([1.0, 0.0, 0.0], k=2)) == 2
    # k larger than the corpus returns everything available.
    assert len(idx.search([1.0, 0.0, 0.0], k=99)) == 3


def test_score_floor_filters_low_similarity() -> None:
    idx = NumpyVectorIndex()
    idx.add([
        ("a", [1.0, 0.0, 0.0]),
        ("b", [0.0, 1.0, 0.0]),
        ("c", [0.0, 0.0, 1.0]),
        ("d", [1.0, 1.0, 0.0]),
    ])
    # Floor 0.5 keeps only "a" (1.0) and "d" (~0.707); "b"/"c" score 0.
    scored = idx.search([1.0, 0.0, 0.0], k=10, floor=0.5)
    ids = {s[0] for s in scored}
    assert ids == {"a", "d"}
    assert all(s[1] >= 0.5 for s in scored)


def test_subject_isolation_index_never_returns_other_subject_rows() -> None:
    # An index built only from subject A's chunks must never surface B's rows.
    idx_a = NumpyVectorIndex()
    idx_a.add([
        ("A1", [1.0, 0.0, 0.0]),
        ("A2", [0.8, 0.2, 0.0]),
    ])
    idx_b = NumpyVectorIndex()
    idx_b.add([
        ("B1", [0.0, 1.0, 0.0]),
        ("B2", [0.0, 0.0, 1.0]),
    ])
    a_hits = [s[0] for s in idx_a.search([1.0, 0.0, 0.0], k=10)]
    b_hits = [s[0] for s in idx_b.search([0.0, 1.0, 0.0], k=10)]
    assert all(h.startswith("A") for h in a_hits)
    assert all(h.startswith("B") for h in b_hits)
    assert set(a_hits).isdisjoint(set(b_hits))


def test_invalidation_after_writes_clears_index() -> None:
    idx = NumpyVectorIndex()
    idx.add([("a", [1.0, 0.0, 0.0])])
    assert idx.search([1.0, 0.0, 0.0], k=1)
    idx.invalidate()
    assert idx.search([1.0, 0.0, 0.0], k=1) == []
    # Re-adding restores searchability.
    idx.add([("a", [1.0, 0.0, 0.0])])
    assert idx.search([1.0, 0.0, 0.0], k=1)[0][0] == "a"


def test_empty_index_search_is_safe() -> None:
    idx = NumpyVectorIndex()
    assert idx.search([1.0, 0.0, 0.0], k=5) == []
    assert idx.search([1.0, 0.0, 0.0], k=0) == []
