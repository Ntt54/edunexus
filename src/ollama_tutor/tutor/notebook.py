"""Carnet de matière local (Feature 008, US8).

``NotebookService`` regroupe pour une matière : livres, programme, objectifs,
niveau, compétences, notes et parcours. Il exécute des **actions RAG**
(FR-033) qui produisent des sorties liées à leurs sources (FR-035) et
supprimables (FR-035), avec provenance (FR-036). Le contexte RAG (FR-034)
est construit à partir des chunks indexés de la matière.

Ce module est UI-agnostique (principe I) : il ne dépend que de ``store`` et
``models``, jamais de fastapi/textual. Le LLM est injectable pour rester
testable hors-ligne (principe III).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .models import NotebookOutput, SourceReference
from .store import LibraryStore

# Actions du carnet (FR-033) et leur type de sortie associé.
ACTIONS = {
    "summarize_source": "summary",
    "compare_chapters": "comparison",
    "create_study_sheet": "study_sheet",
    "quiz_without_answer": "quiz",
    "explain_with_example": "explanation",
    "find_prerequisites": "summary",
    "create_path": "study_sheet",
    "check_missing": "summary",
}


class NotebookService:
    """Local subject notebook with RAG actions (US8)."""

    def __init__(
        self,
        store: LibraryStore,
        llm: Any | None = None,
        rag_context: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.store = store
        self.llm = llm
        # Injectable RAG context builder (default: indexed chunks).
        self._rag_context = rag_context or self._default_rag_context

    # ------------------------------------------------------------------
    # Lecture / notes
    # ------------------------------------------------------------------

    def get(self, subject_id: str) -> dict[str, Any]:
        """Return the notebook with its sources and outputs (FR-034/FR-035)."""
        nb = self.store.get_or_create_notebook(subject_id)
        outputs = self.store.list_notebook_outputs(nb.id)
        return {
            "notebook": {
                "id": nb.id,
                "subject_id": nb.subject_id,
                "notes": nb.notes,
                "sources": [b.to_dict() for b in self.store.list_books(subject_id)],
                "outputs": [o.to_dict() for o in outputs],
            }
        }

    def add_note(self, subject_id: str, note: str) -> dict[str, Any]:
        """Add a personal note (FR-032)."""
        note = (note or "").strip()
        if not note:
            raise ValueError("Note must be non-empty")
        nb = self.store.add_notebook_note(subject_id, note)
        return {"notebook": {"notes": nb.notes}}

    # ------------------------------------------------------------------
    # Actions RAG (FR-033)
    # ------------------------------------------------------------------

    async def run_action(
        self, subject_id: str, action: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a notebook action and persist its output (FR-033/FR-035)."""
        params = params or {}
        if action not in ACTIONS:
            raise ValueError(f"Unknown notebook action: {action}")
        kind = ACTIONS[action]
        context = self._rag_context(subject_id)
        sources = self._sources_from_context(context)
        content = await self._generate(action, kind, context, params)
        nb = self.store.get_or_create_notebook(subject_id)
        output = NotebookOutput(
            id=uuid.uuid4().hex[:12],
            notebook_id=nb.id,
            kind=kind,
            content=content,
            sources=sources,
            created_at=_now(),
        )
        self.store.add_notebook_output(output)
        return {"output": output.to_dict()}

    def delete_output(self, output_id: str) -> dict[str, Any]:
        """Delete a notebook output (FR-035)."""
        self.store.delete_notebook_output(output_id)
        return {"deleted": True}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _default_rag_context(self, subject_id: str) -> list[dict[str, Any]]:
        """RAG context from indexed chunks (FR-034)."""
        chunks = self.store.get_indexed_chunks(subject_id)
        return [
            {
                "book_id": str(c.get("book_id", "")),
                "chapter": c.get("chapter", ""),
                "section": c.get("section", ""),
                "page": c.get("page"),
                "text": (c.get("text") or "")[:500],
            }
            for c in chunks
        ]

    @staticmethod
    def _sources_from_context(context: list[dict[str, Any]]) -> list[SourceReference]:
        """Build source references from the RAG context (FR-035)."""
        seen: set[str] = set()
        sources: list[SourceReference] = []
        for c in context:
            book_id = str(c.get("book_id", ""))
            if not book_id or book_id in seen:
                continue
            seen.add(book_id)
            sources.append(SourceReference(
                book_id=book_id,
                chapter=str(c.get("chapter", "") or ""),
                page=c.get("page"),
                excerpt=(c.get("text") or "")[:200],
                confidence=0.8,
            ))
        return sources

    async def _generate(
        self,
        action: str,
        kind: str,
        context: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> str:
        """Generate output content. Uses the LLM when available, else a
        deterministic fallback so the service stays testable offline."""
        if self.llm is not None:
            try:
                return await self._llm_generate(action, kind, context, params)
            except Exception:
                # Fall back to deterministic content on LLM failure.
                pass
        return self._deterministic_content(action, kind, context, params)

    async def _llm_generate(
        self,
        action: str,
        kind: str,
        context: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> str:
        excerpts = "\n".join(
            f"- [{c.get('chapter') or c.get('section') or '?'}] {c.get('text')}"
            for c in context[:8]
        )
        prompt = (
            f"Action carnet: {action}\n"
            f"Type de sortie: {kind}\n"
            f"Contexte (extraits):\n{excerpts}\n"
            "Produis une réponse pédagogique en français, concise et structurée."
        )
        messages = [{"role": "user", "content": prompt}]
        text = await self.llm.chat_stream(messages, {})
        if isinstance(text, str):
            return text
        # chat_stream yields StreamEvent objects; extract content text.
        parts = []
        async for chunk in text:
            if chunk.kind == "content" and chunk.text:
                parts.append(chunk.text)
        return "".join(parts)

    @staticmethod
    def _deterministic_content(
        action: str,
        kind: str,
        context: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> str:
        """Deterministic fallback content (offline tests, no LLM)."""
        titles = [
            c.get("chapter") or c.get("section") or "notion"
            for c in context
        ]
        unique = list(dict.fromkeys(t for t in titles if t))
        if kind == "quiz":
            # Quiz without answer (FR-033): questions only, no answers.
            lines = [f"Question {i + 1} : explique « {t} »." for i, t in enumerate(unique[:5])]
            return "\n".join(lines) or "Aucune notion disponible pour le quiz."
        if kind == "comparison":
            return "Comparaison des chapitres :\n" + "\n".join(f"- {t}" for t in unique[:6])
        if kind == "study_sheet":
            return "Fiche de révision :\n" + "\n".join(f"- {t}" for t in unique[:8])
        if kind == "explanation":
            return "Explication avec exemple :\n" + "\n".join(f"- {t}" for t in unique[:5])
        # summary / default
        return "Résumé de la matière :\n" + "\n".join(f"- {t}" for t in unique[:8])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
