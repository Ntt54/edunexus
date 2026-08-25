"""Import-lint contract: tutor core stays UI-framework-free (research D13).

Mirrors ``tests/contract/test_core_imports.py`` but for
``src/ollama_tutor/tutor/``: every module under that package must be free of
textual/fastapi imports (contracts/tutor-core-api.md invariant 1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

TUTOR_DIR = Path(__file__).resolve().parents[2] / "src" / "ollama_tutor" / "tutor"
FORBIDDEN_IMPORTS = (
    "import textual",
    "from textual",
    "import fastapi",
    "from fastapi",
)


def _tutor_python_files() -> list[Path]:
    return sorted(TUTOR_DIR.glob("*.py"))


def test_tutor_package_exists_with_modules() -> None:
    assert TUTOR_DIR.is_dir(), "src/ollama_tutor/tutor must exist"
    files = _tutor_python_files()
    assert files, "tutor package must contain at least one module"


@pytest.mark.parametrize("path", _tutor_python_files(), ids=lambda p: p.name)
def test_tutor_module_has_no_ui_framework_imports(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_IMPORTS:
        assert forbidden not in source, (
            f"{path.name} must not contain {forbidden!r} "
            "(tutor/ is UI-framework-free by contract)"
        )
