"""Import de photo dans une conversation (Feature 008, US7).

``ConversationPhotoService`` importe une photo (ou un PDF) dans une
conversation de tutorat : le texte est reconnu via le pipeline OCR local
(``HybridDocumentParser``), la photo est enregistrée avec un statut
``pending``, puis confirmée par l'utilisateur avant d'être intégrée comme
source de la conversation (FR-031 gate de confirmation, FR-032 intégration
de source).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ConversationPhoto
from .store import LibraryStore


class ConversationPhotoService:
    """Import and confirm a photo into a tutoring conversation (US7)."""

    def __init__(self, store: LibraryStore, document_parser: Any | None = None) -> None:
        self.store = store
        self.document_parser = document_parser

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    async def import_photo(self, conversation_id: str, source_path: str) -> dict[str, Any]:
        """Recognize a photo and register it as pending (FR-031)."""
        photo = ConversationPhoto(
            id=uuid.uuid4().hex[:8],
            conversation_id=conversation_id,
            path=source_path,
            confirmation_status="pending",
        )
        try:
            if self.document_parser is not None:
                parsed = await self.document_parser.parse(Path(source_path))
                pages = parsed.get("pages", [])
                photo.recognized_text = "\n".join(p.get("text", "") for p in pages)
            else:
                photo.recognized_text = Path(source_path).read_text(
                    encoding="utf-8", errors="ignore")
        except Exception as e:  # fail-closed: keep pending, surface error
            photo.recognized_text = f"[erreur OCR] {e}"
        self.store.create_conversation_photo(photo)
        return photo.to_dict()

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    def confirm(self, photo_id: str) -> dict[str, Any]:
        """Confirm a photo and record its source linkage (FR-031/FR-032)."""
        photo = self.store.get_conversation_photo(photo_id)
        if photo is None:
            raise KeyError(f"Unknown conversation photo: {photo_id}")
        self.store.confirm_conversation_photo(photo_id)
        photo.confirmation_status = "confirmed"
        # Record the source linkage on the photo row (FR-032).
        if photo.path:
            photo.source_linkage = photo.path
            self.store.update_conversation_photo_source(photo_id, photo.path)
        return photo.to_dict()

    def get(self, photo_id: str) -> dict[str, Any]:
        photo = self.store.get_conversation_photo(photo_id)
        if photo is None:
            raise KeyError(f"Unknown conversation photo: {photo_id}")
        return photo.to_dict()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
