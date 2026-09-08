"""US1 P0-A — grade harness (visible/hidden split). TDD: written before impl."""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.sandbox import (
    GradeResult,
    TestOutcome,
    _build_program,
    _parse_harness,
    _strip_harness_output,
    grade_code,
)


def test_harness_template_uses_sentinel():
    program = _build_program("x = 1\n", ["assert x == 1"])
    assert "__TUTOR_HARNESS__" in program
    assert "x = 1" in program


def test_parse_harness_splits_pass_fail():
    tests = ["assert 1 == 1", "assert 1 == 2"]
    program = _build_program("", tests)
    # Simulate a run: extract marker payload manually via a real run below;
    # here craft stdout directly.
    import json

    payload = json.dumps(
        [{"i": 0, "passed": True, "error": None},
         {"i": 1, "passed": False, "error": "AssertionError: "}]
    )
    stdout = "student output\n\n__TUTOR_HARNESS__" + payload + "\n"
    outcomes = _parse_harness(stdout, tests)
    assert outcomes[0].passed is True
    assert outcomes[1].passed is False
    assert outcomes[0].expr == tests[0]


def test_parse_harness_missing_marker_marks_all_failed():
    outcomes = _parse_harness("no marker here\n", ["assert True"])
    assert len(outcomes) == 1
    assert outcomes[0].passed is False
    assert "did not reach" in (outcomes[0].error or "")


def test_strip_harness_output_removes_marker():
    cleaned = _strip_harness_output("hello\n\n__TUTOR_HARNESS__[{...}]\n")
    assert "__TUTOR_HARNESS__" not in cleaned
    assert "hello" in cleaned


def test_strip_harness_output_no_marker_passthrough():
    assert _strip_harness_output("plain\n") == "plain\n"


@pytest.mark.asyncio
async def test_grade_code_visible_hidden_split():
    result = await grade_code(
        "answer = 42\n",
        visible_tests=["assert answer == 42"],
        hidden_tests=["assert answer == 42", "assert answer != 0"],
    )
    assert isinstance(result, GradeResult)
    assert len(result.visible) == 1
    assert len(result.hidden) == 2
    assert result.all_passed is True
    assert all(isinstance(o, TestOutcome) for o in result.visible + result.hidden)
    # Student-facing stdout must not leak the harness marker.
    assert "__TUTOR_HARNESS__" not in result.run.stdout


@pytest.mark.asyncio
async def test_grade_code_failure_visible():
    result = await grade_code(
        "answer = 1\n",
        visible_tests=["assert answer == 42"],
        hidden_tests=[],
    )
    assert result.all_passed is False
    assert result.visible[0].passed is False


@pytest.mark.asyncio
async def test_grade_code_blocked_short_circuits():
    result = await grade_code(
        "import socket\n",
        visible_tests=["assert True"],
        hidden_tests=["assert True"],
    )
    assert result.run.blocked is True
    assert result.all_passed is False
    assert all(o.passed is False for o in result.visible + result.hidden)


@pytest.mark.asyncio
async def test_grade_code_include_hidden_false():
    result = await grade_code(
        "answer = 42\n",
        visible_tests=["assert answer == 42"],
        hidden_tests=["assert answer == 999"],
        include_hidden=False,
    )
    assert result.hidden == []
    assert result.all_passed is True


@pytest.mark.asyncio
async def test_grade_answer_accepts_execution_evidence():
    """FR-001/FR-020: grade_answer opt-in execution param, AttemptResult intact."""
    from src.ollama_tutor.tutor.assessment import AttemptResult, build_grade_prompt
    from src.ollama_tutor.tutor.models import Exercise
    from src.ollama_tutor.tutor.sandbox import RunResult

    ex = Exercise(
        id="e1",
        subject_id="s1",
        concept_id="c1",
        difficulty="easy",
        statement="Affiche 4.",
        solution="print(2+2)",
        hints=["h1", "h2", "h3"],
    )
    plain = build_grade_prompt(ex, "print(2+2)")
    exec_result = RunResult(
        stdout="4\n",
        stderr="",
        exit_code=0,
        duration_ms=12,
        timed_out=False,
        truncated=False,
    )
    with_exec = build_grade_prompt(ex, "print(2+2)", execution=exec_result)
    plain_text = " ".join(m.content for m in plain)
    exec_text = " ".join(m.content for m in with_exec)
    assert len(exec_text) > len(plain_text)
    assert "4" in exec_text  # real stdout cited
    assert "exit" in exec_text.lower() or "code" in exec_text.lower()
    # INVARIANT 3: AttemptResult never carries the solution.
    attempt = AttemptResult(
        verdict="correct", feedback="ok", hint_level=0, hint=None, solution=None
    )
    assert attempt.solution is None
