"""Adaptation locale du parcours (Feature 008, US4).

``AdaptationService`` met à jour le profil de maîtrise après chaque activité
et ne recalcule qu'une **fenêtre** de quelques étapes du parcours (FR-016),
valide une compétence seulement après **plusieurs preuves différentes**
(FR-017), enregistre réponse/temps/indices/source (FR-018) et conserve une
**portion de stabilité** (objectifs, notion principale, critère) (FR-019).
"""

from __future__ import annotations

from typing import Any

from .models import ExerciseAttempt
from .store import LibraryStore

# Nombre de preuves différentes requises avant de considérer une compétence
# comme maîtrisée (FR-017).
MIN_DISTINCT_PROOFS = 3
# Taille de la fenêtre de recalcul (FR-016) : quelques étapes, pas tout.
WINDOW_SIZE = 3


def build_interleaved_session(
    size: int,
    n_concepts: int,
    kinds: list[str],
    failed: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Plan de séance entremêlée (012 US1, FR-003 ; recherche D3).

    Retourne ``[(concept_idx, kind_idx), ...]`` : les types sont mélangés
    (jamais deux questions adjacentes du même type quand k > 1 — avec un
    seul type, k == 1, la non-adjacence est impossible par définition), les
    concepts sont cyclés, et les ``failed`` (paires ``(concept_idx,
    kind_idx)`` ratées précédemment, plafonnées à ``size``) refont surface
    en fin de séance (pivotées si elles recréeraient un bloc mono-type).

    Pur et déterministe (aucun appel LLM) — testable offline.
    """
    n = max(1, int(size))
    n_concepts = max(1, int(n_concepts))
    kinds = list(kinds or ["open"])
    k = len(kinds)
    plan: list[tuple[int, int]] = []
    for i in range(n):
        if k < 3:
            # 1-2 types : simple alternance (jamais deux adjacents identiques
            # quand k > 1 ; k == 1 : un seul type, non-adjacence impossible).
            kind_idx = i % k
        else:
            # kind_idx = (i + i // k) % k : la retenue de la division
            # garantit deux types adjacents toujours distincts (k ≥ 3).
            kind_idx = (i + i // k) % k
        plan.append((i % n_concepts, kind_idx))
    # Ratés en fin de séance : plafonnés à n pour que size reste une taille
    # (bonus borné, pas de croissance illimitée) ; pivot si le type raté
    # égale le type de queue (k > 1) pour préserver la non-adjacence.
    for pair in list(failed or [])[:n]:
        ci, ki = pair
        ci = int(ci) % n_concepts
        ki = int(ki) % k
        if k > 1 and plan and ki == plan[-1][1]:
            ki = (ki + 1) % k
        plan.append((ci, ki))
    return plan


class AdaptationService:
    """Local, windowed adaptation of the learning path."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # FR-018 : enregistrer réponse/temps/indices/source
    # ------------------------------------------------------------------

    def record_attempt(self, exercise_id: str, *, verdict: str, answer: str = "",
                       feedback: str = "", time_ms: int = 0, hints_used: int = 0,
                       source: str = "") -> ExerciseAttempt:
        """Record an attempt with answer/time/hints/source (FR-018)."""
        attempt = ExerciseAttempt(
            id="", exercise_id=exercise_id, verdict=verdict, answer=answer,
            feedback=feedback, time_ms=time_ms, hints_used=hints_used, source=source,
        )
        return self.store.add_attempt(attempt)

    # ------------------------------------------------------------------
    # FR-017 : validation multi-preuves
    # ------------------------------------------------------------------

    def is_mastered(self, concept_id: str) -> bool:
        """A competency is mastered only after several distinct proofs (FR-017).

        Distinct proofs = distinct exercise sources (e.g. recall, guided
        exercise, transfer problem) with a correct verdict.
        """
        attempts = self.store.list_attempts_by_concept(concept_id)
        correct_sources = {a.source for a in attempts if a.verdict == "correct" and a.source}
        return len(correct_sources) >= MIN_DISTINCT_PROOFS

    def mastery_progress(self, concept_id: str) -> dict[str, Any]:
        """Return proof progress for a concept (for UI)."""
        attempts = self.store.list_attempts_by_concept(concept_id)
        correct_sources = {a.source for a in attempts if a.verdict == "correct" and a.source}
        return {
            "distinct_proofs": len(correct_sources),
            "required": MIN_DISTINCT_PROOFS,
            "mastered": len(correct_sources) >= MIN_DISTINCT_PROOFS,
            "sources": sorted(correct_sources),
        }

    # ------------------------------------------------------------------
    # FR-016 : recalcul d'une fenêtre
    # ------------------------------------------------------------------

    def recompute_window(self, subject_id: str, anchor_step_id: str | None = None) -> dict[str, Any]:
        """Recompute only a window of path steps around the anchor (FR-016).

        Returns the updated path payload plus the window bounds. The window
        is a few steps around the anchor (or the first steps if no anchor).
        """
        paths = self.store.list_learning_paths(subject_id)
        if not paths:
            return {"path": {"id": "", "steps": []}, "window": []}
        path = paths[0]
        steps = self.store.list_path_steps(path.id)
        if not steps:
            return {"path": {"id": "", "steps": []}, "window": []}

        # Locate the anchor index.
        anchor_idx = 0
        if anchor_step_id:
            for i, s in enumerate(steps):
                if s.id == anchor_step_id:
                    anchor_idx = i
                    break
        start = max(0, anchor_idx - 1)
        end = min(len(steps), anchor_idx + WINDOW_SIZE)
        window_ids = [s.id for s in steps[start:end]]

        # Recompute mastery for each window step's concept and refresh
        # why_now/planned_activity based on current mastery.
        for s in steps[start:end]:
            if s.activity_type != "concept" or not s.activity_id:
                continue
            progress = self.mastery_progress(s.activity_id)
            if progress["mastered"]:
                self.store.update_path_step(s.id, status="completed")
            else:
                self.store.update_path_step(
                    s.id,
                    why_now=self._why_now_for(s.title, progress),
                    planned_activity=self._activity_for(s.title, progress),
                )

        return {
            "path": self._path_payload(path.id),
            "window": window_ids,
        }

    # ------------------------------------------------------------------
    # FR-019 : portion de stabilité
    # ------------------------------------------------------------------

    def stability_portion(self, subject_id: str) -> dict[str, Any]:
        """Return the stability portion: objectives, main notion, criterion (FR-019)."""
        profile = self.store.get_subject_profile(subject_id)
        paths = self.store.list_learning_paths(subject_id)
        steps = self.store.list_path_steps(paths[0].id) if paths else []
        main_notion = steps[0].title if steps else ""
        return {
            "objective": profile.objective if profile else "",
            "main_notion": main_notion,
            "success_criterion": (profile.mastery_criteria[0]
                                  if profile and profile.mastery_criteria else ""),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _path_payload(self, path_id: str) -> dict[str, Any]:
        path = self.store.get_learning_path(path_id)
        steps = self.store.list_path_steps(path_id)
        return {
            "id": path.id if path else "",
            "steps": [s.to_dict() for s in steps],
        }

    def _why_now_for(self, title: str, progress: dict[str, Any]) -> str:
        if progress["mastered"]:
            return f"« {title} » est maîtrisé : passez à la suite."
        return (f"« {title} » : {progress['distinct_proofs']}/{progress['required']} "
                f"preuves distinctes obtenues — continuez à vous entraîner.")

    def _activity_for(self, title: str, progress: dict[str, Any]) -> str:
        if progress["mastered"]:
            return f"Réviser « {title} » puis passer à la notion suivante."
        return f"Travailler « {title} » avec une nouvelle activité (variante)."
