"""T008 Integration: recall_written grading + interleaved session (012 US1).

012-real-learning-packs, FR-002/FR-003 :
- kind ``recall_written`` : rendu réponse cachée, correction via le juge LLM
  (fallback exact-match offline) ;
- séance entremêlée : mix de 3 types, aucun bloc mono-type, les ratés
  refont surface en fin de séance.

100 % offline : QuizEngine avec faux client ``chat_stream`` scripté,
``@pytest.mark.asyncio`` explicite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ollama_tutor.client import InferenceStats, StreamEvent
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.adaptation import build_interleaved_session
from src.ollama_tutor.tutor.assessment import QuizEngine, _VALID_Q_KINDS
from src.ollama_tutor.tutor.store import LibraryStore


class _ScriptedClient:
    """Faux client LLM : dépile une réponse JSON par appel chat_stream."""

    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls = 0

    async def embed(self, model, inputs):
        return [[1.0, 0.0]]

    async def chat_stream(self, messages, model, **kwargs):
        self.calls += 1
        payload = self._payloads.pop(0) if self._payloads else {}
        yield StreamEvent(kind="content", text=json.dumps(payload, ensure_ascii=False))
        yield StreamEvent(kind="done", stats=None)


class _BoomClient:
    """Client hors-ligne : tout appel LLM lève (force le fallback exact)."""

    async def embed(self, model, inputs):
        raise RuntimeError("offline")

    async def chat_stream(self, messages, model, **kwargs):
        raise RuntimeError("offline")
        yield  # pragma: no cover — générateur async


def _quiz_question_payload(question: str, model_answer: str) -> dict:
    return {"question": question, "model_answer": model_answer}


def _judge_payload(verdict: str) -> dict:
    return {"verdict": verdict, "feedback": f"feedback {verdict}"}


def _seed_concepts(store: LibraryStore, subject_id: str, n: int = 3):
    return [store.create_concept(subject_id, f"Notion {i}") for i in range(n)]


def test_recall_written_is_valid_kind():
    assert "recall_written" in _VALID_Q_KINDS


@pytest.mark.asyncio
async def test_recall_written_quiz_generation(tmp_path: Path):
    """create_quiz accepte recall_written ; réponses attendues cachées."""
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Histoire")
    _seed_concepts(store, subject.id)
    gen = [_quiz_question_payload(f"Q{i} ?", f"R{i}") for i in range(3)]
    engine = QuizEngine(store, _ScriptedClient(gen), Config(config_dir=tmp_path))
    quiz = await engine.create_quiz(subject.id, 3, ["recall_written"])
    data = engine.get_quiz(quiz.id)
    assert data is not None
    assert len(data["questions"]) == 3
    assert {q["type"] for q in data["questions"]} == {"recall_written"}
    # Réponse attendue cachée avant correction.
    for q in data["questions"]:
        assert "answer" not in q


@pytest.mark.asyncio
async def test_recall_written_grading_llm_verdict(tmp_path: Path):
    """Le juge LLM corrige les réponses rédigées (correct / incorrect)."""
    store = LibraryStore(tmp_path)
    subject = store.create_subject("SVT")
    _seed_concepts(store, subject.id)
    gen = [_quiz_question_payload("Définis la photosynthèse.", "conversion lumière")]
    judge = [_judge_payload("correct"), _judge_payload("incorrect")]
    engine = QuizEngine(store, _ScriptedClient(gen + judge[:1]), Config(config_dir=tmp_path))
    quiz = await engine.create_quiz(subject.id, 1, ["recall_written"])
    qid = engine.get_quiz(quiz.id)["questions"][0]["id"]
    report = await engine.submit_answers(quiz.id, {qid: "la conversion de la lumière"})
    assert report.per_question[0]["verdict"] == "correct"

    engine2 = QuizEngine(store, _ScriptedClient(gen + judge[1:]), Config(config_dir=tmp_path))
    quiz2 = await engine2.create_quiz(subject.id, 1, ["recall_written"])
    qid2 = engine2.get_quiz(quiz2.id)["questions"][0]["id"]
    report2 = await engine2.submit_answers(quiz2.id, {qid2: "n'importe quoi"})
    assert report2.per_question[0]["verdict"] == "incorrect"


@pytest.mark.asyncio
async def test_recall_written_exact_match_fallback_offline(tmp_path: Path):
    """Sans LLM, une réponse exacte vaut correct, une fausse vaut incorrect."""
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Maths")
    _seed_concepts(store, subject.id)
    engine = QuizEngine(store, _BoomClient(), Config(config_dir=tmp_path))
    quiz = await engine.create_quiz(subject.id, 2, ["recall_written"])
    # Génération dégradée offline : réponses modèle vides → on les renseigne.
    questions = engine.get_quiz(quiz.id, include_answers=True)["questions"]
    assert len(questions) == 2
    for i, q in enumerate(questions):
        store._conn.execute(
            "UPDATE quiz_questions SET answer = ? WHERE id = ?",
            (json.dumps({"text": f"attendue {i}"}), q["id"]),
        )
    store._conn.commit()
    report = await engine.submit_answers(
        quiz.id, {questions[0]["id"]: "attendue 0", questions[1]["id"]: "ratée"}
    )
    by_id = {p["question_id"]: p["verdict"] for p in report.per_question}
    assert by_id[questions[0]["id"]] == "correct"
    assert by_id[questions[1]["id"]] == "incorrect"


def test_interleaved_session_mixes_three_types():
    """Le sampler mélange 3 types sans bloc mono-type."""
    kinds = ["mcq", "true_false", "recall_written"]
    plan = build_interleaved_session(9, 3, kinds)
    assert len(plan) == 9
    used = {kinds[ki] for _, ki in plan}
    assert used == set(kinds)
    # Aucun bloc : jamais deux questions adjacentes du même type.
    seq = [ki for _, ki in plan]
    assert all(a != b for a, b in zip(seq, seq[1:]))


def test_interleaved_failed_resurface_at_end():
    """Les ratés refont surface en fin de séance."""
    kinds = ["mcq", "true_false", "recall_written"]
    failed = [(0, 1), (1, 2)]
    plan = build_interleaved_session(6, 3, kinds, failed=failed)
    assert len(plan) == 8
    assert plan[-2:] == failed
    # Jonction séance/ratés : pas de bloc mono-type à la jointure.
    seq = [ki for _, ki in plan]
    assert seq[len(seq) - len(failed) - 1] != seq[len(seq) - len(failed)]


@pytest.mark.asyncio
async def test_interleaved_quiz_no_single_block(tmp_path: Path):
    """Un quiz moteur de 9 questions couvre 3 types sans bloc unique.

    (L'ordre de lecture SQL n'est pas l'ordre d'insertion : l'adjacence
    stricte est vérifiée au niveau du sampler pur ci-dessus.)
    """
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Français")
    _seed_concepts(store, subject.id)
    kinds = ["mcq", "true_false", "recall_written"]
    gen: list[dict] = []
    for i in range(9):
        k = ["mcq", "true_false", "open"][i % 3]
        if k == "mcq":
            gen.append({"question": f"Q{i} ?", "choices": ["A", "B"], "answer_index": 0})
        elif k == "true_false":
            gen.append({"question": f"Q{i} ?", "answer": True})
        else:
            gen.append(_quiz_question_payload(f"Q{i} ?", f"R{i}"))
    engine = QuizEngine(store, _ScriptedClient(gen), Config(config_dir=tmp_path))
    quiz = await engine.create_quiz(subject.id, 9, kinds)
    types = [q["type"] for q in engine.get_quiz(quiz.id)["questions"]]
    assert sorted(types) == sorted(
        ["mcq", "mcq", "mcq", "true_false", "true_false", "true_false"]
        + ["recall_written"] * 3
    )
    assert not all(t == types[0] for t in types)
