"""Parcours d'apprentissage explicable (Feature 008, US3).

``PathBuilder`` construit un parcours ordonné à partir du graphe de
compétences et du profil pédagogique : sélection des notions à couvrir,
tri topologique respectant les prérequis, et enrichissement de chaque
étape avec les champs d'explicabilité (FR-013) : ``why_now``,
``prerequisites``, ``sources``, ``planned_activity``, ``expected_proof``.
"""

from __future__ import annotations

from typing import Any

from .models import (
    CompetencyNode,
    GraphEdge,
    LearningPath,
    PathStep,
    SourceReference,
    SubjectProfile,
)
from .store import LibraryStore


class PathBuilder:
    """Build an explainable learning path from graph + profile."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, subject_id: str, title: str = "Parcours adaptatif") -> dict[str, Any]:
        """Generate (or regenerate) the path for a subject.

        Returns the path payload with ordered, explainable steps.
        """
        nodes, edges = self.store.get_competency_graph(subject_id)
        profile = self.store.get_subject_profile(subject_id)
        if not nodes:
            return {"path": {"id": "", "steps": []}}

        ordered = self._topological_order(nodes, edges)
        path = self._persist(subject_id, title, ordered, profile)
        return {"path": path}

    def reorder(self, subject_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Reorder / exclude steps (FR-014)."""
        paths = self.store.list_learning_paths(subject_id)
        if not paths:
            return {"path": {"id": "", "steps": []}}
        path = paths[0]
        by_id = {s.id: s for s in self.store.list_path_steps(path.id)}
        ordered_ids: list[str] = []
        for item in steps:
            sid = item.get("id")
            if sid not in by_id:
                continue
            if item.get("excluded"):
                self.store.delete_path_step(sid)
                continue
            ordered_ids.append(sid)
        self.store.reorder_path_steps(path.id, ordered_ids)
        return {"path": self._path_payload(path.id)}

    # ------------------------------------------------------------------
    # Graph ordering
    # ------------------------------------------------------------------

    def _topological_order(self, nodes: list[CompetencyNode],
                           edges: list[GraphEdge]) -> list[CompetencyNode]:
        """Kahn topological sort respecting prerequisite edges.

        Nodes with no incoming edges come first; ties broken by title for
        determinism. Cycles are tolerated (remaining nodes appended).
        """
        by_id = {n.id: n for n in nodes}
        indegree = {n.id: 0 for n in nodes}
        adj: dict[str, list[str]] = {n.id: [] for n in nodes}
        for e in edges:
            if e.source_node_id in by_id and e.target_node_id in by_id:
                adj[e.source_node_id].append(e.target_node_id)
                indegree[e.target_node_id] += 1

        ready = sorted((n.id for n in nodes if indegree[n.id] == 0),
                       key=lambda i: by_id[i].title.lower())
        result: list[CompetencyNode] = []
        while ready:
            nid = ready.pop(0)
            result.append(by_id[nid])
            for tgt in sorted(adj[nid], key=lambda i: by_id[i].title.lower()):
                indegree[tgt] -= 1
                if indegree[tgt] == 0:
                    ready.append(tgt)
                    ready.sort(key=lambda i: by_id[i].title.lower())
        # Append any nodes left over from a cycle.
        seen = {n.id for n in result}
        for n in nodes:
            if n.id not in seen:
                result.append(n)
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, subject_id: str, title: str,
                 ordered: list[CompetencyNode],
                 profile: SubjectProfile | None) -> dict[str, Any]:
        # Reuse the most recent path if present, else create one.
        paths = self.store.list_learning_paths(subject_id)
        if paths:
            path = paths[0]
            self.store.update_learning_path(path.id, title=title)
            for step in self.store.list_path_steps(path.id):
                self.store.delete_path_step(step.id)
        else:
            path = self.store.create_learning_path(subject_id, title)

        for idx, node in enumerate(ordered):
            prereq_titles = self._prerequisite_titles(node, ordered)
            self.store.add_path_step(
                path.id,
                activity_type="concept",
                activity_id=node.id,
                title=node.title,
                ordinal=idx,
                why_now=self._why_now(node, idx, profile),
                prerequisites=prereq_titles,
                sources=node.sources,
                planned_activity=self._planned_activity(node, profile),
                expected_proof=self._expected_proof(node, profile),
            )
        return self._path_payload(path.id)

    def _path_payload(self, path_id: str) -> dict[str, Any]:
        path = self.store.get_learning_path(path_id)
        steps = self.store.list_path_steps(path_id)
        return {
            "id": path.id if path else "",
            "steps": [s.to_dict() for s in steps],
        }

    # ------------------------------------------------------------------
    # Explicability helpers
    # ------------------------------------------------------------------

    def _prerequisite_titles(self, node: CompetencyNode,
                             ordered: list[CompetencyNode]) -> list[str]:
        """Titles of nodes that appear before ``node`` in the order."""
        titles: list[str] = []
        for other in ordered:
            if other.id == node.id:
                break
            titles.append(other.title)
        return titles

    def _why_now(self, node: CompetencyNode, idx: int,
                 profile: SubjectProfile | None) -> str:
        if idx == 0:
            return "Point de départ : aucune notion préalable requise."
        return (f"Étape {idx + 1} : s'appuie sur les notions précédentes "
                f"pour construire « {node.title} » progressivement.")

    def _planned_activity(self, node: CompetencyNode,
                          profile: SubjectProfile | None) -> str:
        style = (profile.explanation_style if profile and profile.explanation_style
                 else "explication + exercices")
        return f"Étudier « {node.title} » ({style}) puis vérifier la compréhension."

    def _expected_proof(self, node: CompetencyNode,
                        profile: SubjectProfile | None) -> str:
        criteria = (profile.mastery_criteria if profile and profile.mastery_criteria
                    else ["répondre sans aide à une question sur la notion"])
        return " ; ".join(criteria)
