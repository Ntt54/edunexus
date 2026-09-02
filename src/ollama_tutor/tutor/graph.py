"""Graphe de compétences (Feature 008, US2).

Construit un graphe de compétences à partir des livres importés d'une matière,
principalement par **règles déterministes** (tables des matières / titres de
chapitres et sections, ordre d'apparition, fusion par identifiant conceptuel,
détection de prérequis par ordre/occurrence). Le LLM ne propose que des
**candidats** (concepts/relations) marqués ``ai_proposed``, soumis à validation
par l'utilisateur (FR-007..FR-010).

Ce module est UI-agnostique (principe I) : il ne dépend que de ``store`` et
``models``, jamais de fastapi/textual.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from .models import CompetencyNode, GraphEdge, SourceReference
from .store import LibraryStore

# Relations autorisées (FR-007).
RELATIONS = ("requires", "supports", "covered_by", "contrasts_with")

# Mots-clés indiquant une notion « prérequis » dans un titre de section.
_PREREQ_HINTS = (
    "introduction", "bases", "fondamentaux", "prérequis", "prerequis",
    "notions préliminaires", "rappel", "définitions", "définition",
    "concepts de base", "généralités", "principes",
)

# Mots-clés indiquant une notion « avancée » (dépend de ce qui précède).
_ADVANCED_HINTS = (
    "avancé", "avancée", "approfondi", "approfondissement", "perfectionnement",
    "cas particuliers", "applications", "synthèse", "conclusion",
)


def _slugify(text: str) -> str:
    """Identifiant conceptuel stable (fusion par identifiant conceptuel)."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s or "notion"


class GraphBuilder:
    """Construit / rafraîchit le graphe de compétences d'une matière."""

    def __init__(self, store: LibraryStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Construction déterministe
    # ------------------------------------------------------------------
    def build(self, subject_id: str) -> dict[str, Any]:
        """Construit le graphe depuis les livres importés (FR-007..FR-009).

        Retourne ``{"nodes": N, "edges": M, "ai_proposed": K}``.
        """
        chunks = self._store.get_indexed_chunks(subject_id)
        books = self._store.list_books(subject_id)
        concepts = self._store.list_concepts(subject_id)
        concept_by_name = {c.name.lower(): c for c in concepts}

        # 1) Collecter les « notions » depuis les titres de chapitres/sections.
        seen: dict[str, CompetencyNode] = {}
        order: list[str] = []
        for chunk in chunks:
            title = (chunk.get("chapter") or chunk.get("section") or "").strip()
            if not title:
                continue
            key = _slugify(title)
            if key in seen:
                # Fusion par identifiant conceptuel : on consolide la source.
                node = seen[key]
                node.sources.append(self._source_from_chunk(chunk, title))
                continue
            node = CompetencyNode(
                id="n_" + uuid.uuid4().hex[:12],
                subject_id=subject_id,
                concept_id=self._match_concept(title, concept_by_name),
                title=title,
                mastery_score=0.0,
                confidence=0.8,
                validation_status="extracted",
                sources=[self._source_from_chunk(chunk, title)],
            )
            seen[key] = node
            order.append(key)

        # 2) Détection de prérequis par ordre d'apparition (déterministe).
        edges: list[GraphEdge] = []
        for i, key in enumerate(order):
            node = seen[key]
            # Prérequis : les notions précédentes « de base ».
            for j in range(i):
                prev = seen[order[j]]
                if self._is_prerequisite(prev.title, node.title):
                    edges.append(self._edge(subject_id, prev, node, "requires"))
            # Notions avancées : dépendent de la notion précédente.
            if i > 0 and self._is_advanced(node.title):
                prev = seen[order[i - 1]]
                edges.append(self._edge(subject_id, prev, node, "requires"))

        # 3) Persistance idempotente.
        self._store.replace_competency_graph(subject_id, list(seen.values()), edges)

        ai_proposed = sum(1 for n in seen.values() if n.validation_status == "ai_proposed")
        return {"nodes": len(seen), "edges": len(edges), "ai_proposed": ai_proposed}

    # ------------------------------------------------------------------
    # Helpers déterministes
    # ------------------------------------------------------------------
    @staticmethod
    def _source_from_chunk(chunk: dict[str, Any], title: str) -> SourceReference:
        return SourceReference(
            book_id=str(chunk.get("book_id", "")),
            chapter=str(chunk.get("chapter", "") or title),
            page=chunk.get("page"),
            excerpt=(chunk.get("text") or "")[:200],
            confidence=0.8,
        )

    @staticmethod
    def _match_concept(title: str, concept_by_name: dict[str, Any]) -> str:
        """Fusion par identifiant conceptuel : lie le nœud à un concept existant."""
        t = title.lower().strip()
        if t in concept_by_name:
            return concept_by_name[t].id
        # Correspondance partielle (le titre contient le nom du concept).
        for name, concept in concept_by_name.items():
            if name and (name in t or t in name):
                return concept.id
        return ""

    @staticmethod
    def _is_prerequisite(prev_title: str, node_title: str) -> bool:
        """Une notion « de base » est un prérequis des notions suivantes."""
        p = prev_title.lower()
        return any(h in p for h in _PREREQ_HINTS)

    @staticmethod
    def _is_advanced(title: str) -> bool:
        t = title.lower()
        return any(h in t for h in _ADVANCED_HINTS)

    @staticmethod
    def _edge(subject_id: str, source: CompetencyNode,
              target: CompetencyNode, relation: str) -> GraphEdge:
        return GraphEdge(
            id="e_" + uuid.uuid4().hex[:12],
            subject_id=subject_id,
            source_node_id=source.id,
            target_node_id=target.id,
            relation=relation,
            confidence=0.7,
            validation_status="extracted",
        )

    # ------------------------------------------------------------------
    # Lecture / validation
    # ------------------------------------------------------------------
    def get_graph(self, subject_id: str) -> dict[str, Any]:
        nodes, edges = self._store.get_competency_graph(subject_id)
        return {
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in edges],
        }

    def validate_node(self, node_id: str) -> dict[str, Any]:
        """Marque un nœud comme confirmé par l'utilisateur (FR-010)."""
        self._store.validate_competency_node(node_id)
        return {"node_id": node_id, "validation_status": "user_confirmed"}

    # ------------------------------------------------------------------
    # Agrégation tableau de bord (US5, FR-020..FR-022)
    # ------------------------------------------------------------------
    def dashboard(self, subject_id: str) -> dict[str, Any]:
        """Agrège l'état du graphe : couvert / non couvert / contradictoire /
        non confirmé, avec provenance (FR-020/FR-021/FR-022)."""
        nodes, edges = self._store.get_competency_graph(subject_id)
        covered = []
        uncovered = []
        contradictory = []
        unconfirmed = []
        for n in nodes:
            item = {
                "id": n.id,
                "title": n.title,
                "mastery_score": n.mastery_score,
                "confidence": n.confidence,
                "validation_status": n.validation_status,
                "sources": [s.to_dict() for s in n.sources],
            }
            if n.validation_status == "ai_proposed":
                unconfirmed.append(item)
            elif n.mastery_score >= 0.7:
                covered.append(item)
            else:
                uncovered.append(item)
        # Contradictions : arêtes « contrasts_with » entre nœuds couverts.
        covered_ids = {n["id"] for n in covered}
        for e in edges:
            if e.relation == "contrasts_with" and e.source_node_id in covered_ids:
                contradictory.append({
                    "source": e.source_node_id,
                    "target": e.target_node_id,
                    "relation": e.relation,
                })
        return {
            "covered": covered,
            "uncovered": uncovered,
            "contradictory": contradictory,
            "unconfirmed": unconfirmed,
        }
