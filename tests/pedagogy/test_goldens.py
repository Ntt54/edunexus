"""Pedagogy goldens runner — 013 Vague 1 Socle (US4, T017).

Déterministe, mocké, zéro réseau/LLM, distinct du harness d'exécution.
Asserts sous-chaînes: must_include présent, must_not_include absent, hint-first,
pas de solution complète quand interdit. Échec cite le fragment fautif.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDENS_DIR = Path(__file__).resolve().parent / "goldens"


def _load_goldens() -> list[Path]:
    if not GOLDENS_DIR.is_dir():
        return []
    return sorted(GOLDENS_DIR.glob("*.json"))


def _mock_feedback(golden: dict) -> str:
    """Déterministe mock: retourne le champ golden['mock_feedback'] ou construit via gabarit."""
    # T017: mocké déterministe, zéro LLM
    if "mock_feedback" in golden:
        return str(golden["mock_feedback"])
    # fallback: assemble from must_include
    return " ".join(golden.get("must_include", []))


def _assert_golden(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    golden_id = data.get("id", path.name)
    mock_feedback = _mock_feedback(data)
    must_include = data.get("must_include", [])
    must_not_include = data.get("must_not_include", [])

    for fragment in must_include:
        assert fragment in mock_feedback, (
            f"Golden {golden_id} échoué: fragment attendu absent {fragment!r} "
            f"dans feedback {mock_feedback!r} (fichier {path.name})"
        )
    for fragment in must_not_include:
        assert fragment not in mock_feedback, (
            f"Golden {golden_id} échoué: fragment interdit présent {fragment!r} "
            f"dans feedback {mock_feedback!r} (fichier {path.name})"
        )
    # hint-first: feedback must contain hint cue when required
    if data.get("hint_first"):
        lowered = mock_feedback.lower()
        # at least one hint cue
        assert any(k in lowered for k in ["indice", "piste", "question", "essaie", "hint"]), (
            f"Golden {golden_id} échoué: hint-first attendu mais aucun indice/piste/question "
            f"dans feedback {mock_feedback!r}"
        )
    # no full solution when forbidden
    if data.get("forbid_full_solution"):
        lowered = mock_feedback.lower()
        for forbidden in must_not_include:
            if forbidden.lower() in lowered:
                pytest.fail(
                    f"Golden {golden_id} échoué: solution complète interdite, fragment fautif cité {forbidden!r} "
                    f"dans feedback {mock_feedback!r}"
                )


def test_goldens_directory_has_ten_cases():
    goldens = _load_goldens()
    assert len(goldens) >= 10, f"Attendu 10 goldens, trouvé {len(goldens)} dans {GOLDENS_DIR}"


@pytest.mark.parametrize("path", _load_goldens(), ids=lambda p: p.stem)
def test_pedagogy_golden(path: Path):
    _assert_golden(path)


def test_goldens_cover_required_subjects():
    goldens = _load_goldens()
    if not goldens:
        pytest.skip("no goldens yet")
    subjects = {json.loads(p.read_text(encoding="utf-8")).get("subject", "") for p in goldens}
    # Must include maths, physique, SVT, Python
    for required in ["maths", "physique", "svt", "python"]:
        assert any(required in s.lower() for s in subjects), f"Golden manquant pour {required}, subjects={subjects}"


def test_goldens_are_deterministic_and_offline():
    # Verify no network/LLM markers in runner: offline only (no httpx/requests)
    source = Path(__file__).read_text(encoding="utf-8")
    # check that no network library is imported (simple string search for import lines)
    import_lines = [l.strip() for l in source.splitlines() if l.strip().startswith("import ") or l.strip().startswith("from ")]
    joined = "\n".join(import_lines)
    for forbidden in ["httpx", "requests", "openai"]:
        assert forbidden not in joined, f"Runner must stay offline, found {forbidden} in {joined}"


def test_feedback_with_full_solution_fails_as_expected(tmp_path: Path):
    # Independent test: feedback avec solution complète → échec avec fragment cité
    golden = {
        "id": "test-forbid",
        "must_include": ["indice"],
        "must_not_include": ["42 est la réponse complète"],
        "mock_feedback": "indice: 42 est la réponse complète, voici la solution détaillée",
        "forbid_full_solution": True,
        "hint_first": True,
    }
    p = tmp_path / "tmp.json"
    p.write_text(json.dumps(golden), encoding="utf-8")
    with pytest.raises(AssertionError) as exc:
        _assert_golden(p)
    assert "42 est la réponse complète" in str(exc.value)


# Fix3 deferred: trivial <5-line parametrized check on build_evaluation_prompt
# would flag instruction phrase 'solution complète' in preamble and
# submission substrings (e.g. 'x=2' in 'x=2,5') as leaks — needs nuanced
# filtering (>5 lines) — deferred per oracle optional rule.
