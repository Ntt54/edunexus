"""Tests for US10 — Citation validation (T058, T061).

Pure offline tests: no Ollama daemon, no textual/fastapi imports.
Validates ``validate_citations()`` from tutor/retrieval.py.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.retrieval import (
    validate_citations,
    _normalise_citation,
    _source_key,
    _CITATION_RE,
)


# ------------------------------------------------------------------
# Helper fixtures
# ------------------------------------------------------------------

@pytest.fixture
def three_sources() -> list[dict]:
    """Three sample sources as emitted by _ask_iter sources_frame."""
    return [
        {"book": "Chimie Générale", "chapter": "Structure de l'atome", "page": 12, "score": 0.91},
        {"book": "Physique Nucléaire", "chapter": "Radioactivité", "page": 45, "score": 0.82},
        {"book": "Mathématiques Fondamentales", "chapter": "Algèbre linéaire", "page": None, "score": 0.75},
    ]


# ------------------------------------------------------------------
# T058 — validate_citations: all citations valid
# ------------------------------------------------------------------

class TestValidateCitationsValid:
    """All citations in the response match available sources."""

    def test_all_valid_full_citations(self, three_sources: list[dict]) -> None:
        text = (
            "Selon [Livre Chimie Générale — chapitre Structure de l'atome, p. 12], "
            "l'atome possède un noyau. De plus, "
            "[Livre Physique Nucléaire — chapitre Radioactivité, p. 45] explique la désintégration."
        )
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 2
        assert len(invalid) == 0

    def test_all_valid_book_only(self, three_sources: list[dict]) -> None:
        """Citations that mention only the book title (no chapter/page)."""
        text = (
            "D'après [Livre Chimie Générale], la structure atomique est fondamentale."
        )
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 1
        assert len(invalid) == 0

    def test_valid_english_book_prefix(self, three_sources: list[dict]) -> None:
        """English-style [Book X] citations should also match."""
        text = "As stated in [Book Chimie Générale], the atom has ..."
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 1
        assert len(invalid) == 0

    def test_valid_bold_markdown(self, three_sources: list[dict]) -> None:
        """Markdown-bold citations **[Livre X]** should be extracted and valid."""
        text = "Selon **[Livre Chimie Générale]**, ..."
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 1
        assert len(invalid) == 0

    def test_valid_partial_chapter(self, three_sources: list[dict]) -> None:
        """Citation with chapter but no page (page=None in source)."""
        text = "En vertu de [Livre Mathématiques Fondamentales — chapitre Algèbre linéaire], ..."
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 1
        assert len(invalid) == 0


# ------------------------------------------------------------------
# T058 — validate_citations: some citations invalid
# ------------------------------------------------------------------

class TestValidateCitationsInvalid:
    """Some citations reference books NOT in available sources."""

    def test_one_invalid(self, three_sources: list[dict]) -> None:
        text = (
            "Selon [Livre Chimie Générale — chapitre Structure de l'atome, p. 12], "
            "mais aussi [Livre Histoire Contemporaine — chapitre Guerres mondiales, p. 88]."
        )
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 1
        assert len(invalid) == 1
        assert any("Histoire Contemporaine" in c for c in invalid)

    def test_all_invalid(self, three_sources: list[dict]) -> None:
        text = (
            "Comme le dit [Livre Fantaisie Magique — chapitre Sortilèges, p. 3], "
            "et [Livre Astronomie Avancée — p. 99]."
        )
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 0
        assert len(invalid) == 2

    def test_hallucinated_book(self, three_sources: list[dict]) -> None:
        """Completely fabricated citation not matching any source."""
        text = "Référence : [Livre Manuel de Cuisine, p. 50]."
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 0
        assert len(invalid) == 1


# ------------------------------------------------------------------
# T058 — validate_citations: mixed valid and invalid
# ------------------------------------------------------------------

class TestValidateCitationsMixed:
    """A mix of valid and invalid citations in the same response."""

    def test_two_valid_one_invalid(self, three_sources: list[dict]) -> None:
        text = (
            "Premièrement, [Livre Chimie Générale — chapitre Structure de l'atome, p. 12] "
            "montre que l'atome est composé. Deuxièmement, "
            "[Livre Physique Nucléaire — chapitre Radioactivité, p. 45] décrit les désintégrations. "
            "Enfin, [Livre Botanique Tropicale — chapitre Forêts, p. 7] confirme..."
        )
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 2
        assert len(invalid) == 1
        assert any("Botanique Tropicale" in c for c in invalid)

    def test_repeated_valid_citation(self, three_sources: list[dict]) -> None:
        """Same valid citation used multiple times — each instance is validated."""
        text = (
            "Selon [Livre Chimie Générale], et aussi "
            "[Livre Chimie Générale] le confirme. "
            "Mais [Livre Zoologie Marine] est inventé."
        )
        valid, invalid = validate_citations(text, three_sources)
        # Two valid instances of Chimie Générale, one invalid Zoologie Marine
        assert len(valid) == 2
        assert len(invalid) == 1


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestValidateCitationsEdgeCases:
    """Boundary and empty-input edge cases."""

    def test_empty_response(self, three_sources: list[dict]) -> None:
        valid, invalid = validate_citations("", three_sources)
        assert valid == []
        assert invalid == []

    def test_none_response_text(self, three_sources: list[dict]) -> None:
        valid, invalid = validate_citations(None, three_sources)  # type: ignore[arg-type]
        assert valid == []
        assert invalid == []

    def test_no_citations_in_text(self, three_sources: list[dict]) -> None:
        text = "Ceci est une réponse sans aucune citation référencée."
        valid, invalid = validate_citations(text, three_sources)
        assert valid == []
        assert invalid == []

    def test_no_available_sources(self) -> None:
        text = "Référence : [Livre Chimie Générale — p. 12]."
        valid, invalid = validate_citations(text, [])
        assert len(valid) == 0
        assert len(invalid) == 1

    def test_brackets_without_book_prefix(self, three_sources: list[dict]) -> None:
        """Brackets without Livre/Book prefix are NOT citations."""
        text = "On utilise la formule [E = mc²] et [Livre Chimie Générale — p. 12]."
        valid, invalid = validate_citations(text, three_sources)
        assert len(valid) == 1
        assert len(invalid) == 0


# ------------------------------------------------------------------
# Unit tests for helper functions
# ------------------------------------------------------------------

class TestNormalizeCitation:
    """Tests for _normalise_citation helper."""

    def test_basic_normalisation(self) -> None:
        result = _normalise_citation("[Livre Chimie Générale — chapitre 1, p. 12]")
        assert "livre chimie générale" in result
        assert "chapitre 1" in result
        assert "p. 12" in result

    def test_long_dash_normalisation(self) -> None:
        """En-dash (–) and em-dash (—) are normalised to ' — '."""
        result = _normalise_citation("[Livre Chimie–chapitre 1]")
        assert "livre chimie" in result
        assert "chapitre 1" in result

    def test_trailing_punctuation_stripped(self) -> None:
        result = _normalise_citation("[Livre Chimie Générale].")
        assert result == "livre chimie générale"

    def test_strip_brackets(self) -> None:
        result = _normalise_citation("[Livre Physique]")
        assert result == "livre physique"


class TestSourceKey:
    """Tests for _source_key helper."""

    def test_full_key(self) -> None:
        src = {"book": "Chimie Générale", "chapter": "Structure", "page": 12}
        key = _source_key(src)
        assert "livre chimie générale" in key
        assert "chapitre structure" in key
        assert "p. 12" in key

    def test_no_page(self) -> None:
        src = {"book": "Chimie Générale", "chapter": "Structure", "page": None}
        key = _source_key(src)
        assert "p." not in key

    def test_no_chapter(self) -> None:
        src = {"book": "Chimie Générale", "chapter": None, "page": 12}
        key = _source_key(src)
        assert "chapitre" not in key


class TestCitationRegex:
    """Tests for the _CITATION_RE pattern."""

    def test_standard_livre(self) -> None:
        m = _CITATION_RE.findall("[Livre Chimie — p. 12]")
        assert m == ["[Livre Chimie — p. 12]"]

    def test_standard_book(self) -> None:
        m = _CITATION_RE.findall("[Book Physics — chapter 3]")
        assert m == ["[Book Physics — chapter 3]"]

    def test_bold_markdown(self) -> None:
        m = _CITATION_RE.findall("**[Livre Chimie — p. 1]**")
        assert m == ["**[Livre Chimie — p. 1]**"]

    def test_no_match_without_prefix(self) -> None:
        m = _CITATION_RE.findall("[Einstein theorised]")
        assert m == []

    def test_multiple_citations(self) -> None:
        text = "[Livre A — p. 1] et [Livre B — p. 2]"
        m = _CITATION_RE.findall(text)
        assert len(m) == 2
