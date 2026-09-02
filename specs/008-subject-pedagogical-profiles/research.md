# Research — EduNexus adaptatif (Feature 008)

**Phase 0 output** — Résout les choix techniques clés avant la conception détaillée.

## 1. Construction du graphe de compétences : règles déterministes vs LLM

**Decision**: Construire le graphe principalement par **règles déterministes** sur le texte extractible (tables des matières, titres de chapitres, sections, index, premiers passages) ; le LLM ne propose que des **candidats** (concepts/relations) soumis à validation.

**Rationale**: Le document exige un parcours « déterministe, traçable et explicable » (anti-hallucination). Le LLM seul produirait un programme non vérifiable. Les règles déterministes (fusion par identifiant conceptuel, détection de prérequis par ordre/occurrence, tri topologique) rendent le résultat reproductible et testable hors-ligne (principe III).

**Alternatives considered**:
- Graphe 100 % LLM : rejeté (non déterministe, non testable hors-ligne, risque d'hallucination).
- Graphe 100 % règles sans LLM : rejeté (perd la capacité à proposer des relations implicites) — le LLM reste utile en tant que proposition soumise à validation.

## 2. Génération du parcours : sélection + tri topologique

**Decision**: Générer le parcours en deux temps : (1) sélectionner les nœuds non maîtrisés dont les prérequis sont suffisamment couverts, (2) ordonner par **tri topologique** sur le graphe `requires`.

**Rationale**: Garantit que chaque étape a ses prérequis avant elle (SC-004, FR-012, FR-015). Le LLM n'invente pas le programme ; il ne fait que générer les activités/justifications des étapes.

**Alternatives considered**: Génération séquentielle par le LLM (rejeté — non vérifiable) ; ordre par table des matières seul (rejeté — la table n'est pas une progression pédagogique optimale, FR-028).

## 3. Adaptation après séance : fenêtre locale + preuves multiples

**Decision**: Après chaque activité, recalculer **seulement une fenêtre de quelques étapes** du parcours (pas une régénération complète), et ne valider une compétence « maîtrisée » qu'après **plusieurs preuves de types différents** (rappel, exercice guidé, problème de transfert).

**Rationale**: FR-016/FR-017, SC-005/SC-006. Évite la sur-adaptation instable et respecte le modèle de maîtrise continue par nœud existant (`ProgressTracker`, `D7_WEIGHTS`).

**Alternatives considered**: Régénération complète après chaque séance (rejeté — coûteux, instable) ; validation après une seule preuve (rejeté — trop laxiste, contredit le document).

## 4. Capture de programme par OCR : réutilisation du provider Granite-Docling

**Decision**: Réutiliser le pipeline OCR local existant (`tutor/providers/docling_ocr.py` + rasterisation `pdftoppm`) pour la capture photo/PDF, avec un pipeline structuré : capture → prétraitement (redressement, recadrage, contraste, dédoublonnage) → OCR → structuration en arbre éditable → rapprochement avec les livres/profil → validation → planification.

**Rationale**: FR-023/FR-024, SC-009. Aucune nouvelle dépendance (principe V) ; le provider OCR existe déjà et est testé (`test_docling_ocr.py`). Traitement **incrémental photo par photo** via une file d'attente avec statut visible (clarification Q2).

**Alternatives considered**: Nouveau service OCR cloud (rejeté — viole principe IV) ; traitement par lot sans file (rejeté — clarification Q2 impose l'incrémental).

## 5. Import de photo dans une conversation

**Decision**: Réutiliser le même pipeline OCR pour importer une photo dans une conversation de tutorat, afficher le texte reconnu pour confirmation, et l'intégrer comme source dans la conversation (FR-029/FR-030/FR-031, SC-011).

**Rationale**: Étend la capture photo au flux de tutorat quotidien (clarification Q1). Le texte reconnu est distingué de la génération IA et les passages incertains sont signalés.

**Alternatives considered**: Traitement par le LLM multimodal (rejeté — dépendance non justifiée, pas de garantie locale) ; import sans confirmation (rejeté — viole FR-031).

## 6. Carnet de matière (inspiré NotebookLM)

**Decision**: Implémenter le carnet comme un **espace local** regroupant livres, programme capturé, objectifs, niveau, compétences, notes et parcours, avec des actions RAG (résumer, comparer, fiche, questionner, expliquer, prérequis, parcours, vérifier) produisant des sorties liées aux sources et supprimables (FR-032..FR-036, SC-012).

**Rationale**: Réutilise l'infrastructure RAG/retrieval existante (`retrieval.py`, `reranker.py`) ; aucune dépendance cloud (principe IV/V).

**Alternatives considered**: Carnet cloud type NotebookLM (rejeté — viole principe IV) ; carnet sans lien aux sources (rejeté — viole FR-035/FR-036).

## 7. Multi-utilisateur familial

**Decision**: Ajouter des **profils d'apprenant locaux** (LearnerProfile) sur un seul PC, chacun possédant ses propres matières, graphes, parcours, progression, conversations et carnet. Création/sélection/bascule au démarrage, données isolées et locales (FR-037..FR-039, SC-013).

**Rationale**: Clarification Q3 — demande explicite de l'utilisateur. Pas de comptes, pas d'authentification distante, pas de concurrence multi-utilisateur (assumption). L'isolation se fait par une colonne `learner_id` sur les tables existantes + une table `learner_profiles`.

**Alternatives considered**: Comptes avec authentification (rejeté — hors périmètre, viole la simplicité) ; multi-utilisateur concurrent (rejeté — assumption, hors périmètre).

## 8. Persistance : extension de `LibraryStore` (SQLite)

**Decision**: Étendre `LibraryStore` avec de nouvelles tables (`subject_profiles`, `competency_graph`, `captured_programs`, `program_nodes`, `conversation_photos`, `notebooks`, `notebook_outputs`, `learner_profiles`) et des migrations idempotentes style PRAGMA-table_info existant.

**Rationale**: Réutilise le stockage SQLite existant (principe II/V) ; cohérent avec le style de migration déjà en place (`_migrate`).

**Alternatives considered**: Nouvelle base séparée (rejeté — complexité inutile) ; stockage JSON (rejeté — perd l'intégrité relationnelle du graphe).

## 9. Interface : endpoints REST/WS fins + UI tutor.html

**Decision**: Exposer des endpoints REST/WS fins dans `web/server.py` (profil, graphe, parcours, capture, carnet, profils) qui délèguent aux services `tutor/` ; UI dans `tutor.html` (fichier autonome CSS/JS inline, zéro asset externe).

**Rationale**: Principe I (couche de transport fine) et V (fichier autonome). Cohérent avec l'architecture existante.

**Alternatives considered**: Logique métier dans le serveur (rejeté — viole principe I) ; framework UI (rejeté — viole principe V).
