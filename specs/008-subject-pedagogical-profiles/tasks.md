---

description: "Task list for EduNexus adaptatif (Feature 008)"

---

# Tasks: EduNexus adaptatif — profil pédagogique, parcours explicable, capture de programme & carnet de matière

**Input**: Design documents from `/specs/008-subject-pedagogical-profiles/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Les tests sont inclus (TDD) conformément à la constitution (principe III : tests hors-ligne d'abord) et aux conventions du projet (chaque fonctionnalité couverte par des tests exécutables hors-ligne via `httpx.MockTransport`).

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US9)
- Include exact file paths in descriptions

## Path Conventions

- Single project: `src/`, `tests/` at repository root
- Core logic in `src/ollama_tutor/tutor/` (principe I — no fastapi/textual imports)
- Web transport in `src/ollama_tutor/web/server.py`; UI in `src/ollama_tutor/web/static/tutor.html`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create feature directory structure and confirm branch `008-subject-pedagogical-profiles` in `specs/008-subject-pedagogical-profiles/`
- [X] T002 [P] Verify baseline test suite passes: `venv/bin/pytest tests/ -q` (~391 tests) before any changes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Add new entity dataclasses to `src/ollama_tutor/tutor/models.py`: `SubjectProfile`, `PedagogicalTemplate`, `CompetencyNode`, `GraphEdge`, `CapturedProgram`, `ProgramNode`, `CaptureImage`, `ConversationPhoto`, `SubjectNotebook`, `NotebookOutput`, `LearnerProfile` (per data-model.md)
- [X] T004 [P] Extend `PathStep` in `src/ollama_tutor/tutor/models.py` with explicability fields: `why_now`, `prerequisites`, `sources`, `planned_activity`, `expected_proof` (FR-013)
- [X] T005 Add `learner_id` column to existing tables (`subjects`, `concepts`, `learning_paths`, `path_steps`, `conversations`, `exercises`, `attempts`) via idempotent migration in `src/ollama_tutor/tutor/store.py` `_migrate` (FR-037/FR-038)
- [X] T006 Add new tables to `LibraryStore._create_schema` in `src/ollama_tutor/tutor/store.py`: `subject_profiles`, `pedagogical_templates`, `competency_nodes`, `graph_edges`, `captured_programs`, `program_nodes`, `capture_images`, `conversation_photos`, `subject_notebooks`, `notebook_outputs`, `learner_profiles` (per data-model.md)
- [X] T007 [P] Add `learner_id` filter helper to `LibraryStore` in `src/ollama_tutor/tutor/store.py` (all queries scoped by active learner)
- [X] T008 [P] Add `_log_error` usage for new services in `src/ollama_tutor/tutor/` (principe VI — errors logged to `data/errors.log`)

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Créer une matière avec un profil pédagogique (Priority: P1) 🎯 MVP

**Goal**: Créer une matière configurée comme profil pédagogique explicite avec modèles prédéfinis et conversion d'objectif en paramètres.

**Independent Test**: Créer une matière « Java » via le modèle Programmation, vérifier que les activités/preuves sont préremplies puis modifiables, que le profil est persisté et restauré, et que « apprendre Java pour créer des projets » produit des paramètres internes.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US1] Unit test for `SubjectProfile` CRUD + template prefill in `tests/unit/test_profiles.py`
- [X] T010 [P] [US1] Unit test for goal→parameters conversion (FR-004) in `tests/unit/test_profiles.py`
- [X] T011 [P] [US1] Contract test for profile endpoints in `tests/contract/test_tutor_profiles_api.py`

### Implementation for User Story 1

- [X] T012 [P] [US1] Implement `SubjectProfile`/`PedagogicalTemplate` persistence methods in `src/ollama_tutor/tutor/store.py` (get/set profile, list templates)
- [X] T013 [P] [US1] Create `src/ollama_tutor/tutor/profiles.py` with `ProfileService` (template prefill, goal interpretation, validation FR-001/FR-003/FR-004/FR-005/FR-006)
- [X] T014 [US1] Add profile endpoints in `src/ollama_tutor/web/server.py`: `GET/PUT /api/tutor/subjects/{subject_id}/profile`, `GET /api/tutor/pedagogical-templates`, `POST .../profile/interpret-goal` (per contracts/api.md)
- [X] T015 [US1] Add profile UI (form 2 niveaux + templates) in `src/ollama_tutor/web/static/tutor.html`
- [X] T016 [US1] Add logging for profile operations (principe VI)

**Checkpoint**: User Story 1 fully functional and testable independently (MVP)

---

## Phase 4: User Story 2 - Construire un graphe de compétences (Priority: P1)

**Goal**: Construire un graphe de compétences à partir des livres importés par règles déterministes + candidats LLM soumis à validation.

**Independent Test**: Importer deux livres Java couvrant les mêmes concepts, vérifier la fusion par identifiant, la détection de prérequis, et que chaque nœud référence ses sources avec statut de validation.

### Tests for User Story 2 ⚠️

- [X] T017 [P] [US2] Unit test for graph construction (merge, edges, confidence) in `tests/unit/test_graph.py`
- [X] T018 [P] [US2] Contract test for graph endpoints in `tests/contract/test_tutor_graph_api.py`

### Implementation for User Story 2

- [X] T019 [P] [US2] Implement `CompetencyNode`/`GraphEdge` persistence in `src/ollama_tutor/tutor/store.py`
- [X] T020 [P] [US2] Create `src/ollama_tutor/tutor/graph.py` with `GraphBuilder` (deterministic rules from TOC/titles/index, merge by conceptual id, prerequisite detection, LLM candidates marked `ai_proposed`)
- [X] T021 [US2] Add graph endpoints in `src/ollama_tutor/web/server.py`: `GET .../graph`, `POST .../graph/build`, `POST /api/tutor/graph/nodes/{node_id}/validate` (per contracts/api.md)
- [X] T022 [US2] Add graph UI (visualisation + validation des propositions) in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1 AND 2 work independently

---

## Phase 5: User Story 3 - Générer un parcours explicable (Priority: P1)

**Goal**: Générer un parcours ordonné et explicable (sélection + tri topologique), éditable par l'utilisateur.

**Independent Test**: Avec un profil Java et un graphe construit, générer un parcours, vérifier que chaque étape affiche « pourquoi maintenant », prérequis, sources, activité, preuve, et que l'ordre respecte les prérequis ; déplacer une étape et vérifier la mise à jour.

### Tests for User Story 3 ⚠️

- [X] T023 [P] [US3] Unit test for path generation (selection + topological order) in `tests/unit/test_path_builder.py`
- [X] T024 [P] [US3] Unit test for path edit (move/merge/exclude + persistence) in `tests/unit/test_path_builder.py`

### Implementation for User Story 3

- [X] T025 [P] [US3] Extend `LearningPath`/`PathStep` persistence in `src/ollama_tutor/tutor/store.py` (explicability fields)
- [X] T026 [P] [US3] Create `src/ollama_tutor/tutor/path_builder.py` with `PathBuilder` (select non-mastered nodes with covered prerequisites, topological sort, why_now/sources/activity/proof per step)
- [X] T027 [US3] Add path endpoints in `src/ollama_tutor/web/server.py`: `POST .../path/generate`, `PUT .../path` (per contracts/api.md)
- [X] T028 [US3] Add path UI (affichage explicable + édition) in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1–3 work independently

---

## Phase 6: User Story 4 - Adapter le parcours après chaque séance (Priority: P2)

**Goal**: Mettre à jour la maîtrise après chaque activité et réviser seulement une fenêtre locale du parcours, avec validation après plusieurs preuves différentes.

**Independent Test**: Réussir puis échouer sur une notion, vérifier que seule la fenêtre proche est recalculée et qu'une compétence n'est validée qu'après plusieurs preuves différentes.

### Tests for User Story 4 ⚠️

- [X] T029 [P] [US4] Unit test for window recomputation (FR-016, SC-005) in `tests/unit/test_adaptation.py`
- [X] T030 [P] [US4] Unit test for multi-proof mastery validation (FR-017, SC-006) in `tests/unit/test_adaptation.py`

### Implementation for User Story 4

- [X] T031 [P] [US4] Create `src/ollama_tutor/tutor/adaptation.py` with `AdaptationService` (window recompute, stability portion FR-019, multi-proof validation, record answer/time/hints/source FR-018)
- [X] T032 [US4] Integrate adaptation into `src/ollama_tutor/tutor/service.py` (after each activity, trigger window recompute)
- [X] T033 [US4] Add adaptation UI (stability portion visible, window status) in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1–4 work independently

---

## Phase 7: User Story 6 - Capturer un programme par photo ou PDF (OCR) (Priority: P2)

**Goal**: Capturer un programme/table des matières par photo ou PDF via pipeline OCR local incrémental, structuré en arbre éditable avec validation.

**Independent Test**: Importer une photo d'une table des matières, vérifier l'extraction OCR locale, la structuration en arbre éditable, le signalement des passages incertains et la correction avant génération du parcours.

### Tests for User Story 6 ⚠️

- [X] T034 [P] [US6] Unit test for program capture pipeline (preprocess, OCR, structure, reconcile) in `tests/unit/test_program_capture.py`
- [X] T035 [P] [US6] Unit test for incremental queue + status (FR-023) in `tests/unit/test_program_capture.py`
- [X] T036 [P] [US6] Contract test for capture endpoints in `tests/contract/test_tutor_capture_api.py`

### Implementation for User Story 6

- [X] T037 [P] [US6] Implement `CapturedProgram`/`ProgramNode`/`CaptureImage` persistence in `src/ollama_tutor/tutor/store.py`
- [X] T038 [P] [US6] Create `src/ollama_tutor/tutor/program_capture.py` with `ProgramCaptureService` (reuse `docling_ocr.py` + `pdftoppm`, preprocessing, structuring, reconciliation, validation, incremental queue)
- [X] T039 [US6] Add capture endpoints in `src/ollama_tutor/web/server.py`: `POST .../program/capture`, `GET .../program/{id}`, `PUT .../nodes/{node_id}`, `POST .../confirm` (per contracts/api.md)
- [X] T040 [US6] Add capture UI (upload, arbre éditable, statut de file, correction) in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1–4, 6 work independently

---

## Phase 8: User Story 7 - Importer une photo dans une conversation (Priority: P2)

**Goal**: Importer une photo dans une conversation de tutorat, OCR local, confirmation du texte, intégration comme source.

**Independent Test**: Dans une conversation, importer une photo d'un énoncé, vérifier l'extraction OCR, l'affichage/confirmation du texte, et que le tuteur répond en s'appuyant sur ce texte en le distinguant de sa génération.

### Tests for User Story 7 ⚠️

- [X] T041 [P] [US7] Unit test for conversation photo import (OCR, confirmation, source linkage) in `tests/unit/test_conversation_photo.py`

### Implementation for User Story 7

- [X] T042 [P] [US7] Implement `ConversationPhoto` persistence in `src/ollama_tutor/tutor/store.py`
- [X] T043 [P] [US7] Create `src/ollama_tutor/tutor/conversation_photo.py` with `ConversationPhotoService` (reuse OCR, confirmation gate FR-031, source integration)
- [X] T044 [US7] Add photo endpoints in `src/ollama_tutor/web/server.py`: `POST /api/tutor/conversations/{id}/photo`, `POST /api/tutor/conversation-photos/{id}/confirm` (per contracts/api.md)
- [X] T045 [US7] Add photo import UI in conversation in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1–4, 6, 7 work independently

---

## Phase 9: User Story 9 - Utiliser EduNexus en famille (multi-utilisateur) (Priority: P2)

**Goal**: Plusieurs profils d'apprenant sur un seul PC, chacun avec ses propres données isolées, avec création/sélection/bascule.

**Independent Test**: Créer deux profils, créer une matière distincte pour chacun (même nom possible), basculer entre profils et vérifier l'isolation des matières/parcours/progression/conversations.

### Tests for User Story 9 ⚠️

- [X] T046 [P] [US9] Unit test for learner profile CRUD + cascade delete in `tests/unit/test_learners.py`
- [X] T047 [P] [US9] Unit test for data isolation across learners in `tests/unit/test_learners.py`
- [X] T048 [P] [US9] Contract test for learner endpoints in `tests/contract/test_tutor_learners_api.py`

### Implementation for User Story 9

- [X] T049 [P] [US9] Implement `LearnerProfile` persistence + cascade delete in `src/ollama_tutor/tutor/store.py`
- [X] T050 [P] [US9] Create `src/ollama_tutor/tutor/learners.py` with `LearnerService` (create/select/switch, isolation via `learner_id`)
- [X] T051 [US9] Add learner endpoints in `src/ollama_tutor/web/server.py`: `GET/POST /api/tutor/learners`, `POST .../activate`, `DELETE .../{id}` + `X-Learner-Id` header handling (per contracts/api.md)
- [X] T052 [US9] Add learner selector UI (création/sélection/bascule au démarrage) in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1–4, 6, 7, 9 work independently

---

## Phase 10: User Story 5 - Consulter le tableau de bord du profil (Priority: P3)

**Goal**: Tableau de bord de la matière : notions couvertes/non couvertes/contradictoires/incertaines, distinction visuelle des 3 catégories, traçabilité des propositions.

**Independent Test**: Après construction du graphe et génération du parcours, ouvrir le tableau de bord et vérifier la distinction des 3 catégories et l'affichage des notions couvertes vs non couvertes.

### Tests for User Story 5 ⚠️

- [X] T053 [P] [US5] Unit test for dashboard aggregation (covered/uncovered/contradictory/unconfirmed) in `tests/unit/test_graph.py`

### Implementation for User Story 5

- [X] T054 [P] [US5] Implement dashboard aggregation in `src/ollama_tutor/tutor/graph.py` (or `service.py`) — covered/uncovered/contradictory/unconfirmed + provenance (FR-020/FR-021/FR-022)
- [X] T055 [US5] Add dashboard endpoint in `src/ollama_tutor/web/server.py` (reuse graph data)
- [X] T056 [US5] Add dashboard UI (3 catégories visuellement distinctes) in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: User Stories 1–7, 9 work independently

---

## Phase 11: User Story 8 - Utiliser le carnet de matière (inspiré NotebookLM) (Priority: P3)

**Goal**: Carnet de matière local regroupant livres, programme, objectifs, niveau, compétences, notes et parcours, avec actions RAG produisant des sorties liées aux sources et supprimables.

**Independent Test**: Ouvrir le carnet d'une matière, ajouter une note, lancer « résumer cette source » et « me questionner sans afficher la réponse », vérifier que les sorties sont liées aux sources et supprimables.

### Tests for User Story 8 ⚠️

- [X] T057 [P] [US8] Unit test for notebook CRUD + notes in `tests/unit/test_notebook.py`
- [X] T058 [P] [US8] Unit test for notebook actions (summarize, quiz without answer, sources linkage, delete) in `tests/unit/test_notebook.py`

### Implementation for User Story 8

- [X] T059 [P] [US8] Implement `SubjectNotebook`/`NotebookOutput` persistence in `src/ollama_tutor/tutor/store.py`
- [X] T060 [P] [US8] Create `src/ollama_tutor/tutor/notebook.py` with `NotebookService` (actions FR-033, RAG context FR-034, source linkage FR-035, provenance FR-036)
- [X] T061 [US8] Add notebook endpoints in `src/ollama_tutor/web/server.py`: `GET .../notebook`, `POST .../notes`, `POST .../actions`, `DELETE /api/tutor/notebook-outputs/{id}` (per contracts/api.md)
- [X] T062 [US8] Add notebook UI in `src/ollama_tutor/web/static/tutor.html`

**Checkpoint**: All user stories work independently

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T063 [P] Add WebSocket events for new features (`profile_updated`, `graph_built`, `path_generated`, `program_status`, `photo_status`, `notebook_output`) in `src/ollama_tutor/web/server.py` (per contracts/api.md)
- [X] T064 [P] Add integration test for adaptive flow end-to-end in `tests/integration/test_adaptive_flow.py`
- [X] T065 Run full test suite: `venv/bin/pytest tests/ -q` — all green
- [X] T066 [P] Validate `tutor.html` inline JS syntax: extract `<script>` then `node --check`
- [ ] T067 [P] Run `./benchmark.sh` after any `client.py`/render-path changes (regression gate)
- [ ] T068 Update `specs/008-subject-pedagogical-profiles/quickstart.md` if validation reveals gaps
- [ ] T069 Run quickstart.md validation scenarios S1–S9

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational completion
  - US1 (P1) → US2 (P1) → US3 (P1) → US4 (P2) : chaîne principale (graphe s'appuie sur profil, parcours sur graphe, adaptation sur parcours)
  - US6 (P2) : indépendant, alimente US2/US3 (capture → graphe/parcours)
  - US7 (P2) : indépendant (conversation)
  - US9 (P2) : indépendant (multi-utilisateur, mais le schéma `learner_id` est en Phase 2)
  - US5 (P3) : dépend de US2/US3 (tableau de bord)
  - US8 (P3) : dépend de US2/US3/US6 (carnet)
- **Polish (Phase 12)**: Depends on all desired user stories

### User Story Dependencies

- **US1 (P1)**: After Foundational — no story deps
- **US2 (P1)**: After Foundational — integrates with US1 (profile) but independently testable
- **US3 (P1)**: After Foundational — depends on US2 (graph)
- **US4 (P2)**: After Foundational — depends on US3 (path)
- **US6 (P2)**: After Foundational — independent (feeds US2/US3)
- **US7 (P2)**: After Foundational — independent
- **US9 (P2)**: After Foundational — independent (schema in Phase 2)
- **US5 (P3)**: After Foundational — depends on US2/US3
- **US8 (P3)**: After Foundational — depends on US2/US3/US6

### Within Each User Story

- Tests written FIRST and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel
- Once Foundational completes, independent stories (US6, US7, US9) can start in parallel with the US1→US2→US3→US4 chain
- All tests for a user story marked [P] can run in parallel
- Models within a story marked [P] can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Unit test for SubjectProfile CRUD + template prefill in tests/unit/test_profiles.py"
Task: "Unit test for goal→parameters conversion in tests/unit/test_profiles.py"
Task: "Contract test for profile endpoints in tests/contract/test_tutor_profiles_api.py"

# Launch all models/persistence for User Story 1 together:
Task: "Implement SubjectProfile/PedagogicalTemplate persistence in src/ollama_tutor/tutor/store.py"
Task: "Create src/ollama_tutor/tutor/profiles.py with ProfileService"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (profil pédagogique)
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add US1 (profil) → Test → Deploy/Demo (MVP!)
3. Add US2 (graphe) → Test → Deploy/Demo
4. Add US3 (parcours) → Test → Deploy/Demo
5. Add US4 (adaptation) → Test → Deploy/Demo
6. Add US6 (capture OCR) → Test → Deploy/Demo
7. Add US7 (photo conversation) → Test → Deploy/Demo
8. Add US9 (multi-utilisateur) → Test → Deploy/Demo
9. Add US5 (tableau de bord) → Test → Deploy/Demo
10. Add US8 (carnet) → Test → Deploy/Demo
11. Polish & cross-cutting

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 → US2 → US3 → US4 (chaîne principale)
   - Developer B: US6 (capture OCR)
   - Developer C: US7 (photo conversation) + US9 (multi-utilisateur)
   - Developer D: US5 (tableau de bord) + US8 (carnet) — après US2/US3/US6
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- Constitution: core logic in `tutor/` (no fastapi/textual), tests offline via `httpx.MockTransport`, no new runtime deps without justification, errors logged to `data/errors.log`
