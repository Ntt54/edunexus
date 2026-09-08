"""Sandboxed Python runner + test harness (010 P0-A, FR-002/FR-003/FR-020).

Runner ported from ``autreprojet/python-tutor-main/backend/app/runner.py``;
harness (``TestOutcome``/``GradeResult``/sentinel ``__TUTOR_HARNESS__``)
ported from ``exercises.py:138-288``.

Guarantees (see ``specs/010-greffe-autreprojet/contracts/sandbox.md``):

* ``run_python`` never raises on student-side failures (syntax, timeout,
  non-zero exit, large output, blocked code) — all encoded in
  :class:`RunResult`. It raises :class:`RunnerError` only for caller
  mistakes (non-``str`` code, code over ``MAX_CODE_BYTES``).
* ``skip_safety=True`` is reserved for internal callers that already
  validated the code (hidden tests) — never expose it over the network.
* Empty env, temp cwd ``0o700``, truncated output, bounded wall timeout
  (default 5 s, clamped to [0.5, 30.0]), POSIX rlimits when ``resource``
  is importable (Windows-safe via ImportError guard).

Stdlib only (+ nothing beyond httpx/numpy already present — this module
uses stdlib alone). UI-framework-free by contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .safety import SafetyEvent, SafetyReport, analyze


# Hard ceilings. Configurable via env for local tweaking, but the upper
# bound is fixed so a misconfigured deploy can't grant generous limits.
def _bounded_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _bounded_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(lo, min(hi, value))


DEFAULT_TIMEOUT_SEC = _bounded_float("TUTOR_RUN_TIMEOUT", 5.0, 0.5, 30.0)
MAX_CODE_BYTES = _bounded_int("TUTOR_RUN_MAX_CODE_BYTES", 50_000, 1_000, 200_000)
MAX_OUTPUT_BYTES = _bounded_int("TUTOR_RUN_MAX_OUTPUT_BYTES", 32_000, 1_000, 200_000)

# Resource limits, only applied on POSIX where ``resource`` is available.
RUN_CPU_SECONDS = _bounded_int("TUTOR_RUN_CPU_SECONDS", 5, 1, 60)
RUN_MEM_MB = _bounded_int("TUTOR_RUN_MEM_MB", 256, 32, 4096)
RUN_FSIZE_MB = _bounded_int("TUTOR_RUN_FSIZE_MB", 16, 1, 256)
RUN_NPROC = _bounded_int("TUTOR_RUN_NPROC", 64, 8, 1024)

# When set to "1", the safety scanner also blocks WARN_MODULES (os,
# pathlib, shutil, tempfile, glob, importlib).
STRICT_IMPORTS = os.getenv("TUTOR_STRICT_IMPORTS", "0") == "1"


@dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    truncated: bool
    blocked: bool = False
    safety_events: list[dict] = field(default_factory=list)


class RunnerError(ValueError):
    """Raised for caller-visible runner problems (e.g. code too large)."""


def _truncate(data: bytes, limit: int) -> tuple[str, bool]:
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    return (
        data[:limit].decode("utf-8", errors="replace")
        + f"\n... [truncated at {limit} bytes]",
        True,
    )


def _safe_env() -> dict[str, str]:
    """Minimal, deliberate environment for the student subprocess.

    Nothing from the caller's environment is inherited. We do NOT pass
    PATH — the runner invokes the interpreter by absolute path. Without
    PATH, code that tries ``os.system('curl ...')`` cannot find the
    binary even if it slipped past the static scanner.
    """
    return {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LC_ALL": "C.UTF-8",
        # Empty HOME prevents user-site lookups; -I already disables
        # user site, but a redundant guard is cheap.
        "HOME": "/nonexistent",
    }


def _preexec_limits():  # pragma: no cover - exercised only on POSIX
    """Return a preexec function that applies POSIX resource limits.

    Importing :mod:`resource` is deferred so this module still imports
    cleanly on platforms (notably Windows) that don't ship it. Returns
    ``None`` when ``resource`` is unavailable.
    """
    try:
        import resource  # type: ignore[import-not-found]
    except ImportError:
        return None

    def apply() -> None:
        # CPU seconds (RLIMIT_CPU): caps total CPU time the child can use.
        resource.setrlimit(
            resource.RLIMIT_CPU, (RUN_CPU_SECONDS, RUN_CPU_SECONDS)
        )
        # Address space (RLIMIT_AS): caps total virtual memory.
        mem = RUN_MEM_MB * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except (ValueError, OSError):
            # Some platforms (e.g. macOS) do not honour RLIMIT_AS for
            # Python. We accept that loss silently — wall-clock timeout
            # is still in force.
            pass
        # File size (RLIMIT_FSIZE): prevents filling the tempdir.
        fsize = RUN_FSIZE_MB * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        except (ValueError, OSError):
            pass
        # Core files: disabled.
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass
        # Process / thread count.
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (RUN_NPROC, RUN_NPROC))
        except (ValueError, AttributeError, OSError):
            pass

    return apply


def _events_to_payload(events: list[SafetyEvent]) -> list[dict]:
    return [asdict(e) for e in events]


def _blocked_result(report: SafetyReport) -> RunResult:
    detail = report.summary or "code blocked by safety policy"
    stderr = f"[safety] execution blocked: {detail}\n"
    return RunResult(
        stdout="",
        stderr=stderr,
        exit_code=-1,
        duration_ms=0,
        timed_out=False,
        truncated=False,
        blocked=True,
        safety_events=_events_to_payload(report.events),
    )


async def run_python(
    code: str,
    *,
    stdin: str = "",
    timeout: Optional[float] = None,
    python_executable: Optional[str] = None,
    skip_safety: bool = False,
) -> RunResult:
    """Execute *code* in an isolated Python subprocess and return its result.

    The function never raises on student-side failures (syntax errors,
    timeouts, non-zero exits, large output, blocked imports): all of
    those are returned in the :class:`RunResult`. It only raises
    :class:`RunnerError` for caller mistakes such as oversized code
    submissions.

    ``skip_safety=True`` bypasses the static AST scan. It exists for
    internal callers that have already validated the code (the exercise
    runner does its own checks on hidden test code); never expose it
    over the network.
    """
    if not isinstance(code, str):  # defensive — schema layer should catch this
        raise RunnerError("code must be a string")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise RunnerError(
            f"code exceeds {MAX_CODE_BYTES} bytes; refusing to run"
        )

    safety_events: list[SafetyEvent] = []
    if not skip_safety:
        report = analyze(code)
        if report.blocked:
            return _blocked_result(report)
        safety_events = report.events

    effective_timeout = (
        DEFAULT_TIMEOUT_SEC
        if timeout is None
        else max(0.5, min(30.0, float(timeout)))
    )
    py = python_executable or sys.executable

    workdir = Path(tempfile.mkdtemp(prefix="tutor-run-"))
    try:
        try:
            os.chmod(workdir, 0o700)
        except OSError:
            pass
        script = workdir / "main.py"
        script.write_text(code, encoding="utf-8")

        loop = asyncio.get_event_loop()
        start = loop.time()

        kwargs: dict = dict(
            cwd=str(workdir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_safe_env(),
        )
        preexec = _preexec_limits()
        if preexec is not None and os.name == "posix":
            kwargs["preexec_fn"] = preexec
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(
            py,
            "-I",  # isolated: ignore PYTHON* env vars and user site
            "-B",  # don't write .pyc files
            str(script),
            **kwargs,
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin.encode("utf-8") if stdin else b""),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            # Kill the whole process group when we started one, so any
            # children the student spawned die with the parent.
            try:
                if kwargs.get("start_new_session"):
                    os.killpg(proc.pid, 9)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
            stdout_bytes, stderr_bytes = await proc.communicate()

        duration_ms = int((loop.time() - start) * 1000)
        stdout, t1 = _truncate(stdout_bytes or b"", MAX_OUTPUT_BYTES)
        stderr, t2 = _truncate(stderr_bytes or b"", MAX_OUTPUT_BYTES)

        if timed_out and not stderr.endswith("\n"):
            stderr = (stderr + "\n" if stderr else "") + (
                f"[runner] killed after {effective_timeout:.1f}s timeout"
            )

        exit_code = proc.returncode if proc.returncode is not None else -1
        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=t1 or t2,
            blocked=False,
            safety_events=_events_to_payload(safety_events),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test harness (ported from exercises.py:138-288)
# ---------------------------------------------------------------------------


@dataclass
class TestOutcome:
    __test__ = False  # silence pytest collection (name looks like a test class)

    expr: str
    passed: bool
    error: str | None = None


@dataclass
class GradeResult:
    __test__ = False  # silence pytest collection

    run: RunResult
    visible: list[TestOutcome] = field(default_factory=list)
    hidden: list[TestOutcome] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(o.passed for o in self.visible) and all(
            o.passed for o in self.hidden
        )


# A short harness appended to the student's code. Each test runs in
# isolation: failures are caught and surfaced via stdout as a JSON
# blob the grader parses. Using a sentinel and JSON keeps the protocol
# robust to whatever the student printed earlier.
_HARNESS_TEMPLATE = """
# --- begin tutor test harness ---
import json as _tutor_json
_tutor_results = []
_tutor_tests = {tests!r}
for _i, _expr in enumerate(_tutor_tests):
    try:
        exec(_expr, globals())
        _tutor_results.append({{"i": _i, "passed": True, "error": None}})
    except AssertionError as _e:
        _tutor_results.append({{"i": _i, "passed": False, "error": "AssertionError: " + str(_e)}})
    except BaseException as _e:
        _tutor_results.append({{"i": _i, "passed": False, "error": type(_e).__name__ + ": " + str(_e)}})
print()
print("__TUTOR_HARNESS__" + _tutor_json.dumps(_tutor_results))
# --- end tutor test harness ---
"""


def _build_program(student_code: str, tests: Iterable[str]) -> str:
    test_list = list(tests)
    return student_code.rstrip() + "\n" + _HARNESS_TEMPLATE.format(tests=test_list)


def _parse_harness(stdout: str, tests: list[str]) -> list[TestOutcome]:
    marker = "__TUTOR_HARNESS__"
    idx = stdout.rfind(marker)
    if idx == -1:
        # The harness never ran (e.g. import error / syntax error).
        return [
            TestOutcome(expr=t, passed=False, error="program did not reach tests")
            for t in tests
        ]
    payload = stdout[idx + len(marker):].strip().splitlines()[0]
    try:
        records = json.loads(payload)
    except json.JSONDecodeError:
        return [
            TestOutcome(expr=t, passed=False, error="harness output unreadable")
            for t in tests
        ]
    out: list[TestOutcome] = []
    by_index = {r.get("i"): r for r in records if isinstance(r, dict)}
    for i, expr in enumerate(tests):
        rec = by_index.get(i)
        if rec is None:
            out.append(TestOutcome(expr=expr, passed=False, error="missing result"))
            continue
        out.append(
            TestOutcome(
                expr=expr,
                passed=bool(rec.get("passed")),
                error=rec.get("error"),
            )
        )
    return out


def _strip_harness_output(stdout: str) -> str:
    """Remove the harness marker line so the student sees only their own output."""
    idx = stdout.rfind("__TUTOR_HARNESS__")
    if idx == -1:
        return stdout
    # Trim trailing blank line we added before the marker.
    cleaned = stdout[:idx].rstrip("\n")
    return cleaned + ("\n" if cleaned else "")


async def grade_code(
    student_code: str,
    visible_tests: Iterable[str] = (),
    hidden_tests: Iterable[str] = (),
    *,
    include_hidden: bool = True,
    timeout: float | None = None,
) -> GradeResult:
    """Run *student_code* against visible+hidden tests and return outcomes.

    Generic adaptation of ``exercises.grade()`` decoupled from that
    module's ``Exercise`` type (EduNexus has its own ``Exercise`` model):
    tests are passed as plain ``assert``-expression lists. The student's
    portion is scanned by :func:`.safety.analyze`; the combined program
    runs with ``skip_safety=True`` since the harness itself uses
    ``exec()`` and would otherwise trip the scanner. Visible/hidden
    outcomes are split so callers can withhold hidden details.
    """
    visible_tests = list(visible_tests)
    hidden_tests = list(hidden_tests) if include_hidden else []
    all_tests = visible_tests + hidden_tests
    # Scan the student's portion only; the harness uses exec() to run
    # each test in isolation and would otherwise trip the scanner.
    student_safety = analyze(student_code)
    if student_safety.blocked:
        # Build a short-circuit RunResult mirroring the runner's
        # blocked-result shape so the API contract stays stable.
        blocked_run = RunResult(
            stdout="",
            stderr=f"[safety] execution blocked: {student_safety.summary}\n",
            exit_code=-1,
            duration_ms=0,
            timed_out=False,
            truncated=False,
            blocked=True,
            safety_events=[
                {"type": e.type, "detail": e.detail, "lineno": e.lineno}
                for e in student_safety.events
            ],
        )
        outcomes = [
            TestOutcome(expr=t, passed=False, error="blocked by safety policy")
            for t in all_tests
        ]
        return GradeResult(
            run=blocked_run,
            visible=outcomes[: len(visible_tests)],
            hidden=outcomes[len(visible_tests):],
        )

    program = _build_program(student_code, all_tests)
    result = await run_python(program, timeout=timeout, skip_safety=True)

    outcomes = _parse_harness(result.stdout, all_tests)

    # Replace the harness chatter with a clean stdout the student sees.
    cleaned = RunResult(
        stdout=_strip_harness_output(result.stdout),
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        truncated=result.truncated,
        blocked=result.blocked,
        safety_events=list(result.safety_events),
    )
    visible_outcomes = outcomes[: len(visible_tests)]
    hidden_outcomes = outcomes[len(visible_tests):]
    return GradeResult(run=cleaned, visible=visible_outcomes, hidden=hidden_outcomes)


# Backwards-compatible alias mirroring the source module's ``grade`` name.
grade = grade_code
