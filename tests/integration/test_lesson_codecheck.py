"""Validation sandbox des blocs Python des cours générés.

Constat prod : exemples invalides (`a := 3` hors contexte, indentations
cassées, sorties `print` fausses, noms indéfinis).

100 % offline (subprocess Python local via `run_python`, aucun réseau) :
- extraction ```python (max 12, non-python ignorés) ;
- verdict par bloc via `run_python(code, timeout=3.0)`, safety ACTIVE ;
- persistance `validation` (JSON, NULL défaut, migration 2×) + exposition ;
- `generate_course` mocké ⇒ rapport présent, contenu INCHANGÉ.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.lesson_discussion import (
    LessonDiscussionService,
    extract_python_blocks,
    validate_lesson_codeblocks,
)
from src.ollama_tutor.tutor.store import LibraryStore


CONTENT = """# Cours

```python
print(6 * 7)
```

Du texte.

```javascript
console.log("pas python");
```

```python
x = 1
print(x + 1)
```

Bloc non clôturé :
```python
print("oublié")
"""


def test_extract_python_blocks() -> None:
    blocks = extract_python_blocks(CONTENT)
    assert len(blocks) == 2
    assert blocks[0].strip() == "print(6 * 7)"
    assert "console.log" not in "\n".join(blocks)


def test_extract_ignores_untagged_and_plain() -> None:
    assert extract_python_blocks("```\nprint(1)\n```") == []
    assert extract_python_blocks("aucun bloc") == []


def test_extract_cap_12() -> None:
    content = "".join(f"```python\nprint({i})\n```\n" for i in range(15))
    assert len(extract_python_blocks(content)) == 12


def test_validate_ok_block() -> None:
    report = validate_lesson_codeblocks(["print(6 * 7)"])
    assert set(report) == {"blocks", "checked_at"}
    assert report["blocks"] == [{"index": 0, "ok": True, "error": None}]


def test_validate_runtime_error_block() -> None:
    report = validate_lesson_codeblocks(["print(undefined_name_xyz)"])
    (block,) = report["blocks"]
    assert block["index"] == 0
    assert block["ok"] is False
    assert "NameError" in (block["error"] or "")


def test_validate_syntax_error_block() -> None:
    report = validate_lesson_codeblocks(["def broken(:\n"])
    (block,) = report["blocks"]
    assert block["ok"] is False
    assert block["error"]


def test_validate_timeout_block() -> None:
    report = validate_lesson_codeblocks(["while True:\n    pass"])
    (block,) = report["blocks"]
    assert block["ok"] is False
    assert "timeout" in (block["error"] or "").lower()


def test_validate_blocked_block() -> None:
    report = validate_lesson_codeblocks(["import socket\nprint(1)"])
    (block,) = report["blocks"]
    assert block["ok"] is False
    assert "sécurité" in (block["error"] or "")


def test_validate_never_skips_safety() -> None:
    # `__import__("os").system(...)` doit être bloqué, jamais exécuté.
    report = validate_lesson_codeblocks(['__import__("os").system("echo HACKED")'])
    (block,) = report["blocks"]
    assert block["ok"] is False
    assert "sécurité" in (block["error"] or "")


@pytest.mark.asyncio
async def test_validate_from_running_loop_uses_dedicated_thread() -> None:
    """Chemin prod (route async) : loop courante ⇒ thread dédié."""
    report = validate_lesson_codeblocks(["print(1 + 1)"])
    assert report["blocks"] == [{"index": 0, "ok": True, "error": None}]


def test_validate_empty_blocks() -> None:
    report = validate_lesson_codeblocks([])
    assert report["blocks"] == []
    assert report["checked_at"]


# ---------------------------------------------------------------------------
# Persistance + exposition
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> LibraryStore:
    return LibraryStore(tmp_path / "config")


def _discussion(store: LibraryStore):
    subj = store.create_subject("Python")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-boucles", "Boucles", ordinal=0)
    svc = LessonDiscussionService(store)
    return svc, svc.get_or_create_discussion(step.id, "alice"), subj


def test_validation_migration_idempotent(store: LibraryStore) -> None:
    store._migrate_lesson_validation_column()
    store._migrate_lesson_validation_column()
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(generated_lesson_contents)")}
    assert "validation" in cols


def test_add_generated_content_validation_roundtrip(store: LibraryStore) -> None:
    svc, disc, _subj = _discussion(store)
    report = {"blocks": [{"index": 0, "ok": True, "error": None}], "checked_at": "t"}
    obj = store.add_generated_content(
        disc.id, "lesson_course", "cours", validation=report
    )
    assert obj.validation == report
    assert obj.to_dict()["validation"] == report
    assert store.list_generated_contents(disc.id)[0].validation == report
    # Défaut : NULL.
    obj2 = store.add_generated_content(disc.id, "lesson_summary", "synthèse")
    assert obj2.validation is None
    assert obj2.to_dict()["validation"] is None


def test_e2e_generate_course_reports_but_keeps_content(store: LibraryStore) -> None:
    svc, disc, _subj = _discussion(store)
    llm_body = (
        "# Cours : Boucles\n\n"
        "```python\nprint(2 + 3)\n```\n\n"
        "```python\nprint(1 // 0)\n```\n\n" + "mot " * 820
    )

    class FakeTutor:
        def generate_lesson_text(self, kind, notion, excerpts, question=None):
            return llm_body

    svc.tutor_service = FakeTutor()  # type: ignore[assignment]
    result = svc.generate_course(disc.id, "alice")
    # Contenu conservé TEL QUEL (jamais modifié/supprimé).
    assert result["content"] == llm_body
    assert result["fallback"] is False
    validation = result["validation"]
    assert validation is not None
    assert [b["ok"] for b in validation["blocks"]] == [True, False]
    assert validation["blocks"][1]["error"]
    assert validation["checked_at"]
    # Persisté et ré-exposé à l'identique.
    stored = store.list_generated_contents(disc.id)[0]
    assert stored.validation == validation
    assert json.dumps(validation)


def test_e2e_generate_course_no_blocks_validation_null(store: LibraryStore) -> None:
    svc, disc, _subj = _discussion(store)
    llm_body = "# Cours : Boucles\n\n" + "mot " * 820

    class FakeTutor:
        def generate_lesson_text(self, kind, notion, excerpts, question=None):
            return llm_body

    svc.tutor_service = FakeTutor()  # type: ignore[assignment]
    result = svc.generate_course(disc.id, "alice")
    assert result["content"] == llm_body
    assert result["validation"] is None
