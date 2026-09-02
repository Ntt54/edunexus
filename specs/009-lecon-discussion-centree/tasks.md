# Tasks: Leçon cliquable — discussion centrée sur la notion

**Input**: Design documents from `/specs/009-lecon-discussion-centree/`
**Prerequisites**: plan.md (required), spec.md (required for user stories)
**Tests**: Tests hors-ligne obligatoires (Constitution III)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Arborescence binaires/modèles et préparation

- [ ] T001 Vérifier arborescence `vendor/llama.cpp/b10632/` et `models/gguf/` avec `.gitkeep` et entrée `.gitignore` pour `vendor/` et `models/`
- [ ] T002 Vérifier `data/config.json` pointe `tutor.llama_bin` vers `vendor/llama.cpp/b10632/llama-server` et `tutor.llama_models_dir` vers `models/gguf`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Modèles et persistance partagés — BLOQUE toutes les user stories

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 [P] Étendre `src/ollama_tutor/tutor/models.py` avec `LessonDiscussion`, `GeneratedLessonContent`, `LessonExerciseAttempt` (champs `learner_id`, `status`, `kind`, `sources`, `passed`)
- [ ] T004 Étendre `src/ollama_tutor/tutor/store.py` : créer tables `lesson_discussions`, `lesson_messages`, `generated_lesson_contents`, `lesson_exercise_attempts` + migration `path_steps.status` (`not_started`|`in_progress`|`completed`), méthodes CRUD et filtrage par `learner_id`
- [ ] T005 Vérifier `tests/contract/test_tutor_imports.py` passe (principe I : `tutor/` sans import `fastapi`/`textual`)

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Ouvrir une discussion centrée depuis une leçon (Priority: P1) 🎯 MVP

**Goal**: Chaque leçon du parcours est cliquable et ouvre une discussion isolée par notion/learner avec RAG filtré.

**Independent Test**: Générer un parcours "Variables", cliquer la leçon → discussion s'ouvre avec `path_step_id` visible, historique vide, envoyer "c'est quoi une variable ?" → réponse RAG citant sources de la notion uniquement.

### Tests for User Story 1

- [ ] T006 [P] [US1] Contract test `GET /api/tutor/lesson-discussions/{id}` et `POST /api/tutor/path-steps/{id}/discussion` dans `tests/contract/test_lesson_api.py`
- [ ] T007 [P] [US1] Unit test isolation par leçon et par `learner_id` dans `tests/unit/test_lesson_discussion.py`

### Implementation for User Story 1

- [ ] T008 [P] [US1] Créer `src/ollama_tutor/tutor/lesson_discussion.py` avec `LessonDiscussionService.get_or_create_discussion(path_step_id, learner_id)` et `ask_notion()` (RAG filtré par `concept_id`/`title`)
- [ ] T009 [US1] Implémenter endpoints `POST /api/tutor/path-steps/{step_id}/discussion` et `GET /api/tutor/lesson-discussions/{id}` dans `src/ollama_tutor/web/server.py` (transport fin, filtrage `learner_id`)
- [ ] T010 [US1] Rendre les leçons cliquables dans `src/ollama_tutor/web/static/tutor.html` (parcours → clic → `navigate("lecon/:id")`, passage `not_started`→`in_progress`)
- [ ] T011 [US1] Créer vue dédiée `view-lecon` dans `src/ollama_tutor/web/static/tutor.html` (plein écran, bouton retour, historique messages, input chat, `loadLessonDiscussion()`)

**Checkpoint**: US1 fully functional — discussion isolée ouvrable et filtrable par notion

---

## Phase 4: User Story 2 - Générer un cours ou une synthèse (Priority: P1)

**Goal**: Depuis la discussion, boutons "Générer le cours" (RAG 800–1200 mots) et "Faire une synthèse" (150–250 mots, indépendante si cours absent).

**Independent Test**: Dans discussion "Variables", cliquer "Générer le cours" → contenu `lesson_course` avec sources ; cliquer "Faire une synthèse" sans cours préalable → synthèse depuis sources directes.

### Tests for User Story 2

- [ ] T012 [P] [US2] Unit test génération cours et synthèse (indépendante + dérivée) dans `tests/unit/test_lesson_discussion.py`
- [ ] T013 [P] [US2] Contract test `POST /api/tutor/lesson-discussions/{id}/generate-course` et `/generate-summary` dans `tests/contract/test_lesson_api.py`

### Implementation for User Story 2

- [ ] T014 [US2] Implémenter `generate_course()` et `generate_summary()` dans `src/ollama_tutor/tutor/lesson_discussion.py` (RAG via `get_indexed_chunks` filtré, `TutorService`, persistance `GeneratedLessonContent`)
- [ ] T015 [US2] Ajouter endpoints `POST /api/tutor/lesson-discussions/{id}/generate-course` et `POST .../generate-summary` dans `src/ollama_tutor/web/server.py`
- [ ] T016 [US2] Ajouter boutons et rendu dans `src/ollama_tutor/web/static/tutor.html` (view-lecon : `generateCourse()`, `generateSummary()`, affichage markdown + sources)

**Checkpoint**: US2 functional — cours et synthèse générables et persistants

---

## Phase 5: User Story 3 - Faire des exercices et clôturer la leçon (Priority: P1)

**Goal**: Bouton "Faire des exercices" → 3–5 exercices ciblés (régénérés à chaque tentative) → évaluation → `completed` auto si ≥60% sinon manuel.

**Independent Test**: Dans "Variables", lancer exercices, répondre 2/5 → reste `in_progress` + bouton "Marquer comme terminé quand même" ; répondre 4/5 → passe `completed` et parcours mis à jour.

### Tests for User Story 3

- [ ] T017 [P] [US3] Unit test génération exercices, scoring ≥60% et passage statut dans `tests/unit/test_lesson_discussion.py`
- [ ] T018 [P] [US3] Contract test `POST /api/tutor/lesson-discussions/{id}/exercises` et `POST .../exercises/{attempt_id}/submit` dans `tests/contract/test_lesson_api.py`

### Implementation for User Story 3

- [ ] T019 [US3] Implémenter `generate_exercises()` et `submit_exercises()` dans `src/ollama_tutor/tutor/lesson_discussion.py` (3–5 questions via `PedagogicalTemplate`, scoring, `passed` si ≥60%, persistance `LessonExerciseAttempt`, mise à jour `path_steps.status`)
- [ ] T020 [US3] Ajouter endpoints `POST /api/tutor/lesson-discussions/{id}/exercises` et `POST .../exercises/{attempt_id}/submit` + `POST .../complete-manual` dans `src/ollama_tutor/web/server.py`
- [ ] T021 [US3] Ajouter UI exercices dans `src/ollama_tutor/web/static/tutor.html` (view-lecon : `launchExercises()`, `submitExercises()`, affichage score/feedback, boutons "Refaire" (nouveaux exercices) et "Marquer comme terminé quand même")

**Checkpoint**: US3 functional — exercices évaluables et clôture automatique/manuelle

---

## Phase 6: User Story 4 - Naviguer et retrouver l'état d'avancement (Priority: P2)

**Goal**: Parcours affiche badges `not_started`/`in_progress`/`completed` et progression globale ; re-ouverture restaure historique.

**Independent Test**: Après 1 leçon terminée sur 3, vérifier badges et progression ; rouvrir leçon terminée → historique cours/synthèse/exercices restauré sans réinitialisation.

### Tests for User Story 4

- [ ] T022 [P] [US4] Integration test flux complet parcours→leçon→cours→exercices→progression dans `tests/integration/test_lesson_flow.py`

### Implementation for User Story 4

- [ ] T023 [US4] Étendre `GET /api/tutor/paths` / `GET /api/tutor/path-steps` dans `src/ollama_tutor/web/server.py` pour inclure `status` et `discussion_id` par étape
- [ ] T024 [US4] Mettre à jour rendu parcours dans `src/ollama_tutor/web/static/tutor.html` (badges couleur, progression `x/ n`, clic conserve état)

**Checkpoint**: All user stories independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Finition multi-stories

- [X] T025 Gérer edge cases : leçon sans sources → message + "Importer des sources", génération en cours → boutons désactivés, abandon exercices → `in_progress` conservé
- [X] T026 Ajouter logging `_log_error` pour `lesson_discussion.py` vers `data/errors.log` dans `src/ollama_tutor/web/server.py` (Constitution VI)
- [X] T027 Valider `node --check` sur `src/ollama_tutor/web/static/tutor.html` et `venv/bin/pytest tests/ -q` (tout vert)
- [X] T028 Mettre à jour `specs/009-lecon-discussion-centree/quickstart.md` avec scénarios S1–S4 (ouvrir discussion, générer cours/synthèse, exercices, progression)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational
  - US1 → US2 → US3 séquentiels (partagent `LessonDiscussionService`)
  - US4 dépend de US1+US3 (statuts)
- **Polish (Phase 7)**: Depends on all stories

### Within Each User Story

- Tests FAIL before implementation
- Models → Services → Endpoints → UI

### Parallel Opportunities

- T003 et T006/T007 peuvent être préparés en parallèle (fichiers différents)
- T012/T013 et T017/T018 (tests US2/US3) en parallèle
- Une fois Foundational fini, US1 doit précéder US2/US3 (dépendance service)

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1+2
2. Complete Phase 3 (US1)
3. **STOP and VALIDATE**: Discussion isolée cliquable
4. Deploy/demo

### Incremental Delivery

1. Setup+Foundational → base
2. +US1 → discussion centrée (MVP)
3. +US2 → cours/synthèse
4. +US3 → exercices + clôture (feature complète P1)
5. +US4 → progression visible

---

## Notes

- [P] = different files, no dependencies
- Vérifier `node --check` après chaque modif `tutor.html`
- Commit après chaque tâche
