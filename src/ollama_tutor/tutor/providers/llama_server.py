"""Subprocess manager for llama.cpp's ``llama-server`` (Phase 1 runtime).

Serves local GGUF models (Granite Embedding / Granite-Docling vision /
Granite LLM) on localhost. Designed for CPU-only low-spec machines: by
default **at most one server runs at a time** — starting a new server
stops the oldest active one first, so load/unload is strictly sequential
and the RAM budget is respected.

This module is UI-agnostic (no fastapi/textual) and fully standalone:
it depends only on the standard library and ``httpx``.

Example:
    manager = LlamaServerManager()
    config = LlamaServerConfig(
        binary_path="/usr/local/bin/llama-server",
        model_path="/models/granite-embedding.gguf",
    )
    server = await manager.start(config)
    try:
        ...  # POST to server.base_url + "/embeddings" etc.
    finally:
        await server.stop()  # idempotent
"""

from __future__ import annotations

import asyncio
import inspect
import socket
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = [
    "LlamaServerConfig",
    "LlamaServerError",
    "LlamaServerManager",
    "ManagedLlamaServer",
]

_POLL_INTERVAL_S = 0.25
_STOP_GRACE_S = 5.0


class LlamaServerError(RuntimeError):
    """Raised when a managed llama-server fails to start or be stopped."""


@dataclass
class LlamaServerConfig:
    """Launch configuration for one ``llama-server`` process."""

    binary_path: str
    model_path: str
    host: str = "127.0.0.1"
    port: int = 0  # 0 = pick a free ephemeral port at start time.
    mmproj_path: str = ""  # Optional vision projector, appended as --mmproj.
    parallel: int = 1  # llama-server --parallel N (US8: concurrent embeddings).
    extra_args: list[str] = field(default_factory=list)
    startup_timeout_s: float = 120.0
    health_path: str = "/health"

    def __post_init__(self) -> None:
        if not self.binary_path:
            raise ValueError("binary_path must be a non-empty string")
        if not self.model_path:
            raise ValueError("model_path must be a non-empty string")
        if self.startup_timeout_s < 5:
            raise ValueError("startup_timeout_s must be >= 5 seconds")


@dataclass
class ManagedLlamaServer:
    """A running ``llama-server`` process owned by a :class:`LlamaServerManager`."""

    config: LlamaServerConfig
    port: int
    base_url: str
    process: Any  # Opaque handle (asyncio.subprocess.Process in production).
    _manager: LlamaServerManager | None = field(default=None, repr=False, compare=False)
    _stopped: bool = field(default=False, repr=False, compare=False)

    async def stop(self) -> None:
        """Terminate the server. Idempotent and safe under double-stop races.

        Sends SIGTERM, waits up to 5s for exit, then SIGKILL. Swallows
        ``ProcessLookupError`` (process already gone) and removes itself
        from its manager's active set.
        """
        if self._stopped:
            return
        # Set the flag synchronously before any await: concurrent stop()
        # calls can never both pass the guard above.
        self._stopped = True

        process = self.process
        if process is not None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=_STOP_GRACE_S)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await process.wait()
                except ProcessLookupError:
                    pass

        if self._manager is not None:
            self._manager._forget(self)


def _pick_free_port(host: str) -> int:
    """Ask the OS for a free ephemeral TCP port on ``host``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


async def _default_launcher(argv: list[str]) -> Any:
    """Spawn ``llama-server`` with output discarded (low-spec friendly)."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def _default_prober(url: str) -> bool:
    """Return True once ``url`` answers HTTP 200 (connection errors => False)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(url)
            return response.status_code == 200
    except httpx.HTTPError:
        return False


class LlamaServerManager:
    """Owns managed ``llama-server`` processes with sequential exclusivity.

    Args:
        max_servers: Maximum simultaneously-active servers. With the default
            of 1, starting a new server stops the oldest active one first.
        launcher: ``Callable[[list[str]], Any]`` spawning the process.
            Defaults to ``asyncio.create_subprocess_exec`` (DEVNULL output);
            may be sync or async. Injectable for offline tests.
        prober: ``Callable[[str], Awaitable[bool]]`` receiving the health URL;
            returns True when the server is ready. Defaults to an httpx GET
            that accepts HTTP 200. Injectable for offline tests.
    """

    def __init__(
        self,
        *,
        max_servers: int = 1,
        launcher: Any | None = None,
        prober: Any | None = None,
    ) -> None:
        self._max_servers = max_servers
        self._launcher = launcher if launcher is not None else _default_launcher
        self._prober = prober if prober is not None else _default_prober
        self._active: deque[ManagedLlamaServer] = deque()
        # Serialize start(): two concurrent callers must not exceed the
        # budget while both are still probing their processes.
        self._start_lock = asyncio.Lock()

    async def start(self, config: LlamaServerConfig) -> ManagedLlamaServer:
        """Launch ``llama-server`` and wait until its health endpoint is ready.

        Enforces the exclusivity budget first (oldest server stopped when at
        capacity), resolves ``port == 0`` to a free ephemeral port, spawns the
        binary via the launcher, then polls the prober every 0.25s until
        ``config.startup_timeout_s`` elapses.

        Raises:
            LlamaServerError: If the launcher fails, or the server does not
                become healthy before the deadline (the spawned process is
                stopped first — no leaks).
        """
        async with self._start_lock:
            while len(self._active) >= self._max_servers:
                await self._active[0].stop()

            port = config.port
            if port == 0:
                port = _pick_free_port(config.host)

            argv = [
                config.binary_path,
                "-m",
                config.model_path,
                "--host",
                config.host,
                "--port",
                str(port),
            ]
            if config.mmproj_path:
                argv += ["--mmproj", config.mmproj_path]
            if config.parallel > 1:
                argv += ["--parallel", str(config.parallel)]
            argv += list(config.extra_args)

            try:
                launched = self._launcher(argv)
                if inspect.isawaitable(launched):
                    launched = await launched
            except Exception as exc:
                raise LlamaServerError(
                    f"failed to launch {config.binary_path!r}: {exc}"
                ) from exc

            server = ManagedLlamaServer(
                config=config,
                port=port,
                base_url=f"http://{config.host}:{port}",
                process=launched,
                _manager=self,
            )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + config.startup_timeout_s
            health_url = server.base_url + config.health_path
            while True:
                try:
                    ready = bool(await self._prober(health_url))
                except Exception:
                    ready = False  # Prober hiccups count as "not ready yet".
                if ready:
                    break
                if loop.time() >= deadline:
                    await server.stop()  # Never leak the spawned process.
                    raise LlamaServerError(
                        f"llama-server did not become healthy within "
                        f"{config.startup_timeout_s}s (probed {health_url})"
                    )
                await asyncio.sleep(_POLL_INTERVAL_S)

            self._active.append(server)
            return server

    def list_active(self) -> list[ManagedLlamaServer]:
        """Snapshot of currently-active servers, oldest first."""
        return list(self._active)

    async def stop_all(self) -> None:
        """Stop every active server (oldest first) and clear the registry."""
        while self._active:
            await self._active[0].stop()

    def _forget(self, server: ManagedLlamaServer) -> None:
        """Remove ``server`` from the active set; no-op if absent."""
        try:
            self._active.remove(server)
        except ValueError:
            pass
