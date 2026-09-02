"""Platform-specific utilities."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_linux() -> bool:
    return platform.system() == "Linux"


def find_ollama_binary() -> str | None:
    """Find the ollama binary in PATH."""
    return shutil.which("ollama")


def get_config_dir() -> Path:
    """Get platform-appropriate config directory.

    Default is ``~/.config/ollama-tui`` on POSIX and ``%APPDATA%/ollama-tui``
    on Windows. ``EDUNEXUS_DATA_DIR`` env var is an opt-in override (when set
    it takes precedence for testing / custom locations).
    """
    import os

    env = os.environ.get("EDUNEXUS_DATA_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if is_windows():
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ollama-tui"
    return Path.home() / ".config" / "ollama-tui"
