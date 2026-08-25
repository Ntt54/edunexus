"""Offline unit tests for the llama.cpp ``llama-server`` manager (Phase 1).

Fully offline: a fake launcher spawns FakeProcess handles and a fake prober
replaces the HTTP health check — no real processes, no network.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ollama_tutor.tutor.providers.llama_server import (
    LlamaServerConfig,
    LlamaServerError,
    LlamaServerManager,
    ManagedLlamaServer,
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
    """Launcher returning a fresh FakeProcess per call, appended to ``spawned``."""

    def launcher(argv: list[str]) -> FakeProcess:
        process = FakeProcess(argv)
        spawned.append(process)
        return process

    return launcher


async def ok_prober(url: str) -> bool:
    return True


async def never_prober(url: str) -> bool:
    return False


def counting_prober(succeed_after: int):
    """Prober returning False for the first ``succeed_after - 1`` calls."""
    state = {"calls": 0}

    async def prober(url: str) -> bool:
        state["calls"] += 1
        return state["calls"] >= succeed_after

    return prober, state


def make_config(**overrides: object) -> LlamaServerConfig:
    defaults: dict = {
        "binary_path": "/usr/local/bin/llama-server",
        "model_path": "/models/granite-embedding.gguf",
    }
    defaults.update(overrides)
    return LlamaServerConfig(**defaults)


def argv_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# 1. start() returns a ManagedLlamaServer with correct base_url and argv.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_managed_server_with_expected_argv() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)
    config = make_config(
        port=8123,
        mmproj_path="/models/granite-docling-mmproj.gguf",
        extra_args=["-c", "2048", "-t", "4"],
    )

    server = await manager.start(config)

    assert isinstance(server, ManagedLlamaServer)
    assert server.config is config
    assert server.port == 8123
    assert server.base_url == "http://127.0.0.1:8123"
    assert manager.list_active() == [server]

    argv = spawned[0].argv
    assert argv[0] == "/usr/local/bin/llama-server"
    assert "-m" in argv
    assert argv_value(argv, "-m") == "/models/granite-embedding.gguf"
    assert "--host" in argv
    assert argv_value(argv, "--host") == "127.0.0.1"
    assert "--port" in argv
    assert argv_value(argv, "--port") == "8123"
    # mmproj set → appended right after the core flags.
    assert "--mmproj" in argv
    assert (
        argv_value(argv, "--mmproj") == "/models/granite-docling-mmproj.gguf"
    )
    # extra_args land verbatim at the end.
    assert argv[-4:] == ["-c", "2048", "-t", "4"]

    await server.stop()


@pytest.mark.asyncio
async def test_mmproj_omitted_when_empty() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)

    server = await manager.start(make_config(port=8124))

    assert "--mmproj" not in spawned[0].argv
    await server.stop()


# ---------------------------------------------------------------------------
# 2. port=0 picks a free ephemeral port reflected in base_url and argv.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ephemeral_port_resolution() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)

    server = await manager.start(make_config())  # port defaults to 0

    assert isinstance(server.port, int)
    assert server.port > 0
    assert server.base_url == f"http://127.0.0.1:{server.port}"
    argv = spawned[0].argv
    assert argv_value(argv, "--port") == str(server.port)

    await server.stop()


# ---------------------------------------------------------------------------
# 3. Prober succeeding only after N calls — polling loop works.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prober_succeeding_after_three_calls() -> None:
    spawned: list[FakeProcess] = []
    prober, state = counting_prober(succeed_after=3)
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=prober)

    server = await manager.start(make_config(port=8125))

    assert state["calls"] == 3
    assert server.port == 8125
    assert len(manager.list_active()) == 1

    await server.stop()


# ---------------------------------------------------------------------------
# 4. Prober never ready → LlamaServerError within the timeout, no leaked proc.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_timeout_stops_process_and_raises() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=never_prober)
    config = make_config()
    # Production validation floors startup_timeout_s at 5; tests bypass the
    # guard post-init to keep the suite fast.
    config.startup_timeout_s = 1.0

    with pytest.raises(LlamaServerError, match="healthy"):
        await manager.start(config)

    fake = spawned[0]
    assert "terminate" in fake.calls or "kill" in fake.calls
    assert manager.list_active() == []


# ---------------------------------------------------------------------------
# 5. Launcher raising → wrapped in LlamaServerError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launcher_failure_wrapped_in_llama_server_error() -> None:
    def broken_launcher(argv: list[str]) -> FakeProcess:
        raise FileNotFoundError("no such binary")

    manager = LlamaServerManager(launcher=broken_launcher, prober=ok_prober)

    with pytest.raises(LlamaServerError, match="failed to launch"):
        await manager.start(make_config())
    assert manager.list_active() == []


# ---------------------------------------------------------------------------
# 6. Exclusive mode (max_servers=1): second start stops the first server.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclusive_mode_stops_oldest_server() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(
        max_servers=1, launcher=make_launcher(spawned), prober=ok_prober
    )

    first = await manager.start(make_config(port=8131))
    second = await manager.start(make_config(port=8132))

    assert "terminate" in spawned[0].calls  # oldest process was stopped...
    assert "kill" not in spawned[0].calls  # ...gracefully via SIGTERM+wait.
    assert manager.list_active() == [second]  # only the new one is active
    assert first is not second

    await manager.stop_all()


# ---------------------------------------------------------------------------
# 7. stop() is idempotent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=ok_prober)

    server = await manager.start(make_config(port=8133))
    await server.stop()
    await server.stop()  # Must not raise nor re-signal the process.

    assert spawned[0].calls.count("terminate") == 1
    assert manager.list_active() == []


# ---------------------------------------------------------------------------
# 8. stop_all() stops everything and clears the active list.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_all_clears_every_server() -> None:
    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(
        max_servers=2, launcher=make_launcher(spawned), prober=ok_prober
    )

    await manager.start(make_config(port=8141))
    await manager.start(make_config(port=8142))
    assert len(manager.list_active()) == 2

    await manager.stop_all()

    assert manager.list_active() == []
    for fake in spawned:
        assert "terminate" in fake.calls
    # stop_all afterwards is a harmless no-op.
    await manager.stop_all()
    assert manager.list_active() == []


# ---------------------------------------------------------------------------
# Bonus: config validation guards.
# ---------------------------------------------------------------------------


def test_config_validation_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="binary_path"):
        LlamaServerConfig(binary_path="", model_path="/m.gguf")
    with pytest.raises(ValueError, match="model_path"):
        LlamaServerConfig(binary_path="/bin/llama-server", model_path="")
    with pytest.raises(ValueError, match="startup_timeout_s"):
        LlamaServerConfig(
            binary_path="/bin/llama-server", model_path="/m.gguf", startup_timeout_s=4.9
        )


def test_config_defaults() -> None:
    config = make_config()
    assert config.host == "127.0.0.1"
    assert config.port == 0
    assert config.mmproj_path == ""
    assert config.extra_args == []
    assert config.startup_timeout_s == 120.0
    assert config.health_path == "/health"


@pytest.mark.asyncio
async def test_prober_receives_base_url_plus_health_path() -> None:
    seen: list[str] = []

    async def prober(url: str) -> bool:
        seen.append(url)
        return True

    spawned: list[FakeProcess] = []
    manager = LlamaServerManager(launcher=make_launcher(spawned), prober=prober)

    server = await manager.start(make_config(port=8151, health_path="/healthz"))

    assert seen == ["http://127.0.0.1:8151/healthz"]
    await server.stop()
