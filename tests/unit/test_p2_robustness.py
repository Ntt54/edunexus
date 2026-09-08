"""US6 P2-Robustesse (T038) — AppError, durcissement upload, exporters TSV/ICS.

Adapté de ``autreprojet/OpenTutor-main`` (``libs/exceptions.py`` AppError +
handlers ``main.py``, ``routers/export.py`` Anki/ICS) et
``autreprojet/open-tutor-ai-CE-main`` (``routers/files.py`` upload 64 Ko +
413 précoce, ``files/service.py`` require_owned) vers stdlib pur
(sans genanki/icalendar : TSV + template ICS maison).

100 % offline, aucun réseau/LLM.
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.errors import (
    AppError,
    ConflictError,
    IngestionError,
    LLMUnavailableError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from src.ollama_tutor.tutor.exporters import flashcards_to_tsv, reviews_to_ics


# ---------------------------------------------------------------------------
# T039 — AppError + mapping statuts
# ---------------------------------------------------------------------------


def test_app_error_to_dict() -> None:
    err = AppError("boom")
    assert err.to_dict() == {"code": "internal_error", "message": "boom", "status": 500}
    assert isinstance(err, Exception)


def test_not_found_error_detail() -> None:
    err = NotFoundError("Livre", "abc123")
    assert err.status == 404 and err.code == "not_found"
    assert "abc123" in err.message
    assert NotFoundError("Livre").message == "Livre not found"


@pytest.mark.parametrize(
    ("exc", "code", "status"),
    [
        (NotFoundError("X"), "not_found", 404),
        (ConflictError("pris"), "conflict", 409),
        (ValidationError("invalide"), "validation_error", 422),
        (LLMUnavailableError(), "llm_unavailable", 503),
        (IngestionError("parse KO"), "ingestion_error", 500),
        (PayloadTooLargeError(), "payload_too_large", 413),
    ],
)
def test_error_status_mapping(exc: AppError, code: str, status: int) -> None:
    assert exc.code == code
    assert exc.status == status
    body = exc.to_dict()
    assert body == {"code": code, "message": exc.message, "status": status}


def test_ingestion_error_source_detail() -> None:
    err = IngestionError("contenu vide", source="cours.pdf")
    assert "cours.pdf" in err.message and "contenu vide" in err.message


def test_llm_unavailable_default_message() -> None:
    assert "indisponible" in LLMUnavailableError().message.lower() or "unavailable" in LLMUnavailableError().message.lower()


# ---------------------------------------------------------------------------
# T040 — handler AppError du serveur (sans démarrer le serveur)
# ---------------------------------------------------------------------------


def test_server_app_error_handler_maps_status(tmp_path: Path) -> None:
    import src.ollama_tutor.web.server as web_server

    app = web_server.create_app(config_dir=tmp_path / "config")
    handler = app.exception_handlers.get(AppError)
    assert handler is not None, "handler AppError enregistré"

    async def _call(exc: AppError):
        return await handler(None, exc)  # type: ignore[arg-type]

    resp = asyncio.run(_call(NotFoundError("Livre", "x")))
    assert resp.status_code == 404
    assert json.loads(bytes(resp.body)) == {
        "code": "not_found",
        "message": "Livre x not found",
        "status": 404,
    }
    resp = asyncio.run(_call(PayloadTooLargeError()))
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# T041 — durcissement upload (service, offline)
# ---------------------------------------------------------------------------


def test_validate_upload_length_early_413() -> None:
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import (
        MAX_UPLOAD_BYTES,
        TutorService,
        validate_upload_length,
    )
    from src.ollama_tutor.tutor.store import LibraryStore

    validate_upload_length(None)
    validate_upload_length("123")
    validate_upload_length(str(MAX_UPLOAD_BYTES))
    with pytest.raises(PayloadTooLargeError):
        validate_upload_length(str(MAX_UPLOAD_BYTES + 1))
    # Non numérique : pas de pré-contrôle (lecture plafonnée ensuite).
    validate_upload_length("inconnu")
    assert MAX_UPLOAD_BYTES == 100 * 1024 * 1024


def test_read_limited_upload_chunks_and_cap() -> None:
    from src.ollama_tutor.tutor.service import read_limited_upload

    seen_sizes: list[int] = []

    async def _run(total: int, cap: int) -> bytes:
        left = total

        async def read(n: int) -> bytes:
            nonlocal left
            seen_sizes.append(n)
            if left <= 0:
                return b""
            chunk = b"x" * min(n, left)
            left -= len(chunk)
            return chunk

        return await read_limited_upload(read, max_bytes=cap)

    data = asyncio.run(_run(3 * 65536 + 10, 10 * 1024 * 1024))
    assert len(data) == 3 * 65536 + 10
    assert all(s == 65536 for s in seen_sizes), "lecture par chunks de 64 Ko"
    with pytest.raises(PayloadTooLargeError):
        asyncio.run(_run(200, 100))


def test_require_subject_book_ownership(tmp_path: Path) -> None:
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService
    from src.ollama_tutor.tutor.store import LibraryStore

    store = LibraryStore(tmp_path)
    svc = TutorService(store, None, Config(config_dir=tmp_path))
    src = tmp_path / "cours.txt"
    src.write_text("contenu " * 20, encoding="utf-8")
    book = svc.import_and_index("SVT", str(src), background=False)

    found = svc.require_subject_book(
        store.get_book_subject_id(book.id), book.id  # type: ignore[arg-type]
    )
    assert found.id == book.id
    with pytest.raises(NotFoundError):
        svc.require_subject_book("matiere-inconnue", book.id)
    with pytest.raises(NotFoundError):
        svc.require_subject_book(
            store.get_book_subject_id(book.id), "livre-inconnu"  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# T042 — exporters TSV + ICS (goldens)
# ---------------------------------------------------------------------------


def test_tsv_golden_parseable() -> None:
    rows = [
        {"question": "1/2 + 1/4 = ?", "answer": "3/4"},
        {"question": "Q\tavec\ttabs\nmultiligne", "answer": "R"},
    ]
    out = flashcards_to_tsv(rows)
    assert out == "1/2 + 1/4 = ?\t3/4\nQ avec tabs multiligne\tR\n"
    for line in out.splitlines():
        assert len(line.split("\t")) == 2, "TSV parsable (front/back)"


def test_ics_golden_structure() -> None:
    events = [
        {"uid": "a1", "date": "20260910", "summary": "Réviser mitose",
         "description": "Chapitre 4", "completed": False},
        {"uid": "b2", "date": "20260911", "summary": "Finir, avec; virgule",
         "description": "", "completed": True},
    ]
    ics = reviews_to_ics(events, calendar_name="EduNexus")
    assert "\r\n" in ics, "CRLF RFC"
    assert ics.count("BEGIN:VEVENT") == 2 == ics.count("END:VEVENT")
    assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
    assert "SUMMARY:Réviser mitose" in ics
    assert "SUMMARY:Finir\\, avec\\; virgule" in ics, "échappement RFC5545"
    assert "STATUS:COMPLETED" in ics and "STATUS:CONFIRMED" in ics
    assert "DTSTART;VALUE=DATE:20260910" in ics


# ---------------------------------------------------------------------------
# T043 — script smoke (existence + syntaxe)
# ---------------------------------------------------------------------------


def test_smoke_script_present_and_valid() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "smoke_010.sh"
    assert script.exists(), "scripts/smoke_010.sh requis (T043)"
    assert script.stat().st_mode & stat.S_IXUSR, "script exécutable"
    text = script.read_text(encoding="utf-8")
    assert "pytest" in text and "9215" in text
