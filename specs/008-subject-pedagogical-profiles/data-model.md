# Data Model — EduNexus adaptatif (Feature 008)

**Phase 1 output** — Modèle de données pour les 15 entités de la spec, mappé sur l'infrastructure SQLite existante (`LibraryStore`).

## Vue d'ensemble

La feature étend le schéma SQLite existant de `LibraryStore` avec de nouvelles tables. Les entités existantes (Subject, Book, Chunk, Concept, Exercise, ExerciseAttempt, LearningPath, PathStep, Conversation) sont réutilisées et, pour certaines, étendues. Toutes les nouvelles tables portent une colonne `learner_id` (nullable) pour l'isolation multi-utilisateur familial.

## Entités existantes réutilisées

| Entité | Table | Rôle dans la feature |
|--------|-------|----------------------|
| `Subject` | `subjects` | Matière ; reçoit un `SubjectProfile` (1:1) |
| `Book` / `Chunk` | `books` / `chunks` | Sources du graphe et du carnet |
| `Concept` | `concepts` | Base du nœud de compétence (maîtrise, path_rank) |
| `Exercise` / `ExerciseAttempt` | `exercises` / `attempts` | Preuves de maîtrise |
| `LearningPath` / `PathStep` | `learning_paths` / `path_steps` | Parcours (étendu pour l'explicabilité) |
| `Conversation` | `conversations` | Support de l'import de photo |

## Nouvelles entités

### 1. `LearnerProfile` (Profil d'apprenant) — multi-utilisateur familial

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `name` | str | Requis, non vide |
| `avatar` | str | Optionnel (emoji/couleur) |
| `created_at` / `updated_at` | str (ISO) | Auto |

**Relations**: 1:N vers toutes les tables métier via `learner_id`. Un profil actif est sélectionné au démarrage ; la bascule change le contexte.

**Validation**: `name` non vide (FR-038). Suppression d'un profil ⇒ suppression en cascade de ses matières/graphes/parcours/progression/conversations/carnet (edge case).

### 2. `SubjectProfile` (Profil de matière)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `subject_id` | str | PK/FK → subjects |
| `domain` | str | Requis |
| `level` | str | primaire → supérieur |
| `objective` | str | Requis (examen, projet, remise à niveau…) |
| `deadline` | str (ISO) | Optionnel |
| `available_time` | str | Optionnel |
| `prerequisites` | list[str] | Optionnel |
| `competencies` | list[str] | Optionnel |
| `explanation_style` | str | Optionnel |
| `activities` | list[str] | Prérempli par template |
| `mastery_criteria` | list[str] | Prérempli par template |
| `constraints` | dict | Optionnel |
| `template_id` | str | FK → pedagogical_templates |

**Validation**: FR-001 (champs requis), FR-005 (persistance/restauration), FR-006 (le template n'est pas une contrainte — modifiable).

### 3. `PedagogicalTemplate` (Modèle pédagogique)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str | PK |
| `name` | str | Requis (Programmation, Mathématiques, Sciences expérimentales, SVT, Matière scolaire générale, Langue, Profil libre) |
| `activities` | list[str] | Prérempli |
| `proof_types` | list[str] | Prérempli |
| `default_style` | str | Optionnel |

**Validation**: FR-003 (au moins 7 modèles), FR-004 (conversion objectif→paramètres).

### 4. `CompetencyNode` (Nœud de compétence)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `subject_id` | str | FK → subjects |
| `concept_id` | str | FK → concepts (fusion par identifiant conceptuel) |
| `title` | str | Requis |
| `mastery_score` | float | 0–100, continue (réutilise ProgressTracker) |
| `confidence` | float | 0–1 |
| `validation_status` | str | `extracted` \| `ai_proposed` \| `user_confirmed` |
| `sources` | list[SourceReference] | Références livre/chapitre/page |

**Validation**: FR-007 (nœud = notion/compétence), FR-008 (fusion par identifiant), FR-009 (score de maîtrise), FR-010 (source + confiance + statut).

### 5. `GraphEdge` (Arête du graphe)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `subject_id` | str | FK |
| `source_node_id` | str | FK → competency_nodes |
| `target_node_id` | str | FK → competency_nodes |
| `relation` | str | `requires` \| `supports` \| `covered_by` \| `contrasts_with` |
| `confidence` | float | 0–1 |
| `validation_status` | str | `extracted` \| `ai_proposed` \| `user_confirmed` |

**Validation**: FR-007 (types d'arêtes), FR-010 (confiance + statut).

### 6. `LearningPath` / `PathStep` (étendus)

`PathStep` existant est étendu avec les champs d'explicabilité :

| Champ | Type | Contraintes |
|-------|------|-------------|
| `why_now` | str | Requis (FR-013) |
| `prerequisites` | list[str] | Requis |
| `sources` | list[SourceReference] | Requis |
| `planned_activity` | str | Requis |
| `expected_proof` | str | Requis |

**Validation**: FR-012 (sélection + tri topologique), FR-013 (affichage des 5 champs), FR-014 (déplacer/fusionner/exclure + persistance), FR-015 (pas d'invention LLM).

### 7. `CapturedProgram` (Programme capturé)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `subject_id` | str | FK |
| `source_type` | str | `photo` \| `pdf` |
| `status` | str | `processing` \| `ready` \| `error` |
| `recognized_text` | str | Texte OCR |
| `validation_status` | str | `pending` \| `confirmed` |

**Validation**: FR-023 (pipeline), FR-024 (local), FR-027 (correction avant génération).

### 8. `ProgramNode` (Nœud de programme)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `program_id` | str | FK → captured_programs |
| `parent_id` | str | FK → program_nodes (arbre) |
| `title` | str | Requis |
| `kind` | str | `chapter` \| `sub_part` \| `competency` |
| `origin` | str | `ocr` \| `book` \| `ai_generated` |
| `validation_status` | str | `pending` \| `confirmed` \| `corrected` |

**Validation**: FR-023 (structuration en arbre éditable), FR-025 (passages incertains signalés), FR-026 (distinction visuelle des 3 catégories), FR-028 (table des matières = indice, pas vérité).

### 9. `CaptureImage` (Image de capture)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `program_id` | str | FK |
| `path` | str | Chemin local de l'image retenue |
| `preprocess_state` | str | `raw` \| `deskewed` \| `cropped` \| `contrasted` |

**Validation**: FR-023 (prétraitement), FR-024 (images locales).

### 10. `ConversationPhoto` (Photo de conversation)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `conversation_id` | str | FK → conversations |
| `path` | str | Chemin local |
| `recognized_text` | str | Texte OCR |
| `confirmation_status` | str | `pending` \| `confirmed` |
| `source_linkage` | str | Lien vers la source dans la conversation |

**Validation**: FR-029 (import + OCR + confirmation + source), FR-030 (distinction texte reconnu / génération IA), FR-031 (passages incertains signalés).

### 11. `SubjectNotebook` (Carnet de matière)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `subject_id` | str | FK (1:1) |
| `notes` | list[str] | Notes personnelles |
| `created_at` / `updated_at` | str | Auto |

**Validation**: FR-032 (regroupe livres, programme, objectifs, niveau, compétences, notes, parcours).

### 12. `NotebookOutput` (Sortie de carnet)

| Champ | Type | Contraintes |
|-------|------|-------------|
| `id` | str (uuid) | PK |
| `notebook_id` | str | FK → subject_notebooks |
| `kind` | str | `summary` \| `study_sheet` \| `quiz` \| `comparison` \| `explanation` |
| `content` | str | Contenu généré |
| `sources` | list[SourceReference] | Liens vers les sources |
| `created_at` | str | Auto |

**Validation**: FR-033 (actions), FR-034 (sorties dans le contexte du carnet, sources visibles), FR-035 (liées aux sources + supprimables), FR-036 (provenance, pas de confiance automatique).

## Transitions d'état

- **CapturedProgram**: `processing` → `ready` → (validation) → `confirmed` ; `processing` → `error` (échec OCR).
- **ProgramNode**: `pending` → `confirmed` | `corrected` (correction utilisateur avant contrainte du parcours).
- **ConversationPhoto**: `pending` → `confirmed` (avant utilisation par le tuteur).
- **CompetencyNode.validation_status**: `extracted` → `user_confirmed` ; `ai_proposed` → `user_confirmed` (après validation).
- **PathStep.status**: `pending` → `in_progress` → `completed` (existant, réutilisé).

## Règles de validation transverses

- **Isolation multi-utilisateur**: toute requête filtre par `learner_id` du profil actif (FR-037/FR-038/FR-039).
- **Anti-hallucination**: tout élément `ai_proposed` doit être soumis à validation avant de contraindre le parcours (FR-010, FR-015, FR-025, FR-031, FR-036).
- **Maîtrise**: score continu 0–100 par nœud via `ProgressTracker` ; « maîtrisé » seulement après ≥2 preuves de types différents (FR-017, SC-006).
- **Adaptation**: recalcul par fenêtre locale, jamais complet (FR-016, SC-005) ; portion de stabilité conservée (FR-019).
