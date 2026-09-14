# Tasks: 011 Subject & Learner Context Isolation

**Input**: Design documents from `/specs/011-subject-learner-context/` (plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md)

**Prerequisites**: plan.md & spec.md complete, clarifications 2026-09-14 integrated (isolation par couple, suppression Non classé avec confirmation, learners filtrés par matière, bibliothèque filtrée + toggle)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Vérifier branche et outillage sans migration lourde

- [X] T001 Verify branch 011-subject-learner-context and EDUNEXUS_DATA_DIR in `specs/011-subject-learner-context/plan.md`
- [X] T002 [P] Run baseline `venv/bin/pytest tests/ -q` and note 1 pre-existing `test_models_get_offline_empty_lists` failure in `specs/011-subject-learner-context/tasks.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Socle backend/frontend partagé avant stories — inval cache + prefs persistées

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Add `subject_id`/`learner_id` query parsing helpers in `src/ollama_tutor/web/server.py` (thin delegate to service, log via `_log_error`, 400 on invalid id)
- [X] T004 [P] Extend `src/ollama_tutor/web/server.py` GET handlers for filtered queries: `GET /api/tutor/books`, `GET /api/tutor/learners`, `GET /api/tutor/learning-paths` to accept `subject_id`/`learner_id`/`all` (delegate to `TutorService`/`LearnerService`)
- [X] T005 [P] Add Vue `preferences.ts` keys `activeSubjectId`, `activeLearnerId`, `showAllSources` with localStorage persistence and event `subjectChange` in `web/vue/client/src/stores/preferences.ts`
- [X] T006 Setup invalidation helpers `invalidateSubjectCaches()` (paths/dashboard/books/learners) in `web/vue/client/src/services/api.ts`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Changer de matière filtre toutes les données (Priority: P1) 🎯 MVP

**Goal**: Sélection header "Non classé" ↔ "java" filtre immédiatement Accueil et Mon parcours par couple (matière × apprenant), persiste après reload, état vide explicite si aucune donnée (FR-001, FR-003, FR-006, FR-007, FR-009)

**Independent Test**: Créer 2 matières (Non classé avec parcours Python 15 étapes, java vide), switch → Accueil affiche 0% et plus "Introduction à Python" pour java, reload conserve filtre; `GET /api/tutor/learning-paths?subject_id=<javaId>&learner_id=...` retourne `[]`

### Tests for User Story 1

- [X] T007 [P] [US1] Contract test subject filtering for `GET /api/tutor/learning-paths?subject_id=&learner_id=` in `tests/contract/test_011_subject_filter.py`
- [X] T008 [P] [US1] Integration test subject switch isolation (Mon parcours + Accueil + reload) in `tests/integration/test_011_subject_isolation.py`

### Implementation for User Story 1

- [X] T009 [P] [US1] Implement `LearnerService/TutorService` filtering by couple `(subject_id, learner_id)` for `learning_paths`/`dashboard` stats in `src/ollama_tutor/tutor/service.py`
- [X] T010 [P] [US1] Implement `TutorService.get_dashboard()` filtered by `(subject_id, learner_id)` with empty state `nextStep:null` in `src/ollama_tutor/tutor/service.py`
- [X] T011 [US1] Implement Vue `DashboardView.vue` to call filtered dashboard and show empty CTA "Aucun parcours pour cette matière" in `web/vue/client/src/views/DashboardView.vue`
- [X] T012 [US1] Implement Vue `PathView.vue` filtered `learning-paths?subject_id=&learner_id=` with fallback and cache invalidation on `subjectChange` in `web/vue/client/src/views/PathView.vue`
- [X] T013 [US1] Wire `AppShell.vue` header subject selector to `preferences.activeSubjectId` with emit `subjectChange` and persist in `web/vue/client/src/components/AppShell.vue`

**Checkpoint**: US1 fully functional and testable independently — 10 switches successifs sans fuite (SC-005)

---

## Phase 4: User Story 2 - Renommer / supprimer la matière "Non classé" (Priority: P2)

**Goal**: "Non classé" renommable/supprimable comme toute matière, même UX, validation insensible à la casse, confirmation obligatoire, propagation partout (FR-002)

**Independent Test**: PATCH "Non classé" → "Python" visible partout après refresh; tentative rename vers "java" → 400 "Nom déjà utilisé"; DELETE avec modale → fallback

### Tests for User Story 2

- [X] T014 [P] [US2] Contract test rename/delete Non classé `PATCH/DELETE /api/tutor/subjects/{id}` in `tests/contract/test_011_subject_mutation.py`
- [X] T015 [P] [US2] Integration test rename empty/duplicate and delete with fallback in `tests/integration/test_011_subject_rename_delete.py`

### Implementation for User Story 2

- [X] T016 [P] [US2] Allow `PATCH /api/tutor/subjects/{id}` for "Non classé" with unique check (case-insensitive per learner) in `src/ollama_tutor/tutor/store.py` (method `rename_subject`)
- [X] T017 [P] [US2] Allow `DELETE /api/tutor/subjects/{id}` for "Non classé" with cascade and fallback logic in `src/ollama_tutor/web/server.py` (return `fallbackSubjectId`)
- [X] T018 [US2] Implement header/subject list rename modal and delete ConfirmDialog with 409 handling in `web/vue/client/src/components/AppShell.vue`
- [X] T019 [US2] Propagate new name to `DashboardView.vue` badge and `PathView.vue` titles via reactive `preferences`/`filtered` in `web/vue/client/src/views/DashboardView.vue`

**Checkpoint**: US1+US2 both work independently — 100% rename succès (SC-002)

---

## Phase 5: User Story 3 - Gérer les apprenants sur #/apprenants (Priority: P2)

**Goal**: Page #/apprenants filtrée par matière active (Q3=B), créer/lister/activer/supprimer apprenants par matière, badge "Matière active" cohérent avec header (FR-004, FR-005, FR-008)

**Independent Test**: En "java" créer "Alice" → liste contient Alice seule (pas ceux de Python); switch → "Python" liste change; activate reflète header; DELETE avec confirmation; badge suit header (SC-003 20 navs)

### Tests for User Story 3

- [X] T020 [P] [US3] Contract test `GET /api/tutor/learners?subject_id=` filtered by subject in `tests/contract/test_011_learners_filtered.py`
- [X] T021 [P] [US3] Integration test learner CRUD filtered per matière and badge coherence in `tests/integration/test_011_learners_crud.py`

### Implementation for User Story 3

- [X] T022 [P] [US3] Implement `LearnerService.list_filtered(subject_id)` returning learners with `(subject_id, learner_id)` data in `src/ollama_tutor/tutor/learners.py`
- [X] T023 [P] [US3] Extend `GET /api/tutor/learners` with `?subject_id=` and `POST /api/tutor/learners` to create for active subject in `src/ollama_tutor/web/server.py`
- [X] T024 [US3] Implement filtered `LearnersView.vue`: create form (FR-008 validation), list `learners?subject_id=`, activate/delete with ConfirmDialog, badge "Matière active" bind to `preferences.activeSubjectId` in `web/vue/client/src/views/LearnersView.vue`
- [X] T025 [US3] Ensure `LearnerService.delete` cascades per `store.py` and fallback active learner selection in `web/vue/client/src/stores/preferences.ts`

**Checkpoint**: All 3 stories independently functional — création <30s (SC-004), badge cohérent 20/20

---

## Phase 6: Polish & Cross-Cutting (Q4 & FR-009)

**Purpose**: Bibliothèque filtrée avec toggle + polish global

- [X] T026 Implement `GET /api/tutor/books?subject_id=&all=` filtering by `subject_books` with toggle in `src/ollama_tutor/web/server.py`
- [X] T027 Implement `LibraryView.vue` filtered books + toggle "Toutes" persisting `showAllSources` in `web/vue/client/src/views/LibraryView.vue`
- [X] T028 [P] Add cache invalidation on learner switch (FR-009) for `filteredPaths/dashboard/books/learners` in `web/vue/client/src/services/api.ts`
- [X] T029 [P] Contract test books filtering `GET /api/tutor/books?subject_id=` and all toggle in `tests/contract/test_011_books_filtered.py`
- [X] T030 Run `quickstart.md` validation Scenarios 1-4 and `venv/bin/pytest tests/ -q` + `npm --prefix web/vue/client run build` in `specs/011-subject-learner-context/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational; can proceed in parallel once Phase 2 done, or sequentially P1→P2→P3
- **Polish (Phase 6)**: Depends on US1+US3 (uses their filters), ideally after US2

### User Story Dependencies

- **US1 (P1)**: After Foundational — No dependencies on other stories, provides MVP
- **US2 (P2)**: After Foundational — May reuse US1 filters but independently testable; no hard dep on US1
- **US3 (P2)**: After Foundational — Depends on subject context from US1 (filtered learners) but testable with mocked subject_id

### Within Each User Story

- Tests FAIL before implementation → Models/services → Endpoints → Frontend integration

### Parallel Opportunities

- T002, T004, T005 parallel within Setup/Foundational (different files)
- T007||T008, T009||T010, T014||T015, T020||T021 test pairs parallel
- Once Foundational done, US1/US2/US3 can start in parallel by 3 devs
- T028||T029 polish parallel (frontend vs contract)

---

## Parallel Example: User Story 1

```bash
# Launch tests together:
Task: "Contract test subject filtering in tests/contract/test_011_subject_filter.py"   # T007
Task: "Integration test subject isolation in tests/integration/test_011_subject_isolation.py"  # T008

# Launch service filters together:
Task: "Implement filtering by couple in service.py"  # T009
Task: "Implement dashboard filtered in service.py"   # T010 (same file -> sequential, but T011 Vue parallel)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1+2 (Setup+Foundational)
2. Complete Phase 3 (US1) → MVP demo: switch Non classé ↔ java isole données sans fuite
3. STOP and VALIDATE via quickstart Scenario 1

### Incremental Delivery

1. Setup+Foundational → foundation ready
2. +US1 → Démo MVP (10 switches sans fuite)
3. +US2 → Rename/delete Non classé
4. +US3 → Apprenants filtrés par matière
5. +Polish → Bibliothèque toggle + inval cache learner
Each increment adds value without breaking previous SCs.

### Parallel Team Strategy

With 3 devs post-Foundational:
- Dev A: US1 (filtering)
- Dev B: US2 (rename/delete)
- Dev C: US3 (learners)
Merge after each checkpoint, run `quickstart.md` Sc 1-4.

---

## Notes

- [P] tasks = different files, no deps
- [Story] label maps to spec.md US order
- Each story independently testable via quickstart Scenarios 1-4
- 400 "Nom déjà utilisé" message i18n exists (FR/EN) — reuse
- No new runtime dep; confirm modale reuses existing component
