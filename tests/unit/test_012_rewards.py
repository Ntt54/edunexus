"""T026 Unit tests: XP saine anti-grinding + serie hebdo + joker (012 US4).

FR-012 / SC-007 : ``XP = base(difficulte) x decay(repetition <7j)``,
cap journalier, re-do meme item meme jour <= 25 %, serie hebdo + 1
joker freeze, re-submit idempotent -> 0 XP (suivi G3).

100 % offline : store tmp, LLM scripte, ``@pytest.mark.asyncio``
explicite, aucun appel reseau.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ollama_tutor.tutor import rewards as rewards_mod
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture(autouse=True)
def _clean_ledger():
    rewards_mod.reset_daily_ledger()
    yield
    rewards_mod.reset_daily_ledger()


# ---------------------------------------------------------------------------
# Helpers offline
# ---------------------------------------------------------------------------


def _uid(prefix: str, n: int) -> str:
    return f"{prefix}{n:04d}"


def _make_exercise(store: LibraryStore, difficulty: str, tag: str) -> str:
    subject = store.create_subject(f"Sujet {tag}")
    concept = store.create_concept(subject.id, f"Notion {tag}")
    ex_id = f"ex-{tag}"
    store._conn.execute(
        "INSERT INTO exercises (id, subject_id, concept_id, difficulty,"
        " statement, solution, hints, status, created_at)"
        " VALUES (?, ?, ?, ?, 'stmt', 'sol', '[]', 'open', ?)",
        (ex_id, subject.id, concept.id, difficulty,
         datetime.now(timezone.utc).isoformat()),
    )
    store._conn.commit()
    return ex_id


def _insert_attempt(
    store: LibraryStore,
    exercise_id: str,
    n: int,
    verdict: str = "correct",
    when: datetime | None = None,
) -> str:
    from src.ollama_tutor.tutor.models import ExerciseAttempt

    aid = _uid("att", n)
    if isinstance(when, datetime):
        created = when.isoformat()
    elif isinstance(when, str) and when:
        created = when
    else:
        created = datetime.now(timezone.utc).isoformat()
    store.add_attempt(
        ExerciseAttempt(
            id=aid,
            exercise_id=exercise_id,
            verdict=verdict,
            answer="reponse",
            feedback="",
            created_at=created,
        )
    )
    return aid


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


class _VerdictLLM:
    """Faux LLM : verdict de correction scripte (grade_answer offline)."""

    def __init__(self, verdict: str = "correct"):
        self._verdict = verdict

    async def chat_stream(self, messages, model, options=None):
        from src.ollama_tutor.client import StreamEvent

        yield StreamEvent(
            kind="content",
            text=json.dumps(
                {"verdict": self._verdict, "feedback": "Bien."},
                ensure_ascii=False,
            ),
        )
        yield StreamEvent(kind="done", stats=None)


def _make_service(store: LibraryStore, tmp_path: Path, verdict: str = "correct"):
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService

    return TutorService(store, _VerdictLLM(verdict), Config(config_dir=tmp_path))


# ---------------------------------------------------------------------------
# T026a — ponderation par la difficulte reelle
# ---------------------------------------------------------------------------


def test_base_xp_weights_difficulty() -> None:
    assert rewards_mod.base_xp("easy") == 10
    assert rewards_mod.base_xp("medium") == 15
    assert rewards_mod.base_xp("hard") == 25
    assert rewards_mod.base_xp("easy") < rewards_mod.base_xp("medium")
    assert rewards_mod.base_xp("medium") < rewards_mod.base_xp("hard")


def test_base_xp_unknown_difficulty_falls_back_to_medium() -> None:
    assert rewards_mod.base_xp("nimportequoi") == rewards_mod.base_xp("medium")
    assert rewards_mod.base_xp("") == rewards_mod.base_xp("medium")
    assert rewards_mod.base_xp(None) == rewards_mod.base_xp("medium")


# ---------------------------------------------------------------------------
# T026b — decroissance sur repetition <7j
# ---------------------------------------------------------------------------


def test_decay_factor_halves_per_repetition() -> None:
    assert rewards_mod.decay_factor(0) == 1.0
    assert rewards_mod.decay_factor(1) == 0.5
    assert rewards_mod.decay_factor(2) == 0.25
    assert rewards_mod.decay_factor(3) == 0.125


def test_compute_exercise_xp_first_success_full_base() -> None:
    assert rewards_mod.compute_exercise_xp("easy") == 10
    assert rewards_mod.compute_exercise_xp("medium") == 15
    assert rewards_mod.compute_exercise_xp("hard") == 25


def test_compute_exercise_xp_decayed_on_repeat_within_7d() -> None:
    assert rewards_mod.compute_exercise_xp("medium", repetitions_7d=1) == 8
    assert rewards_mod.compute_exercise_xp("medium", repetitions_7d=2) == 4
    assert rewards_mod.compute_exercise_xp("hard", repetitions_7d=1) in (12, 13)


def test_count_recent_correct_attempts_respects_7d_window(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        ex = _make_exercise(store, "easy", "fenetre")
        old = datetime.now(timezone.utc) - timedelta(days=8)
        _insert_attempt(store, ex, 1, when=old)
        assert rewards_mod.count_recent_correct_attempts(store, ex) == 0
        recent = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        _insert_attempt(store, ex, 2, when=recent)
        assert rewards_mod.count_recent_correct_attempts(store, ex) == 1
        # Les verdicts non-correct ne comptent pas comme repetition reussie.
        _insert_attempt(store, ex, 3, verdict="incorrect")
        assert rewards_mod.count_recent_correct_attempts(store, ex) == 1
    finally:
        store.close()


def test_count_recent_excludes_current_attempt(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        ex = _make_exercise(store, "medium", "excl")
        aid = _insert_attempt(store, ex, 1)
        assert rewards_mod.count_recent_correct_attempts(store, ex) == 1
        assert (
            rewards_mod.count_recent_correct_attempts(store, ex, exclude_id=aid)
            == 0
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T026c — re-do meme item meme jour <= 25 %
# ---------------------------------------------------------------------------


def test_same_day_redo_multiplier_capped_at_25pct() -> None:
    for difficulty in ("easy", "medium", "hard"):
        for redos in (1, 2, 3, 5):
            mult = rewards_mod.exercise_multiplier(
                repetitions_7d=redos, same_day_redos=redos
            )
            assert mult <= rewards_mod.SAME_DAY_REDO_MAX


def test_same_day_redo_xp_bounded_per_difficulty() -> None:
    assert rewards_mod.compute_exercise_xp("easy", 1, 1) <= 3
    assert rewards_mod.compute_exercise_xp("medium", 1, 1) <= 4
    assert rewards_mod.compute_exercise_xp("hard", 1, 1) <= 7


def test_award_second_same_day_success_bounded(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        ex = _make_exercise(store, "easy", "redo")
        first = rewards_mod.award_exercise_xp(store, exercise_id=ex, difficulty="easy")
        assert first == 10
        # 1 reussite anterieure le meme jour + tentative courante exclue.
        _insert_attempt(store, ex, 1)
        current = _insert_attempt(store, ex, 2)
        second = rewards_mod.award_exercise_xp(
            store, exercise_id=ex, difficulty="easy", attempt_id=current
        )
        # Repetition <7j (x0.5) + re-do meme jour plafonne a 25 %.
        assert second <= 3
    finally:
        store.close()


def test_sc007_twenty_easy_redos_under_10pct_of_normal_session(
    tmp_path: Path,
) -> None:
    """SC-007 : 20x le meme exercice facile <= 10 % d'une seance normale."""
    store = LibraryStore(tmp_path)
    try:
        ex = _make_exercise(store, "easy", "grind")
        total = 0
        per_award: list[int] = []
        for n in range(20):
            aid = _insert_attempt(store, ex, 100 + n)
            granted = rewards_mod.award_exercise_xp(
                store, exercise_id=ex, difficulty="easy", attempt_id=aid
            )
            per_award.append(granted)
            total += granted
        assert per_award[0] == 10
        assert all(g <= 3 for g in per_award[1:])
        normal_session = 20 * rewards_mod.base_xp("medium")
        assert total <= 0.10 * normal_session
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T026d — cap journalier
# ---------------------------------------------------------------------------


def test_daily_grant_clamps_to_cap() -> None:
    assert rewards_mod.daily_grant(0, 20) == 20
    assert rewards_mod.daily_grant(190, 20) == 10
    assert rewards_mod.daily_grant(200, 20) == 0
    assert rewards_mod.daily_grant(250, 20) == 0


def test_daily_cap_enforced_across_distinct_items(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        granted_total = 0
        for n in range(12):
            ex = _make_exercise(store, "hard", f"cap{n}")
            granted_total += rewards_mod.award_exercise_xp(
                store, exercise_id=ex, difficulty="hard"
            )
        assert granted_total == rewards_mod.DAILY_XP_CAP
        extra = _make_exercise(store, "hard", "cap-extra")
        assert (
            rewards_mod.award_exercise_xp(
                store, exercise_id=extra, difficulty="hard"
            )
            == 0
        )
        profile = store.get_learner_profile()
        assert profile["total_xp"] == rewards_mod.DAILY_XP_CAP
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T026e — serie hebdo + 1 joker freeze
# ---------------------------------------------------------------------------


def test_weekly_streak_grows_on_consecutive_weeks() -> None:
    state = {"weeks": [], "frozen_week": None}
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W36")
    assert (streak, consumed) == (1, False)
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W37")
    assert (streak, consumed) == (2, False)
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W38")
    assert (streak, consumed) == (3, False)
    assert state["frozen_week"] is None


def test_same_week_activity_keeps_streak_without_freeze() -> None:
    state = {"weeks": ["2026-W36"], "frozen_week": None}
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W36")
    assert (streak, consumed) == (1, False)
    assert state["frozen_week"] is None


def test_single_missed_week_bridged_by_joker() -> None:
    state = {"weeks": ["2026-W36"], "frozen_week": None}
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W38")
    assert consumed is True
    assert streak == 2
    assert state["frozen_week"] == "2026-W37"
    assert "2026-W38" in state["weeks"]
    # La semaine suivante prolonge la serie (3 semaines actives
    # creditees, la semaine joker preservant la continuite).
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W39")
    assert (streak, consumed) == (3, False)


def test_joker_single_use_second_gap_resets() -> None:
    state = {"weeks": ["2026-W36", "2026-W38"], "frozen_week": "2026-W37"}
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W40")
    assert consumed is False
    assert streak == 1
    assert state["weeks"] == ["2026-W40"]


def test_two_week_gap_resets_even_with_intact_joker() -> None:
    state = {"weeks": ["2026-W36"], "frozen_week": None}
    state, streak, consumed = rewards_mod.record_weekly_activity(state, "2026-W39")
    assert (streak, consumed) == (1, False)
    assert state["weeks"] == ["2026-W39"]
    assert state["frozen_week"] is None


def test_freeze_state_roundtrip_and_tolerant_parse() -> None:
    state = {"weeks": ["2026-W36"], "frozen_week": "2026-W37"}
    assert rewards_mod.parse_freeze_state(rewards_mod.dump_freeze_state(state)) == {
        "weeks": ["2026-W36"],
        "frozen_week": "2026-W37",
    }
    assert rewards_mod.parse_freeze_state(None) == {
        "weeks": [],
        "frozen_week": None,
    }
    assert rewards_mod.parse_freeze_state("pas-du-json") == {
        "weeks": [],
        "frozen_week": None,
    }


def test_weekly_activity_persisted_in_streak_freeze_json(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        monday = date(2026, 9, 14)  # lundi -> 2026-W38
        streak, consumed = rewards_mod.record_weekly_activity_for_store(
            store, monday
        )
        assert streak == 1 and consumed is False
        profile = store.get_learner_profile()
        raw = profile.get("streak_freeze_json")
        assert raw is not None
        assert "2026-W38" in rewards_mod.parse_freeze_state(raw)["weeks"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Quiz : base + bonus de serie, sous cap
# ---------------------------------------------------------------------------


def test_award_quiz_xp_base_then_streak_bonus(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        day1 = date(2026, 6, 10)
        with patch("src.ollama_tutor.tutor.store.date") as mock_date:
            mock_date.today.return_value = day1
            mock_date.fromisoformat = date.fromisoformat
            first = rewards_mod.award_quiz_xp(store, today=day1)
        assert first["xp"] == 20
        assert first["streak"] == 1
        assert first["bonus"] == 0

        day2 = date(2026, 6, 11)
        with patch("src.ollama_tutor.tutor.store.date") as mock_date:
            mock_date.today.return_value = day2
            mock_date.fromisoformat = date.fromisoformat
            second = rewards_mod.award_quiz_xp(store, today=day2)
        assert second["streak"] == 2
        assert second["bonus"] == 10
        assert second["xp"] == 30
        profile = store.get_learner_profile()
        assert profile["total_xp"] == 50
    finally:
        store.close()


def test_award_quiz_xp_respects_daily_cap(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        rewards_mod._DAILY_AWARDED[rewards_mod._day_key(None)] = (
            rewards_mod.DAILY_XP_CAP - 5
        )
        result = rewards_mod.award_quiz_xp(store)
        assert result["xp"] == 5
        assert store.get_learner_profile()["total_xp"] == 5
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Re-cablage service : grade_answer + submit_answers (dont suivi G3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grade_answer_correct_awards_weighted_xp(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        ex = _make_exercise(store, "easy", "grade")
        svc = _make_service(store, tmp_path, verdict="correct")
        result = await svc.grade_answer(ex, "bonne reponse")
        assert result.verdict == "correct"
        assert store.get_learner_profile()["total_xp"] == 10
        # Meme item, meme jour : re-do plafonne (<= 25 % de la base).
        result2 = await svc.grade_answer(ex, "bonne reponse")
        assert result2.verdict == "correct"
        total = store.get_learner_profile()["total_xp"]
        assert total <= 10 + 3
    finally:
        store.close()


@pytest.mark.asyncio
async def test_grade_answer_hard_worth_more_than_easy(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    try:
        ex_easy = _make_exercise(store, "easy", "d1")
        ex_hard = _make_exercise(store, "hard", "d2")
        svc = _make_service(store, tmp_path, verdict="correct")
        await svc.grade_answer(ex_easy, "ok")
        xp_easy = store.get_learner_profile()["total_xp"]
        store2 = LibraryStore(tmp_path / "autre")
        try:
            ex_h = _make_exercise(store2, "hard", "d3")
            svc2 = _make_service(store2, tmp_path / "autre", verdict="correct")
            await svc2.grade_answer(ex_h, "ok")
            xp_hard = store2.get_learner_profile()["total_xp"]
        finally:
            store2.close()
        assert xp_easy == 10
        assert xp_hard == 25
        assert xp_hard > xp_easy
    finally:
        store.close()


def _quiz_payload() -> dict:
    return {
        "question": "Question ?",
        "choices": ["A", "B"],
        "answer_index": 0,
        "answer": True,
        "model_answer": "A",
        "pairs": [{"left": "L1", "right": "R1"}],
        "answer_order": [0],
    }


class _ScriptedClient:
    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)

    async def embed(self, model, inputs):
        return [[1.0, 0.0]]

    async def chat_stream(self, messages, model, **kwargs):
        from src.ollama_tutor.client import StreamEvent

        payload = self._payloads.pop(0) if self._payloads else {}
        yield StreamEvent(kind="content", text=json.dumps(payload, ensure_ascii=False))
        yield StreamEvent(kind="done", stats=None)


def _correct_answer(question: dict):
    qtype = question["type"]
    answer = question["answer"]
    if qtype == "mcq":
        return int(answer["index"])
    if qtype == "true_false":
        return bool(answer["value"])
    if qtype == "matching":
        return list(answer["order"])
    return str(answer.get("text", ""))


def _answer_all_correct(
    questions: list[dict], scripted: _ScriptedClient
) -> dict[str, object]:
    answers: dict[str, object] = {}
    for q in questions:
        answers[q["id"]] = _correct_answer(q)
        if q["type"] in ("open", "code"):
            scripted._payloads.append({"verdict": "correct", "feedback": "ok"})
    return answers


@pytest.mark.asyncio
async def test_g3_resubmit_completed_quiz_awards_zero_xp(tmp_path: Path) -> None:
    """Suivi G3 : re-submit idempotent -> meme rapport, 0 XP supplementaire."""
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.assessment import QuizEngine
    from src.ollama_tutor.tutor.service import TutorService

    store = LibraryStore(tmp_path)
    try:
        scripted = _ScriptedClient([_quiz_payload() for _ in range(2)])
        svc = TutorService(store, scripted, Config(config_dir=tmp_path))
        svc.quiz_engine = QuizEngine(store, scripted, Config(config_dir=tmp_path))
        exam = await svc.create_blueprint_exam("cm/bepc", 60, size=2)
        full = svc.quiz_engine.get_quiz(exam["id"], include_answers=True)
        assert full is not None
        questions = full["questions"]
        answers = _answer_all_correct(questions, scripted)

        report1 = await svc.submit_answers(exam["id"], answers)
        xp_after_first = store.get_learner_profile()["total_xp"]
        assert xp_after_first == 20

        other = {q["id"]: "reponse fausse" for q in questions}
        report2 = await svc.submit_answers(exam["id"], other)
        assert report2.to_dict() == report1.to_dict()
        assert store.get_learner_profile()["total_xp"] == xp_after_first
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Garde offline / sans LLM
# ---------------------------------------------------------------------------


def test_rewards_module_is_stdlib_only_offline() -> None:
    source = Path(rewards_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import fastapi",
        "from fastapi",
        "import textual",
        "from textual",
        "import httpx",
        "from httpx",
        "llm_client",
        "chat_stream",
    ):
        assert forbidden not in source
