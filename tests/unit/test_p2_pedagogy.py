"""US7 P2-Pédagogie/UI (T044) — docs_refs, feedback standard, HITL.

Adapté de ``autreprojet/python-tutor-main`` (``docs_refs.py`` allowlist +
lookup, ``main.py`` evidence packet + verdict + next_step) et
``autreprojet/open-tutor-ai-CE-main`` (``self_regulation.py`` FeedbackForm
+ garde owner-ou-admin). KaTeX/ThinkBox (tutor-gpt) testés côté fichier
statique. 100 % offline, aucun réseau/LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ollama_tutor.tutor.assessment import (
    classify_assessment_verdict,
    extract_next_step,
)
from src.ollama_tutor.tutor.docs_refs import (
    DEFAULT_ALLOWED_HOSTS,
    filter_allowlisted,
    is_allowlisted,
    lookup,
)
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# T045 — docs_refs : allowlist + lookup offline-first
# ---------------------------------------------------------------------------


def test_allowlist_hosts() -> None:
    assert "docs.python.org" in DEFAULT_ALLOWED_HOSTS
    assert is_allowlisted("https://docs.python.org/3/library/functions.html")
    assert not is_allowlisted("https://evil.example.com/docs")
    assert not is_allowlisted("ftp://docs.python.org/x")
    assert not is_allowlisted("https://docs.python.org.evil.com/x")
    assert is_allowlisted("https://docs.python.org:443/x")
    assert not is_allowlisted("pas une url")


def test_allowlist_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUTOR_DOCS_ALLOWLIST", "ecole.example.fr, docs.python.org")
    assert is_allowlisted("https://ecole.example.fr/cours")
    assert not is_allowlisted("https://numpy.org/doc")
    monkeypatch.delenv("TUTOR_DOCS_ALLOWLIST")
    assert is_allowlisted("https://numpy.org/doc")


def test_filter_allowlisted() -> None:
    urls = [
        "https://docs.python.org/3/library/functions.html#print",
        "https://malicious.example.com/steal",
    ]
    assert filter_allowlisted(urls) == urls[:1]


def test_lookup_offline_curated_max4() -> None:
    res = lookup(
        code="for i in range(10):\n    print(len(str(i)))",
        concepts=["boucle for", "print"],
        verify_online=False,
    )
    assert len(res.refs) <= 4, "max 4 refs (source)"
    assert res.refs, "tokens connus ⇒ refs curées"
    assert res.online is False and res.online_ok is False
    assert res.note is None
    assert all(r.source == "curated" for r in res.refs)


def test_lookup_exercise_refs_filtered_and_capped() -> None:
    res = lookup(
        code="x = 1",
        exercise_refs=[
            "https://docs.python.org/3/library/functions.html#print",
            "https://evil.example.com/x",
            "https://docs.pytest.org/en/stable/",
            "https://numpy.org/doc/stable/",
            "https://pandas.pydata.org/docs/",
            "https://matplotlib.org/stable/",
        ],
        verify_online=False,
    )
    urls = [r.url for r in res.refs]
    assert "https://evil.example.com/x" not in urls
    assert len(urls) <= 4
    assert any(r.source == "exercise" for r in res.refs)


def test_lookup_empty_is_stable() -> None:
    res = lookup(code="x = 1", verify_online=False)
    assert res.refs == [] and res.online_ok is False


# ---------------------------------------------------------------------------
# T047 — feedback standard : verdict + next_step
# ---------------------------------------------------------------------------


def test_classify_assessment_verdict_lines() -> None:
    assert classify_assessment_verdict("passed\nbon travail") == "passed"
    assert classify_assessment_verdict("needs_work: revoir la boucle") == "needs_work"
    assert classify_assessment_verdict("Needs Work — presque") == "needs_work"
    assert classify_assessment_verdict("error: traceback...") == "error"


def test_classify_assessment_verdict_evidence_fallback() -> None:
    assert classify_assessment_verdict("du texte", exit_code=1, stderr="Traceback") == "error"
    assert classify_assessment_verdict("du texte", exit_code=1, stderr="") == "needs_work"
    assert classify_assessment_verdict("du texte", exit_code=0, timed_out=True) == "needs_work"
    assert classify_assessment_verdict("du texte", timed_out=True, stderr="timeout") == "error"
    assert classify_assessment_verdict("du texte", exit_code=0) == "needs_work"
    assert classify_assessment_verdict("") == "needs_work"


def test_extract_next_step() -> None:
    assert extract_next_step("passed\nNext step: revois range().") == "revois range()."
    assert extract_next_step("- Next step — tester avec 0.") == "tester avec 0."
    assert extract_next_step("1. Next step: x") == "x"
    assert extract_next_step("pas d'étape ici") is None
    assert extract_next_step("") is None


def test_grade_answer_returns_next_step(tmp_path: Path) -> None:
    import asyncio

    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.models import Exercise
    from src.ollama_tutor.tutor.service import TutorService

    store = LibraryStore(tmp_path)
    subject = store.create_subject("Prog")
    concept = store.create_concept(subject.id, "Boucles")
    store._conn.execute(
        "INSERT INTO exercises (id, subject_id, concept_id, difficulty, statement,"
        " solution, hints, status, created_at) VALUES ('ex1', ?, ?, 'easy',"
        " 'stmt', 'sol', '[]', 'open', 'now')",
        (subject.id, concept.id),
    )
    store._conn.commit()
    exercise = store.get_exercise("ex1")
    assert exercise is not None

    class _LLM:
        async def chat_stream(self, messages, model, options=None):
            from src.ollama_tutor.client import StreamEvent

            text = (
                '{"verdict": "incorrect", "feedback": "La boucle ne termine pas.\\n'
                'Next step: ajoute un compteur."}'
            )

            class _Ev:
                def __init__(self, kind, text=""):
                    self.kind = kind
                    self.text = text

            yield _Ev("content", text)
            yield _Ev("done")

    svc = TutorService(store, _LLM(), Config(config_dir=tmp_path))
    attempt = asyncio.run(svc.grade_answer("ex1", "mauvaise réponse"))
    assert attempt.verdict == "incorrect"
    assert attempt.next_step == "ajoute un compteur."


# ---------------------------------------------------------------------------
# T048/T049 — HITL : tables + wiring + garde owner-ou-admin
# ---------------------------------------------------------------------------


def _svc(tmp_path: Path):
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService

    store = LibraryStore(tmp_path)
    return TutorService(store, None, Config(config_dir=tmp_path)), store


def test_feedback_requires_rating_and_comment(tmp_path: Path) -> None:
    svc, _store = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.submit_feedback("book", "b1", rating=0, comment="nul", owner_id="u1")
    with pytest.raises(ValueError):
        svc.submit_feedback("book", "b1", rating=3, comment="  ", owner_id="u1")
    with pytest.raises(ValueError):
        svc.submit_feedback("book", "b1", rating=3, comment="ok")


def test_feedback_crud_and_owner_guard(tmp_path: Path) -> None:
    svc, store = _svc(tmp_path)
    src = tmp_path / "cours.txt"
    src.write_text("contenu " * 20, encoding="utf-8")
    book = svc.import_and_index("SVT", str(src), background=False)

    fb = svc.submit_feedback(
        "book", book.id, rating=4, comment="Très clair", owner_id="lea"
    )
    assert fb["rating"] == 4 and fb["comment"] == "Très clair"
    assert fb["owner_id"] == "lea"

    got = svc.get_feedback(fb["id"])
    assert got is not None and got["id"] == fb["id"]
    assert len(svc.list_feedback("book", book.id)) == 1

    # Owner modifie : ok.
    updated = svc.update_feedback(
        fb["id"], comment="Parfait", owner_id="lea", is_admin=False
    )
    assert updated["comment"] == "Parfait"
    # Tiers non-admin : refusé.
    with pytest.raises(PermissionError):
        svc.update_feedback(fb["id"], comment="hack", owner_id="mia", is_admin=False)
    with pytest.raises(PermissionError):
        svc.delete_feedback(fb["id"], owner_id="mia", is_admin=False)
    # Admin : ok.
    assert svc.delete_feedback(fb["id"], owner_id="root", is_admin=True) is True
    assert svc.get_feedback(fb["id"]) is None


def test_feedback_unknown_target(tmp_path: Path) -> None:
    from src.ollama_tutor.tutor.errors import NotFoundError

    svc, _store = _svc(tmp_path)
    with pytest.raises(NotFoundError):
        svc.submit_feedback("book", "inexistant", rating=5, comment="x", owner_id="u")


def test_feedback_migration_idempotent(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    store._migrate_p2_pedagogy()
    store._migrate_p2_pedagogy()
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(feedback)")}
    assert {"id", "target_type", "target_id", "owner_id", "rating", "comment"} <= cols
