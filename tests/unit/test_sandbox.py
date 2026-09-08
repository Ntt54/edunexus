"""US1 P0-A — sandboxed runner (FR-003). TDD: written before impl. 100% offline."""

from __future__ import annotations

import pytest

from src.ollama_tutor.tutor.sandbox import (
    MAX_CODE_BYTES,
    RunnerError,
    run_python,
)


@pytest.mark.asyncio
async def test_run_print_expression():
    r = await run_python("print(2+2)")
    assert r.stdout.strip() == "4"
    assert r.exit_code == 0
    assert r.timed_out is False
    assert r.blocked is False


@pytest.mark.asyncio
async def test_blocked_import_socket_short_circuits():
    r = await run_python("import socket")
    assert r.blocked is True
    assert r.exit_code == -1
    details = " ".join(
        f"{e.get('type')}: {e.get('detail')}" for e in r.safety_events
    )
    assert "blocked_import" in details
    assert "socket" in details
    # Blocked code never executes: no stdout, stderr carries the safety note.
    assert r.stdout == ""
    assert "[safety]" in r.stderr


@pytest.mark.asyncio
async def test_blocked_call_os_system():
    r = await run_python("import os\nos.system('echo hi')")
    assert r.blocked is True


@pytest.mark.asyncio
async def test_syntax_error_returns_diagnostic_not_blocked():
    r = await run_python("def broken(:\n")
    assert r.blocked is False
    assert r.exit_code != 0
    # Syntax errors surface via safety_events (syntax_error) or stderr.
    payload = " ".join(str(e) for e in r.safety_events) + r.stderr
    assert "syntax" in payload.lower() or "invalid" in payload.lower() or r.stderr != ""


@pytest.mark.asyncio
async def test_infinite_loop_times_out():
    r = await run_python("while True:\n    pass\n", timeout=0.5)
    assert r.timed_out is True
    assert "killed after" in r.stderr


@pytest.mark.asyncio
async def test_large_output_truncated():
    r = await run_python("print('A' * 100000)")
    assert r.truncated is True
    assert "truncated at" in r.stdout


@pytest.mark.asyncio
async def test_stdin_passthrough():
    r = await run_python(
        "import sys\nprint(sys.stdin.read().strip().upper())", stdin="hello\n"
    )
    assert r.stdout.strip() == "HELLO"
    assert r.exit_code == 0


@pytest.mark.asyncio
async def test_nonzero_exit_encoded():
    r = await run_python("import sys\nsys.exit(3)")
    assert r.exit_code == 3
    assert r.timed_out is False
    assert r.blocked is False


@pytest.mark.asyncio
async def test_runner_error_on_non_string_code():
    with pytest.raises(RunnerError):
        await run_python(123)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_runner_error_on_oversized_code():
    with pytest.raises(RunnerError):
        await run_python("x = 1\n" * (MAX_CODE_BYTES // 6 + 100))


@pytest.mark.asyncio
async def test_skip_safety_runs_hidden_test_code():
    # Internal callers (hidden tests using exec) bypass the static scan.
    code = "exec('y = 41')\nprint(y + 1)"
    blocked = await run_python(code)
    assert blocked.blocked is True
    allowed = await run_python(code, skip_safety=True)
    assert allowed.blocked is False
    assert allowed.stdout.strip() == "42"


@pytest.mark.asyncio
async def test_env_isolation_no_caller_secrets(monkeypatch):
    monkeypatch.setenv("TUTOR_SECRET_PROBE", "should-not-leak")
    r = await run_python(
        "import os\nprint(os.getenv('TUTOR_SECRET_PROBE') or 'absent')"
    )
    assert r.stdout.strip() == "absent"
