"""Multi-utilisateur familial (Feature 008, US9).

``LearnerService`` gère les profils des membres de la famille : création,
sélection/bascule et suppression avec cascade. Chaque apprenant possède ses
propres matières, graphes, parcours, progression, conversations et carnet —
toutes les données restent locales (FR-037/FR-038/FR-039). L'isolation est
assurée par le ``learner_id`` porté par chaque ligne.
"""

from __future__ import annotations

from typing import Any

from .models import LearnerProfile
from .store import LibraryStore


class LearnerService:
    """Create/select/switch learners with data isolation (US9)."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, name: str, avatar: str = "") -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Learner name must be non-empty")
        learner = self.store.create_learner(name, avatar=avatar)
        return learner.to_dict()

    def list(self) -> dict[str, Any]:
        return {"learners": [l.to_dict() for l in self.store.list_learners()]}

    def get(self, learner_id: str) -> dict[str, Any]:
        learner = self.store.get_learner(learner_id)
        if learner is None:
            raise KeyError(f"Unknown learner: {learner_id}")
        return learner.to_dict()

    def delete(self, learner_id: str) -> dict[str, Any]:
        """Delete a learner and cascade its data (FR-039 edge case)."""
        learner = self.store.get_learner(learner_id)
        if learner is None:
            raise KeyError(f"Unknown learner: {learner_id}")
        self.store.delete_learner(learner_id)
        return {"deleted": learner_id}

    # ------------------------------------------------------------------
    # Activation / isolation
    # ------------------------------------------------------------------

    def activate(self, learner_id: str) -> dict[str, Any]:
        """Select a learner; returns its scoped subjects (FR-039)."""
        learner = self.store.get_learner(learner_id)
        if learner is None:
            raise KeyError(f"Unknown learner: {learner_id}")
        subjects = self.store.list_subjects(learner_id=learner_id)
        return {
            "learner": learner.to_dict(),
            "subjects": [s.to_dict() for s in subjects],
        }
