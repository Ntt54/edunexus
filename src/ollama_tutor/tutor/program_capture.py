"""Capture de programme par photo/PDF (Feature 008, US6).

``ProgramCaptureService`` capture un programme / table des matières depuis
une photo ou un PDF via le pipeline OCR local (``HybridDocumentParser``),
structure le texte reconnu en arbre éditable de ``ProgramNode``, signale les
passages incertains et permet la correction avant génération du parcours
(FR-023..FR-027). Le traitement est incrémental via une file avec statut
visible.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import CapturedProgram, ProgramNode
from .store import LibraryStore


class ProgramCaptureService:
    """Capture and structure a program from photo/PDF (US6)."""

    def __init__(self, store: LibraryStore, document_parser: Any | None = None) -> None:
        self.store = store
        self.document_parser = document_parser

    # ------------------------------------------------------------------
    # Capture entry point
    # ------------------------------------------------------------------

    async def capture(self, subject_id: str, source_path: str,
                      source_type: str = "photo") -> dict[str, Any]:
        """Start a capture; returns the program with queue status (FR-023)."""
        program = CapturedProgram(
            id=_new_id(),
            subject_id=subject_id,
            source_type=source_type,
            status="processing",
            validation_status="pending",
            created_at=_now(),
        )
        self.store.create_captured_program(program)

        recognized_text = ""
        nodes: list[ProgramNode] = []
        try:
            if self.document_parser is not None:
                parsed = await self.document_parser.parse(Path(source_path))
                pages = parsed.get("pages", [])
                recognized_text = "\n".join(p.get("text", "") for p in pages)
            else:
                # No OCR provider: fall back to reading the file as text.
                recognized_text = Path(source_path).read_text(encoding="utf-8", errors="ignore")
            nodes = self._structure(program.id, recognized_text)
        except Exception as e:  # fail-closed: surface on the program row
            program.status = "error"
            program.recognized_text = f"[erreur OCR] {e}"
            self.store.update_captured_program(program)
            return self._payload(program, [])

        program.status = "ready"
        program.recognized_text = recognized_text
        self.store.update_captured_program(program)
        self.store.replace_program_nodes(program.id, nodes)
        return self._payload(program, nodes)

    # ------------------------------------------------------------------
    # Read / correct
    # ------------------------------------------------------------------

    def get(self, program_id: str) -> dict[str, Any]:
        program = self.store.get_captured_program(program_id)
        if program is None:
            raise KeyError(f"Unknown program: {program_id}")
        nodes = self.store.get_program_nodes(program_id)
        return self._payload(program, nodes)

    def correct_node(self, node_id: str, title: str) -> dict[str, Any]:
        """Correct an OCR node before path generation (FR-027)."""
        node = self._find_node(node_id)
        node.title = title
        node.validation_status = "corrected"
        self.store.update_program_node(node)
        return node.to_dict()

    def confirm(self, program_id: str) -> dict[str, Any]:
        """Confirm the whole program (FR-026)."""
        program = self.store.get_captured_program(program_id)
        if program is None:
            raise KeyError(f"Unknown program: {program_id}")
        program.validation_status = "confirmed"
        self.store.update_captured_program(program)
        nodes = self.store.get_program_nodes(program_id)
        return self._payload(program, nodes)

    # ------------------------------------------------------------------
    # Structuring (deterministic, no LLM)
    # ------------------------------------------------------------------

    def _structure(self, program_id: str, text: str) -> list[ProgramNode]:
        """Turn recognized text into a tree of ProgramNode.

        Lines matching a numbered heading (e.g. "1.2 Titre", "Chapitre 3")
        become chapter/sub-part nodes; other non-empty lines become
        competency leaves. Uncertain lines (very short, or containing OCR
        artifacts) are flagged ``pending``.
        """
        nodes: list[ProgramNode] = []
        current_parent = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = re.match(r"^(\d+(?:\.\d+)*)[\s.)-]*(.*)$", line)
            if m:
                number, title = m.group(1), m.group(2).strip()
                depth = number.count(".")
                kind = "chapter" if depth == 0 else "sub_part"
                node = ProgramNode(
                    id=_new_id(), program_id=program_id, parent_id="",
                    title=title or number, kind=kind, origin="ocr",
                    validation_status="pending",
                )
                nodes.append(node)
                if depth == 0:
                    current_parent = node.id
                else:
                    node.parent_id = current_parent
                continue
            # Non-numbered line: competency leaf under the current chapter.
            node = ProgramNode(
                id=_new_id(), program_id=program_id, parent_id=current_parent,
                title=line[:120], kind="competency", origin="ocr",
                validation_status="pending",
            )
            nodes.append(node)
        return nodes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_node(self, node_id: str) -> ProgramNode:
        node = self.store.get_program_node(node_id)
        if node is None:
            raise KeyError(f"Unknown program node: {node_id}")
        return node

    def _payload(self, program: CapturedProgram, nodes: list[ProgramNode]) -> dict[str, Any]:
        return {
            "program": {
                **program.to_dict(),
                "nodes": [n.to_dict() for n in nodes],
            }
        }


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
