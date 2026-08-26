"""Exercise generation, hint ladder, grading, code analysis (US4).

Builds the LLM prompts and parses the structured responses for practice:
- ``generate_exercise`` → statement + solution + 3 pre-generated hint stages
  (so revealing a hint needs NO extra LLM call — FR-016/017);
- ``grade_answer`` → structured verdict (correct/incorrect/partial) + feedback;
- ``analyze_code`` → error-category taxonomy prompt (FR-019) with an optional
  execution-result context hook (FR-020 stub).

UI-framework-free by contract (no textual/fastapi imports). Prompt builders
live here (not in ``prompts.py``) so this lane stays self-contained.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..models import Message, MessageRole, OllamaOptions
from .models import (
    ExamSession,
    Exercise,
    ExerciseAttempt,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    _coerce_json,
    _now_iso,
    _uid,
)
from .store import LibraryStore


@dataclass
class AttemptResult:
    """Outcome of grading an answer (contract: TutorService.grade_answer).

    ``solution`` is always ``None`` here — INVARIANT 3 forbids returning the
    solution from grading; it is only ever returned by ``request_solution``.
    """

    verdict: str | None
    feedback: str
    hint_level: int
    hint: str | None
    solution: str | None = None


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_exercise_prompt(
    concept_name: str,
    difficulty: str,
    level: str,
    past_errors: list[str] | None = None,
) -> list[Message]:
    """Build the messages that ask the LLM to generate an exercise.

    The model is instructed to reply with strict JSON carrying the statement,
    the solution (withheld from the learner by the REST layer), and exactly
    three progressively revealing hint stages.
    """
    system = (
        "Tu es un tuteur qui crée un exercice d'entraînement en français sur un "
        "concept précis. Réponds STRICTEMENT en JSON, sans aucun texte autour, "
        "selon la forme : "
        '{"statement": "...", "solution": "...", "hints": ["...", "...", "..."]}. '
        "Le champ 'hints' contient exactement 3 étapes d'indice de plus en plus "
        "précises (indice léger, puis indice plus direct, puis explication "
        "détaillée). La solution ne doit JAMAIS apparaître dans l'énoncé ni "
        "dans les indices."
    )
    past = ""
    if past_errors:
        past = (
            "Erreurs fréquentes de l'élève sur ce concept à prendre en compte : "
            + " ; ".join(past_errors)
            + "."
        )
    user = (
        f"Concept : {concept_name}\n"
        f"Difficulté : {difficulty}\n"
        f"Niveau de l'élève : {level}\n"
        f"{past}\n"
        "Génère un exercice avec une solution et 3 indices progressifs."
    )
    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


def build_grade_prompt(exercise: Exercise, answer: str) -> list[Message]:
    """Build the messages that ask the LLM to grade a learner's answer."""
    system = (
        "Tu es un correcteur qui évalue la réponse d'un élève à un exercice. "
        "Réponds STRICTEMENT en JSON, sans aucun texte autour, selon la forme : "
        '{"verdict": "correct|partial|incorrect", "feedback": "..."}. '
        "verdict='correct' si la réponse résout l'exercice, 'partial' si elle "
        "contient une partie juste mais incomplète ou avec une erreur mineure, "
        "'incorrect' sinon. 'feedback' explique brièvement pourquoi (utile pour "
        "la remédiation). Ne révèle jamais la solution complète."
    )
    user = (
        f"Énoncé :\n{exercise.statement}\n\n"
        f"Solution attendue (à ne pas révéler) :\n{exercise.solution}\n\n"
        f"Réponse de l'élève :\n{answer}\n\n"
        "Donne le verdict et un retour constructif."
    )
    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


# FR-019: the six error categories used by code analysis.
CODE_ERROR_CATEGORIES = (
    "syntax (erreur de syntaxe), runtime (erreur à l'exécution), "
    "logic (erreur de logique/algorithme), practice (mauvaise pratique), "
    "design (problème de conception/structure), "
    "misconception (méconception du concept)"
)


def build_code_analysis_prompt(
    code: str,
    context: str = "",
    execution_result: str | None = None,
) -> list[Message]:
    """Build the messages that ask the LLM to analyze submitted code (FR-019).

    ``execution_result`` is an optional hook (FR-020 stub): when provided it is
    injected into the prompt so the analysis can reference actual run output.
    """
    system = (
        "Tu es un analyste de code pédagogique. Tu analyses un code soumis par "
        "un élève et identifies les problèmes en les classant dans l'une de ces "
        f"catégories : {CODE_ERROR_CATEGORIES}. "
        "Réponds en français, de façon constructive et encourageante, en "
        "citant les lignes concernées quand c'est possible."
    )
    user = f"Contexte : {context or 'aucun'}\n\nCode à analyser :\n```\n{code}\n```"
    if execution_result is not None:
        user += f"\n\nRésultat d'exécution fourni :\n{execution_result}"
    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from an LLM response."""
    if not text:
        return {}
    s = text.strip()
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def parse_exercise_response(text: str) -> dict[str, Any]:
    """Parse the LLM exercise JSON into ``{statement, solution, hints}``."""
    data = _extract_json(text)
    statement = str(data.get("statement", "")).strip()
    solution = str(data.get("solution", "")).strip()
    hints = data.get("hints", [])
    if isinstance(hints, list):
        hints = [str(h) for h in hints][:3]
    while len(hints) < 3:
        hints.append("")
    return {"statement": statement, "solution": solution, "hints": hints}


def parse_grade_response(text: str) -> tuple[str, str]:
    """Parse the LLM grade JSON into ``(verdict, feedback)``."""
    data = _extract_json(text)
    verdict = str(data.get("verdict", "incorrect")).strip().lower()
    if verdict not in ("correct", "partial", "incorrect"):
        verdict = "incorrect"
    feedback = str(data.get("feedback", "")).strip()
    return verdict, feedback


# ---------------------------------------------------------------------------
# Hint ladder (pure logic, no LLM)
# ---------------------------------------------------------------------------

def next_hint(exercise: Exercise) -> tuple[int, str | None]:
    """Return ``(new_hint_level, revealed_hint)`` after escalating one stage.

    Caps at hint_level 3 (the explanation stage). Returns the hint text for the
    newly revealed stage, or ``None`` when already at the maximum.
    """
    if exercise.hint_level >= 3:
        return exercise.hint_level, None
    new_level = exercise.hint_level + 1
    revealed = exercise.hints[new_level - 1] if new_level - 1 < len(exercise.hints) else None
    return new_level, revealed


# ---------------------------------------------------------------------------
# Knowledge preparation prompts (US5 / T039): flashcards + glossary from chunks
# ---------------------------------------------------------------------------

def build_prepare_prompt(chapter: str, batch_text: str) -> list[Message]:
    """Ask the LLM to extract concepts, flashcards and glossary from a batch.

    The model replies with strict JSON carrying ``concepts``, ``flashcards`` and
    ``glossary`` lists (see ``parse_prepare_response``).
    """
    system = (
        "Tu es un préparateur pédagogique. À partir d'un extrait d'un livre, tu "
        "extrais les notions clés, des flashcards (question/réponse/level) et des "
        "définitions de glossaire. Réponds STRICTEMENT en JSON, sans texte autour, "
        "selon la forme : "
        '{"concepts": ["..."], '
        '"flashcards": [{"concept": "...", "question": "...", "answer": "...", '
        '"level": "beginner|intermediate|advanced|expert"}], '
        '"glossary": [{"term": "...", "definition": "..."}]}. '
        "Le niveau d'une flashcard reflète la difficulté de la question."
    )
    user = (
        f"Chapitre / section : {chapter or 'général'}\n\n"
        f"Extrait :\n{batch_text}\n\n"
        "Extrais les notions, flashcards et termes de glossaire pertinents."
    )
    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


def parse_prepare_response(text: str) -> dict[str, Any]:
    """Parse the LLM prepare JSON into concepts/flashcards/glossary lists."""
    data = _extract_json(text)
    concepts = data.get("concepts", []) or []
    if isinstance(concepts, list):
        concepts = [str(c).strip() for c in concepts if str(c).strip()]
    flashcards = data.get("flashcards", []) or []
    glossary = data.get("glossary", []) or []
    return {"concepts": concepts, "flashcards": flashcards, "glossary": glossary}


# ---------------------------------------------------------------------------
# Quiz / exam question generation (US5 / T040)
# ---------------------------------------------------------------------------

_VALID_Q_KINDS = {"mcq", "true_false", "open", "matching", "code"}


def build_quiz_question_prompt(
    concept_name: str, kind: str, size_hint: int = 1
) -> list[Message]:
    """Ask the LLM to generate one quiz question of ``kind`` about a concept."""
    kind_spec: dict[str, str] = {
        "mcq": (
            '{"question": "...", "choices": ["A", "B", "C", "D"], "answer_index": 0}'
        ),
        "true_false": '{"question": "...", "answer": true}',
        "open": '{"question": "...", "model_answer": "..."}',
        "matching": (
            '{"question": "...", "pairs": [{"left": "...", "right": "..."}], '
            '"answer_order": [0,1,2,3]}'
        ),
        "code": (
            '{"question": "...", "language": "python", "model_answer": "..."}'
        ),
    }
    spec = kind_spec.get(kind, kind_spec["open"])
    system = (
        "Tu es un évaluateur qui crée une question d'entraînement en français sur "
        "un concept précis. Réponds STRICTEMENT en JSON, sans texte autour, selon "
        f"la forme : {spec}. "
        "Pour 'matching', 'answer_order' est la liste des indices du bon 'right' "
        "pour chaque 'left' (dans l'ordre des 'left'). Pour 'mcq', 'answer_index' "
        "est l'indice (0-based) de la bonne réponse dans 'choices'."
    )
    user = (
        f"Concept : {concept_name}\n"
        f"Type de question : {kind}\n"
        "Génère une question claire et correcte."
    )
    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


def parse_quiz_question_response(text: str, kind: str) -> dict[str, Any]:
    """Parse an LLM quiz-question JSON into ``(payload, answer)`` pieces."""
    data = _extract_json(text)
    kind = kind or "open"
    question_text = str(data.get("question", "")).strip()
    if kind == "mcq":
        choices = data.get("choices", []) or []
        choices = [str(c) for c in choices][:4]
        while len(choices) < 2:
            choices.append("")
        payload = {"question": question_text, "choices": choices}
        answer = {"index": int(data.get("answer_index", 0) or 0)}
    elif kind == "true_false":
        payload = {"question": question_text}
        answer = {"value": bool(data.get("answer", False))}
    elif kind == "matching":
        pairs = data.get("pairs", []) or []
        norm_pairs = []
        for p in pairs:
            if isinstance(p, dict):
                norm_pairs.append(
                    {"left": str(p.get("left", "")), "right": str(p.get("right", ""))}
                )
        order = data.get("answer_order", []) or list(range(len(norm_pairs)))
        payload = {"question": question_text, "pairs": norm_pairs}
        answer = {"order": [int(x) for x in order][: len(norm_pairs)]}
    else:  # open / code
        payload = {"question": question_text, "language": str(data.get("language", "python") or "python")}
        answer = {"text": str(data.get("model_answer", "") or "")}
    return {"payload": payload, "answer": answer}


def build_open_judge_prompt(
    question: str, model_answer: str, response: str
) -> list[Message]:
    """Ask the LLM to judge an open/code answer against the model answer."""
    system = (
        "Tu es un correcteur. Tu compares la réponse d'un élève à une réponse "
        "modèle et décides si elle est correcte. Réponds STRICTEMENT en JSON, "
        'selon la forme : {"verdict": "correct|partial|incorrect", '
        '"feedback": "..."}. Un détail mineur ou une formulation différente mais '
        "équivalente ⇒ 'partial'."
    )
    user = (
        f"Question :\n{question}\n\n"
        f"Réponse modèle :\n{model_answer}\n\n"
        f"Réponse de l'élève :\n{response}\n\n"
        "Donne le verdict et un retour bref."
    )
    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


# ---------------------------------------------------------------------------
# Quiz report
# ---------------------------------------------------------------------------

@dataclass
class QuizReport:
    """Result of grading a quiz/exam (contract: QuizReport)."""

    score: float  # 0-100
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    per_question: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "per_question": self.per_question,
        }


# D7 mastery deltas for quiz/exam correction (research D7: ±10 quiz, ±8 exam).
# Kept separate from ``progress.D7_WEIGHTS`` so the US4 weight test stays exact.
_QUIZ_DELTA = 10.0
_EXAM_DELTA = 8.0


def _answer_to_text(qtype: str, payload: dict[str, Any], answer: dict[str, Any]) -> str:
    """Convert a stored correct answer to a human-readable string (T062)."""
    if qtype == "mcq":
        choices = payload.get("choices", [])
        idx = answer.get("index", -1)
        if 0 <= idx < len(choices):
            return str(choices[idx])
        return str(idx)
    elif qtype == "true_false":
        return "Vrai" if answer.get("value") else "Faux"
    elif qtype == "matching":
        pairs = payload.get("pairs", [])
        order = answer.get("order", [])
        parts = []
        for i, left_idx in enumerate(order):
            if i < len(pairs):
                left = pairs[i].get("left", "")
                right_idx = left_idx if isinstance(left_idx, int) and left_idx < len(pairs) else i
                right = pairs[right_idx].get("right", "") if right_idx < len(pairs) else ""
                parts.append(f"{left} → {right}")
        return "; ".join(parts)
    else:  # open / code
        return str(answer.get("text", ""))


def _response_to_text(qtype: str, payload: dict[str, Any], response: Any) -> str:
    """Convert a learner's response to a human-readable string (T062)."""
    if qtype == "mcq":
        choices = payload.get("choices", [])
        try:
            idx = int(response)
            if 0 <= idx < len(choices):
                return str(choices[idx])
        except (TypeError, ValueError):
            pass
        return str(response)
    elif qtype == "true_false":
        return "Vrai" if response else "Faux"
    elif qtype == "matching":
        pairs = payload.get("pairs", [])
        order = list(response) if isinstance(response, (list, tuple)) else []
        parts = []
        for i, left_idx in enumerate(order):
            if i < len(pairs):
                left = pairs[i].get("left", "")
                right_idx = left_idx if isinstance(left_idx, int) and left_idx < len(pairs) else i
                right = pairs[right_idx].get("right", "") if right_idx < len(pairs) else ""
                parts.append(f"{left} → {right}")
        return "; ".join(parts)
    else:  # open / code
        return str(response)


class QuizEngine:
    """Quiz/exam generation, correction and reporting (US5 / T040).

    Generates questions bound to a subject's concepts, corrects submissions
    (exact match for objective types, LLM-judged for open/code), enforces exam
    rules (no-hint gate, time-limit auto-submit) and updates mastery via D7
    weights. All persistence goes through the injected ``LibraryStore`` (direct
    SQL, no new store methods required). UI-framework-free by contract.
    """

    def __init__(self, store: LibraryStore, client: Any, config: Any) -> None:
        self.store = store
        self.client = client
        self.config = config

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def _llm_json(self, messages: list[Message]) -> dict[str, Any]:
        """Run a non-streaming LLM call and parse its JSON response."""
        parts: list[str] = []
        options = OllamaOptions(num_ctx=max(8192, getattr(self.config.options, "num_ctx", 0) or 0))
        async for ev in self.client.chat_stream(
            messages, self.config.tutor_model, options=options
        ):
            if ev.kind == "content":
                parts.append(ev.text)
        return _extract_json("".join(parts))

    async def create_quiz(
        self,
        subject_id: str,
        size: int,
        kinds: list[str],
        *,
        concepts: list[Any] | None = None,
    ) -> Quiz:
        """Generate a quiz of ``size`` questions across ``kinds`` bound to concepts.

        ``concepts`` (keyword-only) overrides the subject's full concept list
        (used by exam scoping); ``None`` keeps the legacy behavior.
        """
        return await self._create_assessment(
            subject_id, size, kinds, kind="quiz", concepts=concepts
        )

    async def create_exam(
        self,
        subject_id: str,
        size: int,
        time_limit_s: int,
        *,
        concepts: list[Any] | None = None,
    ) -> ExamSession:
        """Generate a timed, assistance-free exam (kind='exam', allow_help=False)."""
        quiz = await self._create_assessment(
            subject_id, size, ["mcq", "true_false", "open", "matching", "code"],
            kind="exam", time_limit_s=time_limit_s, concepts=concepts,
        )
        return ExamSession(
            id=quiz.id,
            subject_id=quiz.subject_id,
            time_limit_s=quiz.time_limit_s,
            started_at=quiz.started_at,
            finished_at=quiz.finished_at,
            score=quiz.score,
            report=quiz.report,
        )

    async def _create_assessment(
        self,
        subject_id: str,
        size: int,
        kinds: list[str],
        kind: str = "quiz",
        time_limit_s: int | None = None,
        concepts: list[Any] | None = None,
    ) -> Quiz:
        if concepts is None:
            concepts = self.store.list_concepts(subject_id)
        if not concepts:
            raise KeyError(f"No concepts in subject: {subject_id}")
        kinds = [k for k in kinds if k in _VALID_Q_KINDS] or ["open"]
        quiz = Quiz(
            id=_uid(),
            subject_id=subject_id,
            kind=kind,
            status="created",
            allow_help=(kind != "exam"),
            time_limit_s=time_limit_s if kind == "exam" else None,
            started_at=_now_iso(),
        )
        self._insert_quiz(quiz)
        total_points = 0.0
        for i in range(max(1, size)):
            concept = concepts[i % len(concepts)]
            qkind = kinds[i % len(kinds)]
            try:
                data = await self._llm_json(
                    build_quiz_question_prompt(concept.name, qkind)
                )
                parsed = parse_quiz_question_response(
                    json.dumps(data), qkind
                )
            except Exception:
                parsed = {"payload": {}, "answer": {}}
            points = 1.0
            total_points += points
            q = QuizQuestion(
                id=_uid(),
                quiz_id=quiz.id,
                type=qkind,
                payload=parsed["payload"],
                answer=parsed["answer"],
                concept_id=concept.id,
                points=points,
            )
            self._insert_question(q)
        # store total points on the quiz row via report-less update
        self.store._conn.execute(
            "UPDATE quizzes SET status = 'in_progress' WHERE id = ?", (quiz.id,)
        )
        self.store._conn.commit()
        quiz.status = "in_progress"
        return quiz

    # ------------------------------------------------------------------
    # Submission / correction
    # ------------------------------------------------------------------

    async def submit_answers(
        self,
        quiz_id: str,
        answers: dict[str, Any],
        hint_requested: bool = False,
    ) -> QuizReport:
        """Correct a quiz/exam submission and persist the report.

        Exam rules:
        - ``hint_requested`` on an exam ⇒ refuse (raises ``ExamHelpError`` so the
          REST layer can return 409).
        - a timed exam whose window has elapsed scores any unanswered question as
          incorrect (auto-submit semantics).
        """
        quiz = self._get_quiz_row(quiz_id)
        if quiz is None:
            raise KeyError(f"Unknown quiz: {quiz_id}")
        if quiz["kind"] == "exam" and hint_requested:
            raise ExamHelpError("L'aide est interdite pendant un examen.")
        questions = self._get_questions(quiz_id)
        if not questions:
            raise KeyError(f"Quiz has no questions: {quiz_id}")

        # Exam expiry: unanswered questions score as incorrect.
        expired = False
        if quiz["kind"] == "exam" and quiz.get("time_limit_s"):
            try:
                from datetime import datetime
                started = datetime.fromisoformat(quiz["started_at"])
                elapsed = (datetime.now(started.tzinfo) - started).total_seconds() \
                    if started.tzinfo else (datetime.now() - started).total_seconds()
                if elapsed > float(quiz["time_limit_s"]):
                    expired = True
            except Exception:
                expired = False

        concept_verdicts: dict[str, list[bool]] = {}
        per_question: list[dict[str, Any]] = []
        total_points = 0.0
        earned = 0.0

        # Pre-resolve concept names for error recording (T062).
        concept_ids_needed = {q.get("concept_id") for q in questions if q.get("concept_id")}
        concept_name_map: dict[str, str] = {}
        if concept_ids_needed:
            ids_list = list(concept_ids_needed)
            placeholders = ",".join("?" for _ in ids_list)
            rows = self.store._conn.execute(
                f"SELECT id, name FROM concepts WHERE id IN ({placeholders})",
                ids_list,
            ).fetchall()
            concept_name_map = {r["id"]: r["name"] for r in rows}

        for q in questions:
            qid = q["id"]
            points = float(q.get("points") or 0.0)
            total_points += points
            concept_id = q.get("concept_id")
            response = answers.get(qid)
            if response is None:
                # unanswered: incorrect (explicit for expired exams, implicit otherwise)
                verdict = "incorrect"
                awarded = 0.0
            else:
                verdict, awarded = await self._correct_question(q, response)
            earned += awarded
            if concept_id:
                concept_verdicts.setdefault(concept_id, []).append(verdict == "correct")
            per_question.append({
                "question_id": qid,
                "type": q["type"],
                "concept_id": concept_id,
                "verdict": verdict,
                "awarded": awarded,
                "points": points,
            })
            # T062: record error for incorrect/partial answers in error_history.
            if verdict in ("incorrect", "partial") and concept_id:
                q_payload = q.get("payload") or {}
                q_answer = q.get("answer") or {}
                concept_name = concept_name_map.get(concept_id, concept_id)
                question_text = q_payload.get("question", "")
                correct_answer_text = _answer_to_text(q["type"], q_payload, q_answer)
                given_answer_text = _response_to_text(q["type"], q_payload, response)
                self.store.record_error(
                    subject_id=quiz["subject_id"],
                    concept_name=concept_name,
                    question_text=question_text,
                    given_answer=given_answer_text,
                    correct_answer=correct_answer_text,
                    error_type=verdict,
                )
            self._insert_answer(QuizAnswer(
                question_id=qid, verdict=verdict,
                response={"value": response}, awarded=awarded,
            ))

        score = (earned / total_points * 100.0) if total_points > 0 else 0.0
        # Mastery updates (D7 weights) per concept.
        delta = _EXAM_DELTA if quiz["kind"] == "exam" else _QUIZ_DELTA
        for concept_id, results in concept_verdicts.items():
            correct = sum(1 for r in results if r)
            if correct == len(results):
                self.store.record_progress(concept_id, delta)
            elif correct == 0:
                self.store.record_progress(concept_id, -delta)
            # mixed ⇒ no change (avoid over-penalizing)

        strengths, weaknesses = self._strengths_weaknesses(concept_verdicts)
        report = QuizReport(
            score=round(score, 2),
            strengths=strengths,
            weaknesses=weaknesses,
            per_question=per_question,
        )
        self.store._conn.execute(
            "UPDATE quizzes SET status = 'completed', finished_at = ?, "
            "score = ?, report = ? WHERE id = ?",
            (_now_iso(), report.score, _json(report.to_dict()), quiz_id),
        )
        self.store._conn.commit()
        return report

    async def _correct_question(
        self, q: dict[str, Any], response: Any
    ) -> tuple[str, float]:
        """Return ``(verdict, awarded_points)`` for one question."""
        qtype = q["type"]
        points = float(q.get("points") or 0.0)
        answer = q.get("answer") or {}
        payload = q.get("payload") or {}
        correct = False
        if qtype == "mcq":
            correct = int(response) == int(answer.get("index", -1))
        elif qtype == "true_false":
            correct = bool(response) == bool(answer.get("value"))
        elif qtype == "matching":
            correct = list(response) == list(answer.get("order", []))
        else:  # open / code → LLM-judged
            model_answer = answer.get("text", "")
            try:
                data = await self._llm_json(
                    build_open_judge_prompt(
                        payload.get("question", ""), model_answer, str(response)
                    )
                )
                verdict = str(data.get("verdict", "incorrect")).lower()
            except Exception:
                verdict = "incorrect"
            if verdict == "correct":
                correct = True
                awarded = points
            elif verdict == "partial":
                correct = False
                awarded = points * 0.5
                return "partial", awarded
            else:
                correct = False
                awarded = 0.0
            return ("correct" if correct else "incorrect"), awarded
        return ("correct" if correct else "incorrect"), (points if correct else 0.0)

    def _strengths_weaknesses(
        self, concept_verdicts: dict[str, list[bool]]
    ) -> tuple[list[str], list[str]]:
        strengths: list[str] = []
        weaknesses: list[str] = []
        by_id: dict[str, str] = {}
        if concept_verdicts:
            ids = list(concept_verdicts.keys())
            placeholders = ",".join("?" for _ in ids)
            rows = self.store._conn.execute(
                f"SELECT id, name FROM concepts WHERE id IN ({placeholders})", ids
            ).fetchall()
            by_id = {r["id"]: r["name"] for r in rows}
        for concept_id, results in concept_verdicts.items():
            name = by_id.get(concept_id, concept_id)
            if all(results):
                strengths.append(name)
            elif not any(results):
                weaknesses.append(name)
        return strengths, weaknesses

    # ------------------------------------------------------------------
    # Storage helpers (direct SQL over LibraryStore)
    # ------------------------------------------------------------------

    def _insert_quiz(self, quiz: Quiz) -> None:
        self.store._conn.execute(
            "INSERT INTO quizzes "
            "(id, subject_id, kind, status, allow_help, time_limit_s, "
            "started_at, finished_at, score, report) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quiz.id, quiz.subject_id, quiz.kind, quiz.status,
                1 if quiz.allow_help else 0, quiz.time_limit_s,
                quiz.started_at, quiz.finished_at, quiz.score,
                _json(quiz.report) if quiz.report else None,
            ),
        )
        self.store._conn.commit()

    def _insert_question(self, q: QuizQuestion) -> None:
        self.store._conn.execute(
            "INSERT INTO quiz_questions "
            "(id, quiz_id, type, payload, answer, concept_id, points) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                q.id, q.quiz_id, q.type, _json(q.payload), _json(q.answer),
                q.concept_id, q.points,
            ),
        )
        self.store._conn.commit()

    def _insert_answer(self, a: QuizAnswer) -> None:
        self.store._conn.execute(
            "INSERT OR REPLACE INTO quiz_answers "
            "(question_id, response, verdict, awarded) "
            "VALUES (?, ?, ?, ?)",
            (a.question_id, _json(a.response), a.verdict, a.awarded),
        )
        self.store._conn.commit()

    def _get_quiz_row(self, quiz_id: str) -> dict[str, Any] | None:
        row = self.store._conn.execute(
            "SELECT * FROM quizzes WHERE id = ?", (quiz_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def _get_questions(self, quiz_id: str) -> list[dict[str, Any]]:
        rows = self.store._conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY id", (quiz_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _coerce_json(d.get("payload", {}), {})
            d["answer"] = _coerce_json(d.get("answer", {}), {})
            out.append(d)
        return out

    def get_quiz(self, quiz_id: str, include_answers: bool = False) -> dict[str, Any] | None:
        """Return a quiz with its questions; strips answers unless completed."""
        row = self._get_quiz_row(quiz_id)
        if row is None:
            return None
        completed = row.get("status") == "completed"
        questions = self._get_questions(quiz_id)
        q_out = []
        for q in questions:
            item = {
                "id": q["id"],
                "type": q["type"],
                "payload": q["payload"],
                "concept_id": q["concept_id"],
                "points": q["points"],
            }
            if include_answers or completed:
                item["answer"] = q["answer"]
            q_out.append(item)
        report = _coerce_json(row.get("report"), None)
        return {
            "id": row["id"],
            "subject_id": row["subject_id"],
            "kind": row["kind"],
            "status": row["status"],
            "allow_help": bool(row.get("allow_help")),
            "time_limit_s": row.get("time_limit_s"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "score": row.get("score"),
            "questions": q_out,
            "report": report if completed else None,
        }


class ExamHelpError(Exception):
    """Raised when help is requested on an exam (maps to HTTP 409)."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
