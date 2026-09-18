"""T015 Integration: épreuve blanche complète + reprise + correctif (012 US2).

012-real-learning-packs, FR-006/FR-007 :
création depuis blueprint → verrouillage au temps écoulé → correction /20
par compétence → reprise d'une épreuve interrompue (temps restant recalculé)
→ correctif ciblé sur la même compétence en séance suivante.

100 % offline : QuizEngine/TutorService avec client LLM scripté
(``chat_stream`` dépilé), ``@pytest.mark.asyncio`` explicite, aucune épreuve
réelle (squelettes titres/objectifs des packs uniquement).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ollama_tutor.client import StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor import exams as exams_mod
from src.ollama_tutor.tutor.assessment import QuizEngine
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _question_payload() -> dict:
    """Charge utile générique valable pour chaque kind objectif/open."""
    return {
        "question": "Question ?",
        "choices": ["A", "B"],
        "answer_index": 0,
        "answer": True,
        "model_answer": "A",
        "pairs": [{"left": "L1", "right": "R1"}, {"left": "L2", "right": "R2"}],
        "answer_order": [0, 1],
    }


class _ScriptedClient:
    """Faux client LLM : dépile une réponse JSON par appel chat_stream."""

    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)

    async def embed(self, model, inputs):
        return [[1.0, 0.0]]

    async def chat_stream(self, messages, model, **kwargs):
        payload = self._payloads.pop(0) if self._payloads else {}
        yield StreamEvent(kind="content", text=json.dumps(payload, ensure_ascii=False))
        yield StreamEvent(kind="done", stats=None)


def _make_service(tmp_path: Path, n_gen: int):
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    scripted = _ScriptedClient([_question_payload() for _ in range(n_gen)])
    svc = TutorService(store, scripted, config)
    # Court-circuite le routeur LLM : QuizEngine direct sur le scripté.
    svc.quiz_engine = QuizEngine(store, scripted, config)
    return svc, store, scripted


def _answer_half_split(questions: list[dict], scripted: _ScriptedClient) -> dict[str, object]:
    """Moitié juste / moitié fausse ; verdicts juges alignés sur les ouvertes.

    L'ordre de lecture SQL n'est pas l'ordre d'insertion : le partage se
    fait sur l'ordre lu (déterministe en count, pas en kinds), et les
    verdicts du juge LLM sont empilés après coup dans l'ordre des questions
    ouvertes répondues.
    """
    half = len(questions) // 2
    answers: dict[str, object] = {}
    for i, q in enumerate(questions):
        if i < half:
            answers[q["id"]] = _correct_answer(q)
            verdict = "correct"
        else:
            answers[q["id"]] = _wrong_answer(q)
            verdict = "incorrect"
        if q["type"] in ("open", "code"):
            scripted._payloads.append({"verdict": verdict, "feedback": "ok"})
    return answers


def _backdate_start(store: LibraryStore, exam_id: str, ago: timedelta) -> None:
    past = (datetime.now(timezone.utc) - ago).isoformat()
    store._conn.execute(
        "UPDATE quizzes SET started_at = ? WHERE id = ?", (past, exam_id)
    )
    store._conn.commit()


def _correct_answer(question: dict) -> object:
    qtype = question["type"]
    answer = question["answer"]
    if qtype == "mcq":
        return int(answer["index"])
    if qtype == "true_false":
        return bool(answer["value"])
    if qtype == "matching":
        return list(answer["order"])
    return str(answer.get("text", ""))


def _wrong_answer(question: dict) -> object:
    qtype = question["type"]
    answer = question["answer"]
    if qtype == "mcq":
        return 1 - int(answer["index"])
    if qtype == "true_false":
        return not bool(answer["value"])
    if qtype == "matching":
        return list(reversed(list(answer["order"]))) or [99]
    return "réponse fausse"


# ---------------------------------------------------------------------------
# Helpers purs : scaler /20, mention, verrouillage
# ---------------------------------------------------------------------------


def test_scale_and_mention_thresholds():
    assert exams_mod.scale_to_20(1.0, 2.0) == 10.0
    assert exams_mod.scale_to_20(0.0, 0.0) == 0.0
    assert exams_mod.mention(9.99) == "Insuffisant"
    assert exams_mod.mention(10.0) == "Passable"
    assert exams_mod.mention(20.0) == "Très bien"


def test_null_coefficients_tolerated_as_unit_weight():
    pack = exams_mod.get_blueprint("cm/bepc")
    weights = exams_mod.weights_for_pack(pack)
    assert weights
    assert all(w == 1.0 for w in weights.values())


# ---------------------------------------------------------------------------
# Épreuve blanche complète : création → verrouillage → correction /20
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_examen_blanc_lock_then_grade_per_competence(tmp_path: Path):
    svc, store, scripted = _make_service(tmp_path, n_gen=4)
    exam = await svc.create_blueprint_exam("cm/terminale-c", 180, size=4)
    assert exam["blueprint"] == "cm/terminale-c"
    assert exam["time_limit_s"] == 180 * 60
    assert exam["score_20"] is None
    kinds = {q["type"] for q in exam["questions"]}
    assert kinds <= {"mcq", "true_false", "matching", "open"}
    # Coeffs BEPC/probatoire/bac non confirmés (null) → poids unitaires.
    rows = store._conn.execute(
        "SELECT points FROM quiz_questions WHERE quiz_id = ?", (exam["id"],)
    ).fetchall()
    assert [r["points"] for r in rows] == [1.0] * 4

    # Temps écoulé → verrouillage.
    _backdate_start(store, exam["id"], timedelta(hours=4))
    locked = svc.lock_exam(exam["id"])
    assert locked["locked"] is True
    assert locked["statut"] == "verrouillée"

    # Correction : moitié juste, moitié fausse → 10/20, Passable.
    full = svc.quiz_engine.get_quiz(exam["id"], include_answers=True)
    assert full is not None
    questions = full["questions"]
    answers = _answer_half_split(questions, scripted)
    report = await svc.submit_answers(exam["id"], answers)
    assert report.score_20 == 10.0
    assert report.mention == "Passable"
    assert report.detail_competences is not None
    assert len(report.detail_competences) == len(questions)
    assert (
        sum(1 for e in report.detail_competences if e["score_20"] == 20.0)
        == len(questions) // 2
    )
    for entry in report.detail_competences:
        assert entry["score_20"] in (0.0, 20.0)
        assert entry["competence"]
    # Persisté : score_20 + blueprint_key lisibles côté lecture.
    row = store._conn.execute(
        "SELECT score_20, blueprint_key, status FROM quizzes WHERE id = ?",
        (exam["id"],),
    ).fetchone()
    assert float(row["score_20"]) == 10.0
    assert row["blueprint_key"] == "cm/terminale-c"
    assert row["status"] == "completed"


# ---------------------------------------------------------------------------
# Reprise d'une épreuve interrompue : temps restant recalculé
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprise_interrompue_recalcule_temps_restant(tmp_path: Path):
    svc, store, _scripted = _make_service(tmp_path, n_gen=2)
    exam = await svc.create_blueprint_exam("cm/bepc", 120, size=2)
    # Un tiers du temps consommé → reprise possible, ~80 min restantes.
    _backdate_start(store, exam["id"], timedelta(minutes=40))
    resumed = svc.resume_exam(exam["id"])
    assert resumed["statut"] == "interrompue"
    assert 75 * 60 <= resumed["remaining_s"] <= 80 * 60
    assert resumed["locked"] is False
    # Temps totalement écoulé → re-verrouillée, pas de reprise.
    _backdate_start(store, exam["id"], timedelta(minutes=200))
    relocked = svc.resume_exam(exam["id"])
    assert relocked["statut"] == "verrouillée"
    assert relocked["locked"] is True
    assert relocked["remaining_s"] == 0


@pytest.mark.asyncio
async def test_resume_completed_exam_rejected(tmp_path: Path):
    svc, _store, _scripted = _make_service(tmp_path, n_gen=1)
    exam = await svc.create_blueprint_exam("cm/bepc", 60, size=1)
    await svc.submit_answers(exam["id"], {})
    with pytest.raises(ValueError):
        svc.resume_exam(exam["id"])


# ---------------------------------------------------------------------------
# Correctif : la même compétence ratée refait surface en séance suivante
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correctif_meme_competence_seance_suivante(tmp_path: Path):
    svc, store, scripted = _make_service(tmp_path, n_gen=4)
    exam = await svc.create_blueprint_exam("cm/premiere-c", 120, size=4)
    full = svc.quiz_engine.get_quiz(exam["id"], include_answers=True)
    assert full is not None
    questions = full["questions"]
    answers = _answer_half_split(questions, scripted)
    report = await svc.submit_answers(exam["id"], answers)
    failed_ids = exams_mod.corrective_targets(report.per_question)
    assert failed_ids, "l'épreuve doit laisser des compétences à corriger"

    concepts = store.list_concepts(exam["subject_id"])
    by_id = {c.id: i for i, c in enumerate(concepts)}
    failed_pairs = [(by_id[cid], 0) for cid in failed_ids if cid in by_id]
    assert failed_pairs
    # Séance suivante : les mêmes compétences ratées refont surface
    # (l'ordre de lecture SQL n'est pas l'ordre d'insertion : inclusion
    # d'ensemble, comme en US1 — l'ordre strict vit dans le sampler pur).
    session = await svc.quiz_engine.create_quiz(
        exam["subject_id"], 3, ["mcq", "true_false", "open"], failed=failed_pairs
    )
    data = svc.quiz_engine.get_quiz(session.id)
    assert data is not None
    session_concepts = {q["concept_id"] for q in data["questions"]}
    assert set(failed_ids) <= session_concepts


# ---------------------------------------------------------------------------
# Idempotence : re-submit d'une épreuve déjà corrigée (F2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resubmit_completed_exam_returns_persisted_report(tmp_path: Path):
    """F2 — second submit après correction → rapport persisté, zéro rejouement.

    Les réponses du second appel sont volontairement différentes (tout faux) :
    sans garde, la re-correction donnerait un autre rapport et dupliquerait
    les effets (``error_history`` / ``quiz_answers`` / ``record_progress``).
    """
    svc, store, scripted = _make_service(tmp_path, n_gen=4)
    exam = await svc.create_blueprint_exam("cm/terminale-c", 180, size=4)
    full = svc.quiz_engine.get_quiz(exam["id"], include_answers=True)
    assert full is not None
    questions = full["questions"]
    answers = _answer_half_split(questions, scripted)
    report1 = await svc.submit_answers(exam["id"], answers)

    def _snapshot():
        n_err = store._conn.execute(
            "SELECT COUNT(*) AS n FROM error_history"
        ).fetchone()["n"]
        ans = {
            r["question_id"]: (r["verdict"], float(r["awarded"]), r["response"])
            for r in store._conn.execute(
                "SELECT question_id, verdict, awarded, response FROM quiz_answers"
            ).fetchall()
        }
        prog = {
            (r["subject_id"], r["concept_id"]): float(r["score"])
            for r in store._conn.execute(
                "SELECT subject_id, concept_id, score FROM progress"
            ).fetchall()
        }
        return int(n_err), ans, prog

    n_err1, ans1, prog1 = _snapshot()

    other = {q["id"]: _wrong_answer(q) for q in questions}
    report2 = await svc.submit_answers(exam["id"], other)

    assert report2.to_dict() == report1.to_dict()
    n_err2, ans2, prog2 = _snapshot()
    assert n_err2 == n_err1
    assert ans2 == ans1
    assert prog2 == prog1
