"""Conversations nommées (005-platform-ui-library).

Service de niveau domaine : encapsule la persistance des conversations
(sessions étendues ``tutoring_sessions``) et de leurs sources actives.
Aucune dépendance d'interface — le serveur web délègue ici.
"""

from __future__ import annotations

from typing import Any

from .store import LibraryStore


class ConversationService:
    """Façade mince au-dessus de ``LibraryStore`` pour les conversations."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store

    def create(self, subject_id: str, title: str = "") -> Any:
        """Crée une conversation vierge rattachée à un espace."""
        return self.store.create_tutoring_session(subject_id, title=title)

    def list(self) -> list[dict[str, Any]]:
        """Toutes les conversations, la plus récemment active d'abord."""
        return self.store.list_conversations()

    def rename(self, conversation_id: str, title: str) -> None:
        """Renomme une conversation ; KeyError si inconnue."""
        if not self.store.rename_conversation(conversation_id, title):
            raise KeyError(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        """Supprime une conversation (messages + sources actives)."""
        return self.store.delete_conversation(conversation_id)

    def sources(self, conversation_id: str) -> list[str]:
        """Identifiants des sources actives ([] si aucune/inconnue)."""
        return self.store.get_conversation_source_ids(conversation_id)

    def set_sources(self, conversation_id: str, book_ids: list[str]) -> int:
        """Remplace les sources actives ; KeyError si conversation inconnue."""
        if self.store.get_tutoring_session(conversation_id) is None:
            raise KeyError(conversation_id)
        return self.store.set_conversation_sources(conversation_id, book_ids)
