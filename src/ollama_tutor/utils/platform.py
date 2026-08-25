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
    """Get platform-appropriate config directory."""
    if is_windows():
        import os
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ollama-tui"
    return Path.home() / ".config" / "ollama-tui"
