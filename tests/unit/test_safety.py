"""US1 P0-A — static safety analysis (FR-002). TDD: written before impl."""

from __future__ import annotations

import os

from src.ollama_tutor.tutor.safety import (
    BLOCKED_MODULES,
    WARN_MODULES,
    analyze,
)


def test_benign_code_not_blocked():
    report = analyze("x = 1 + 2\nprint(x)\n")
    assert report.blocked is False
    assert report.events == []


def test_blocked_import_socket():
    report = analyze("import socket\n")
    assert report.blocked is True
    assert any(
        e.type == "blocked_import" and e.detail == "socket" for e in report.events
    )


def test_blocked_from_import_subprocess():
    report = analyze("from subprocess import run\n")
    assert report.blocked is True
    assert any(e.type == "blocked_import" for e in report.events)


def test_blocked_nested_import_urllib_request():
    report = analyze("import urllib.request\n")
    assert report.blocked is True


def test_blocked_call_os_system():
    report = analyze("import os\nos.system('echo hi')\n")
    assert report.blocked is True
    assert any(
        e.type == "blocked_call" and e.detail == "os.system" for e in report.events
    )


def test_blocked_call_eval_exec():
    for snippet in ("eval('1+1')\n", "exec('x = 1')\n"):
        report = analyze(snippet)
        assert report.blocked is True, snippet
        assert any(e.type == "blocked_call" for e in report.events)


def test_blocked_pickle_import():
    report = analyze("import pickle\n")
    assert report.blocked is True


def test_warn_modules_allowed_by_default():
    assert "os" in WARN_MODULES
    report = analyze("import os\nprint(os.getcwd())\n")
    assert report.blocked is False


def test_strict_mode_blocks_warn_modules_and_open(monkeypatch):
    monkeypatch.setenv("TUTOR_STRICT_IMPORTS", "1")
    report = analyze("import os\n")
    assert report.blocked is True
    assert any(e.type == "blocked_import" for e in report.events)
    report2 = analyze("open('f.txt').read()\n")
    assert report2.blocked is True
    assert any(
        e.type == "blocked_call" and e.detail == "open" for e in report2.events
    )


def test_open_allowed_by_default():
    report = analyze("open('f.txt').read()\n")
    assert report.blocked is False


def test_syntax_error_not_blocked_but_reported():
    report = analyze("def broken(:\n")
    assert report.blocked is False
    assert any(e.type == "syntax_error" for e in report.events)


def test_analyze_never_raises():
    # Non-str input or empty code must not raise (empty → clean report).
    report = analyze("")
    assert report.blocked is False
    assert report.events == []


def test_summary_joins_events():
    report = analyze("import socket\n")
    assert "blocked_import" in report.summary
    assert "socket" in report.summary


def test_blocked_modules_cover_network_and_native():
    for mod in ("subprocess", "socket", "ctypes", "pickle", "requests", "httpx"):
        assert mod in BLOCKED_MODULES
