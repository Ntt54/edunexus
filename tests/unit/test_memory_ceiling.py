"""Phase 6 RAM memory-ceiling gate (low-spec machines, ≤8 GB).

Regression contract: ``LlamaServerManager`` runs AT MOST ONE llama-server at
a time by default (sequential load/unload = RAM budget), and the EduNexus
web app stops every managed server on shutdown so GGUF RAM is freed on exit.

Fully offline: fake launcher/prober as in test_llama_server.py; the shutdown
test swaps ``web.server.get_default_manager`` for a recorder stub.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from ollama_tutor.tutor.providers.llama_server import (
    LlamaServerConfig,
    LlamaServerManager,
)


class FakeProcess:
    """Stands in for asyncio.subprocess.Process; records lifecycle calls."""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.calls: list[str] = []

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")

    async def wait(self) -> int:
        self.calls.append("wait")
        return 0


def make_launcher(spawned: list[FakeProcess]) -> Callable[[list[str]], FakeProcess]:
    def launcher(argv: list[str]) -> FakeProcess:
        process = FakeProcess(argv)
        spawned.append(process)
        return process

    return launcher


async def ok_prober(url: str) -> bool:
    return True


def make_config(name: str = "granite-embedding.gguf") -> LlamaServerConfig:
    return LlamaServerConfig(
        binary_path="/usr/local/bin/llama-server",
        model_path=f"/models/{name}",
        port=9215,
    )


# ---------------------------------------------------------------------------
# 1. Default exclusivity.
# ---------------------------------------------------------------------------


def test_default_manager_is_single_server() -> None:
    manager = LlamaServerManager()
    assert manager._max_servers == 1


# ---------------------------------------------------------------------------
# 2. Second start evicts the first (RAM budget).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_start_stops_first_process() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)

    first = await manager.start(make_config("a.gguf"))
    second = await manager.start(make_config("b.gguf"))

    # Exactly ONE server active after the second start — and it's the newest.
    active = manager.list_active()
    assert len(active) == 1
    assert active[0] is second
    assert first not in active

    # The FIRST process was terminated (SIGTERM path; wait() returns 0 so no
    # SIGKILL escalation is needed), the second is untouched.
    assert "terminate" in spawned[0].calls
    assert "terminate" not in spawned[1].calls
    assert "kill" not in spawned[1].calls
    assert first._stopped is True
    assert second._stopped is False


# ---------------------------------------------------------------------------
# 3. stop_all empties the registry and stops everything.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_all_stops_every_process_and_clears_registry() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)

    # Even with a higher budget, stop_all must sweep them all.
    generous = LlamaServerManager(
        max_servers=3, launcher=make_launcher(spawned), prober=ok_prober
    )
    servers = [
        await generous.start(make_config(f"{i}.gguf")) for i in range(3)
    ]
    assert len(generous.list_active()) == 3

    await generous.stop_all()

    assert generous.list_active() == []
    for server, process in zip(servers, spawned):
        assert server._stopped is True
        assert "terminate" in process.calls

    # The single-server manager is unaffected but also clean.
    assert manager.list_active() == []


# ---------------------------------------------------------------------------
# 4. App shutdown frees llama-server RAM (unconfigured path included).
# ---------------------------------------------------------------------------


class RecordingManager:
    """Stub replacing the module-level default manager in web.server."""

    def __init__(self) -> None:
        self.stop_all_calls = 0

    async def stop_all(self) -> None:
        self.stop_all_calls += 1


def test_app_shutdown_calls_stop_all_on_default_manager(
    tmp_path: Path, monkeypatch
) -> None:
    recorder = RecordingManager()
    monkeypatch.setattr(web_server, "get_default_manager", lambda: recorder)

    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app):
        assert recorder.stop_all_calls == 0  # nothing freed while running

    assert recorder.stop_all_calls == 1  # RAM freed exactly once on exit
