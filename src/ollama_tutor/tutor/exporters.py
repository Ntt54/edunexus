"""Exporteurs stdlib (010 P2-Robustesse, T042).

Adapté de ``autreprojet/OpenTutor-main`` (``routers/export.py`` : export
Anki TSV + calendrier ICS) SANS genanki/icalendar : TSV compatible import
Anki (``front\\tback``) et template ICS maison (RFC 5545 minimal, CRLF).

Stdlib seul, aucun import UI (ni fastapi ni textual).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _clean_cell(value: Any) -> str:
    """Neutralise tabulations/retours pour une cellule TSV (parsable)."""
    text = str(value or "")
    return " ".join(text.replace("\t", " ").splitlines()).strip()


def flashcards_to_tsv(rows: list[dict[str, Any]]) -> str:
    """Flashcards → TSV Anki : une ligne ``question\\tréponse`` par carte.

    ``rows`` : ``[{question, answer}]``. Chaque ligne contient exactement
    une tabulation (golden testé).
    """
    lines = [
        f"{_clean_cell(r.get('question', ''))}\t{_clean_cell(r.get('answer', ''))}"
        for r in rows
    ]
    return "".join(line + "\n" for line in lines)


def _escape_ics_text(value: Any) -> str:
    """Échappe virgule/point-virgule/retours (RFC 5545 §3.3.11)."""
    text = str(value or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(",", "\\,").replace(";", "\\;")
    return "\\n".join(text.splitlines())


def _safe_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in str(name or ""))
    return safe.strip() or "export"


def reviews_to_ics(
    events: list[dict[str, Any]],
    calendar_name: str = "EduNexus",
    prodid: str = "-//EduNexus//Reviews//FR",
) -> str:
    """Révisions dues → calendrier ICS (VEVENT DATE, STATUS).

    ``events`` : ``[{uid, date: "AAAAMMJJ", summary, description?,
    completed?}]``. Sortie CRLF, ``BEGIN/END:VEVENT`` équilibrés.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        f"X-WR-CALNAME:{_safe_name(calendar_name)}",
    ]
    for ev in events:
        uid = str(ev.get("uid", ""))
        day = "".join(c for c in str(ev.get("date", "")) if c.isdigit())
        if not uid or len(day) != 8:
            continue
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}@edunexus",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{day}",
                f"DTEND;VALUE=DATE:{day}",
                f"SUMMARY:{_escape_ics_text(ev.get('summary', ''))}",
                f"DESCRIPTION:{_escape_ics_text(ev.get('description', ''))}",
                f"STATUS:{'COMPLETED' if ev.get('completed') else 'CONFIRMED'}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


__all__ = ["flashcards_to_tsv", "reviews_to_ics"]
