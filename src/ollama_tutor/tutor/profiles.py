"""Pedagogical subject profiles (Feature 008, US1).

UI-agnostic service: no fastapi/textual imports (constitution principle I).
Provides template prefill, goal interpretation, and profile validation.
"""

from __future__ import annotations

from typing import Any

from .models import PedagogicalTemplate, SubjectProfile
from .store import LibraryStore


class ProfileService:
    """Service for pedagogical subject profiles (US1)."""

    def __init__(self, store: LibraryStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def list_templates(self) -> list[PedagogicalTemplate]:
        """Return the predefined pedagogical templates (FR-003)."""
        self._store.seed_pedagogical_templates()
        return self._store.list_pedagogical_templates()

    def get_template(self, template_id: str) -> PedagogicalTemplate | None:
        for t in self.list_templates():
            if t.id == template_id:
                return t
        return None

    # ------------------------------------------------------------------
    # Profile CRUD
    # ------------------------------------------------------------------

    def get_profile(self, subject_id: str) -> SubjectProfile | None:
        return self._store.get_subject_profile(subject_id)

    def save_profile(self, profile: SubjectProfile) -> SubjectProfile:
        """Validate and persist a subject profile (FR-001/FR-005)."""
        if not profile.domain:
            raise ValueError("domain is required")
        if not profile.objective:
            raise ValueError("objective is required")
        self._store.set_subject_profile(profile)
        return profile

    def create_from_template(self, subject_id: str, template_id: str,
                             domain: str = "", objective: str = "") -> SubjectProfile:
        """Create a profile prefilled from a template (FR-003)."""
        template = self.get_template(template_id)
        profile = SubjectProfile(subject_id=subject_id, template_id=template_id)
        if template is not None:
            profile.activities = list(template.activities)
            profile.mastery_criteria = list(template.proof_types)
            profile.explanation_style = template.default_style
        if domain:
            profile.domain = domain
        if objective:
            profile.objective = objective
        self._store.set_subject_profile(profile)
        return profile

    # ------------------------------------------------------------------
    # Goal interpretation (FR-004)
    # ------------------------------------------------------------------

    def interpret_goal(self, goal: str) -> dict[str, Any]:
        """Convert a plain-language goal into internal pedagogical parameters.

        Deterministic keyword-based mapping so it works offline and is
        testable without an LLM (FR-004).
        """
        g = goal.lower()
        params: dict[str, Any] = {
            "approach": "general",
            "debugging": "medium",
            "practice": "medium",
            "progression": "prereq",
        }
        if any(k in g for k in ("projet", "créer", "construire", "build", "app")):
            params["approach"] = "project"
            params["practice"] = "high"
        if any(k in g for k in ("examen", "exam", "concours", "test", "certif")):
            params["approach"] = "exam"
            params["practice"] = "high"
        if any(k in g for k in ("remise à niveau", "revoir", "réviser", "revision", "base")):
            params["approach"] = "remedial"
            params["progression"] = "foundations_first"
        if any(k in g for k in ("déboguer", "debug", "corriger", "bug")):
            params["debugging"] = "high"
        if any(k in g for k in ("parler", "converser", "oral", "langue")):
            params["approach"] = "conversational"
        return params
