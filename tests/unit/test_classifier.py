"""Unit tests for the rule-based domain classifier (Feature 006).

Tests classify_rules() from src.ollama_tutor.tutor.classifier — no LLM or
network required.
"""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.classifier import (
    DOMAIN_GENERIQUE,
    DOMAIN_LANGUES,
    DOMAIN_MATHEMATIQUES,
    DOMAIN_PROGRAMMATION,
    DOMAIN_SCIENCES,
    classify_rules,
)


class TestClassifyRules:
    def test_programmation_python(self):
        text = "def hello():\n    print('world')\nimport os"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_PROGRAMMATION
        assert confidence > 0.3

    def test_programmation_javascript(self):
        text = "const x = 42; function add(a, b) { return a + b; }"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_PROGRAMMATION

    def test_programmation_rust(self):
        text = "fn main() { let x: i32 = 42; println!(\"{x}\"); }"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_PROGRAMMATION

    def test_programmation_keywords(self):
        text = "Le framework utilise une API REST et le serveur compile le code"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_PROGRAMMATION

    def test_mathematiques_formulas(self):
        text = "La somme ∑ de la série converge vers π/4"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_MATHEMATIQUES

    def test_mathematiques_keywords(self):
        text = "Le théorème de convergence dérive de l'hypothèse de la démonstration"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_MATHEMATIQUES

    def test_mathematiques_unicode_symbols(self):
        text = "Calculer l'intégrale ∫ de f(x)dx entre α et β"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_MATHEMATIQUES

    def test_sciences(self):
        text = "L'atome se compose de protons, neutrons et électrons. La molécule d'eau..."
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_SCIENCES

    def test_sciences_biology(self):
        text = "La mitose est le processus de division cellulaire. L'ADN code les protéines."
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_SCIENCES

    def test_langues(self):
        text = "La conjugaison du verbe être au subjonctif présent. La grammaire de la phrase."
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_LANGUES

    def test_langues_grammar(self):
        text = "Le sujet et le COD de la phrase. L'adjectif s'accorde avec le nom."
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_LANGUES

    def test_generique(self):
        text = "Aujourd'hui nous allons visiter le musée"
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_GENERIQUE

    def test_empty_text(self):
        domain, confidence = classify_rules("")
        assert domain == DOMAIN_GENERIQUE
        assert confidence <= 0.1

    def test_whitespace_only(self):
        domain, confidence = classify_rules("   \n  \t  ")
        assert domain == DOMAIN_GENERIQUE
        assert confidence <= 0.1

    def test_confidence_bounds(self):
        _, conf = classify_rules("def class import return function")
        assert 0.0 <= conf <= 1.0

    def test_confidence_at_least_floor_when_match(self):
        """When there's at least one match, confidence should be >= 0.3."""
        _, conf = classify_rules("variable")
        assert conf >= 0.3

    def test_confidence_zero_match_returns_low(self):
        """When no keyword matches, confidence is 0.1."""
        _, conf = classify_rules("xyzzy nothing matches here foo bar")
        assert conf == 0.1

    def test_mixed_domainProgramming_wins(self):
        """When programming keywords outnumber others, programming wins."""
        text = (
            "def fonction():\n"
            "    import os\n"
            "    return os.path.exists('test')\n"
            "class MyClass:\n"
            "    pass"
        )
        domain, _ = classify_rules(text)
        assert domain == DOMAIN_PROGRAMMATION

    def test_case_insensitive(self):
        """Keywords should match regardless of case."""
        text = "The FUNCTION returns a VARIABLE. IMPORT os module."
        domain, confidence = classify_rules(text)
        assert domain == DOMAIN_PROGRAMMATION
        assert confidence > 0.3
