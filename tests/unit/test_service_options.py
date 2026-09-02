"""Tests for CPU-oriented inference options used by TutorService."""

from __future__ import annotations

from pathlib import Path

from src.ollama_tutor.config import Config
from src.ollama_tutor.models import OllamaOptions
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


class _NoopClient:
    pass


def _service(tmp_path: Path) -> tuple[TutorService, Config]:
    config = Config(tmp_path / "config")
    service = TutorService(LibraryStore(config.config_dir), _NoopClient(), config)
    return service, config


def test_generation_options_use_cpu_friendly_defaults(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    options = service._generation_options()

    assert options.num_ctx == 4096
    assert options.num_predict == 2048
    assert options.num_thread == 3


def test_generation_options_preserve_explicit_settings(tmp_path: Path) -> None:
    service, config = _service(tmp_path)
    config.options = OllamaOptions(
        num_ctx=8192,
        num_predict=128,
        num_thread=4,
        num_batch=256,
        keep_alive="1h",
        temperature=0.2,
    )

    options = service._generation_options()

    assert options.to_dict() == {
        "temperature": 0.2,
        "num_ctx": 8192,
        "num_predict": 128,
        "num_batch": 256,
        "num_thread": 4,
        "keep_alive": "1h",
    }
