# Tasks: 012 Real Learning Packs

**Input**: Design documents from `/specs/012-real-learning-packs/` (plan.md, spec.md clarified 2026-09-18, research.md D1-D11, data-model.md, contracts/api.md, quickstart.md)

**Prerequisites**: plan.md & spec.md complete, clarifications intégrées (livraison en une fois, parents seuls, Cameroun BEPC/probatoire/bac, WCAG 2.2 AA, référentiel hybride)

**Tests**: Constitution III — chaque moteur reçoit des tests de régression offline (TestClient + stores tmp, LLM mockés, `@pytest.mark.asyncio` explicite)

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3, US4)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Vérifier branche et socle sans migration lourde ; poser les packs curriculum

- [X] T001 Verify branch 012-real-learning-packs and EDUNEXUS_DATA_DIR in `specs/012-real-learning-packs/plan.md`
- [X] T002 [P] Run baseline `python3 -m pytest tests/ -q` and note failures in `specs/012-real-learning-packs/tasks.md` (baseline 2026-09-18 : 1182 passed, 0 failure)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Socle partagé — packs curriculum, migrations minimales, prefs lisibilité, shell API

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Create `tutor/packs.py` loader/validator (stdlib json, schemaVersion, checksum) with skeletons `cm/bepc`, `cm/premiere-a/c/d`, `cm/terminale-a/c/d` (`statut: squelette_à_valider`) in `src/ollama_tutor/data/packs/`
- [X] T004 [P] Add idempotent migrations in `src/ollama_tutor/tutor/store.py` (tables `packs`, `atomic_notes`, `atomic_note_links`, `semester_plans`, `plan_slots`, `parent_shares` + columns `readability_json`, `streak_freeze_json`, `score_20`, `blueprint_key`)
- [X] T005 [P] Add `readability` keys (`fontScale`, `lineHeight`, `letterSpacing`, `theme`, `dyslexia`) with localStorage persistence in `web/vue/client/src/stores/preferences.ts` + CSS variable base in `web/vue/client/src/index.css`
- [X] T006 Setup API helpers (`packs/exams/share/notes/planner`) with cache-bust in `web/vue/client/src/services/api.ts`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Mémorisation active (Priority: P1) 🎯 MVP socle

**Goal**: Rappels dus visibles <10 s, quiz à rappel rédigé + feedback immédiat, séances entremêlées avec refonte surface des ratés (FR-001→FR-004, FR-014)

**Independent Test**: Backdater des dues → badge « À réviser » ; 5 réponses rédigées corrigées ; séance 3 types entremêlés ; `GET /api/tutor/reminders` retourne `due_count`

### Tests for User Story 1

- [X] T007 [P] [US1] Contract test `GET /api/tutor/reminders` (due, retard, stale_plan) in `tests/contract/test_012_reminders.py`
- [X] T008 [P] [US1] Integration test recall_written + interleave sampler in `tests/integration/test_012_recall_interleave.py`

### Implementation for User Story 1

- [X] T009 [P] [US1] Enrich `due_reviews()` (compteurs, retard) in `src/ollama_tutor/tutor/review.py` + `due_count` in `get_dashboard()` in `src/ollama_tutor/tutor/service.py`
- [X] T010 [P] [US1] Add `recall_written` kind to `_VALID_Q_KINDS` and written-answer render path in `src/ollama_tutor/tutor/assessment.py` (reuse `grade_answer` judge)
- [X] T011 [P] [US1] Replace round-robin `assessment.py:871` with interleaved sampler (mix 3 types, ratés en fin de séance) driven by `src/ollama_tutor/tutor/adaptation.py`
- [X] T012 [US1] Thin `GET /api/tutor/reminders` delegate in `src/ollama_tutor/web/server.py` (400/404 via `_log_error`)
- [X] T013 [US1] Create `RemindersView.vue` (À réviser + badge) and extend `QuizView.vue` (recall render) + `DashboardView.vue` (due badge) in `web/vue/client/src/views/`

**Checkpoint**: US1 fully functional and testable independently — SC-001, SC-002

---

## Phase 4: User Story 2 - BEPC, probatoire, bac (Priority: P1)

**Goal**: Programmes camerounais alignés, épreuves blanches chronométrées notées /20, correctifs ciblés, vue parent sur maîtrise réelle (FR-005→FR-008)

**Independent Test**: `cm/terminale-c` filtré programme ; brevet blanc verrouillé noté /20 avec détail compétences ; token parent → overview, révoqué → 403

### Tests for User Story 2

- [X] T014 [P] [US2] Contract test `POST /exams` (blueprint, /20, interrompue) + parent share (200/403) in `tests/contract/test_012_exams_parent.py`
- [X] T015 [P] [US2] Integration test épreuve blanche complète + reprise + correctif même compétence in `tests/integration/test_012_exam_blanc.py`

### Implementation for User Story 2

- [X] T016 [P] [US2] Create `tutor/exams.py` (blueprints BEPC/1ère/Terminale A/C/D, scaler /20 pondéré, verrouillage, reprise) wired to `create_exam` in `src/ollama_tutor/tutor/service.py`
- [X] T017 [P] [US2] Create `tutor/sharing.py` (consentement, share_token, vue agrégée lecture seule) on `LearnerProfile` in `src/ollama_tutor/tutor/store.py`
- [X] T018 [US2] Thin `POST /exams`, `POST/DELETE /learners/{id}/share`, `GET /parent/overview` in `src/ollama_tutor/web/server.py`
- [X] T019 [US2] Create `ExamView.vue` (chrono, verrouillage, détail /20) and `ParentView.vue` (temps, maîtrise, erreurs, jalon) in `web/vue/client/src/views/`

**Checkpoint**: US1+US2 both work independently — SC-003, SC-004

---

## Phase 5: User Story 3 - Niveau universitaire (Priority: P2)

**Goal**: Citations traçables en un clic, notes atomiques liées + plans assemblés, planning semestre ECTS sans surcharge (FR-009→FR-011)

**Independent Test**: 20 réponses → 95 % d'affirmations citées ; 5 notes liées → plan assemblé ; semestre 5 UE planifié <1 min sans semaine >150 %

### Tests for User Story 3

- [X] T020 [P] [US3] Contract test notes atomic CRUD/links/plan + `POST /planner/semester` in `tests/contract/test_012_notes_planner.py`
- [X] T021 [P] [US3] Integration test citations inline + assemblage de plan in `tests/integration/test_012_citations_notes.py`

### Implementation for User Story 3

- [X] T022 [P] [US3] Create `tutor/notes.py` (atomic_notes, links précise/contredit/mécanisme-de/exemple-de, assemble-plan) in `src/ollama_tutor/tutor/notes.py` (carnet existant inchangé)
- [X] T023 [P] [US3] Create `tutor/planner.py` (charge UE, 1 ECTS = 25-30 h, règle 150 %, recompaction plafonnée) in `src/ollama_tutor/tutor/planner.py`
- [X] T024 [US3] Render citations inline (affirmation → paragraphe exact) in `web/vue/client/src/views/LessonView.vue` (patron `notebook.py:138`)
- [X] T025 [US3] Thin notes/planner routes in `src/ollama_tutor/web/server.py` + `PlannerView.vue` and notebook atomic UI in `web/vue/client/src/views/`

**Checkpoint**: All 3 stories independently functional — SC-005, SC-006

---

## Phase 6: User Story 4 - Motivation saine + lisibilité (Priority: P3)

**Goal**: XP proportionné anti-grinding + séries hebdo avec joker ; mode lisibilité WCAG 2.2 AA + preset Dyslexie BDA (FR-012→FR-014, FR-013)

**Independent Test**: 20× même exercice facile → <10 % XP séance normale ; preset Dyslexie → contraste ≥4.5:1, interligne 1.5, TTS mot-à-mot ; bookmarklet 1.4.12 sans troncature

### Tests for User Story 4

- [X] T026 [P] [US4] Unit test rewards (pondération difficulté, decay <7j, cap journalier, joker) in `tests/unit/test_012_rewards.py`

### Implementation for User Story 4

- [X] T027 [P] [US4] Create `tutor/rewards.py` (`XP = base × decay`, caps, série hebdo + freeze) and rewire call sites in `src/ollama_tutor/tutor/service.py`
- [X] T028 [US4] Implement readability presets + focus visible + colonne unique in `web/vue/client/src/index.css` wired to `preferences.ts` across `web/vue/client/src/views/`
- [X] T029 [US4] Wire TTS word-highlight + speed control in `web/vue/client/src/views/LessonView.vue` (speechSynthesis existant)

**Checkpoint**: All 4 stories independently functional — SC-007, SC-008

---

## Phase 7: Polish & Cross-Cutting

**Purpose**: Validation bout-en-bout + point de contrôle humain OBC + while single-shot delivery

- [X] T030 Run `quickstart.md` Scenarios 1-4 and full `python3 -m pytest tests/ -q` + `npm --prefix web/vue run build` in `specs/012-real-learning-packs/quickstart.md` (2026-09-18 : 1267 passed en 366 s + build 16 s OK)
- [ ] T031 [MANUAL] Relecture humaine de l'arrêté MINESEC 2022 (durées/coeffs) then flip packs `squelette_à_valider → validé` in `src/ollama_tutor/data/packs/` — À FAIRE PAR L'UTILISATEUR, bloque le statut `validé` des packs
- [X] T032 [P] Verify no `tutor/`→`fastapi` imports drift via `tests/contract/test_tutor_imports.py` and no new runtime deps in `pyproject.toml` (2026-09-18 : gate vert dans la suite + `git diff` vide sur pyproject.toml et package.json)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational; can proceed in parallel once Phase 2 done, or sequentially P1→P1→P2→P3
- **Polish (Phase 7)**: Depends on all stories; T031 manual OBC review gates pack validation

### User Story Dependencies

- **US1 (P1)**: After Foundational — No dependencies on other stories, socle mémorisation
- **US2 (P1)**: After Foundational — Reuses US1 quiz engine but independently testable (mock quiz kind)
- **US3 (P2)**: After Foundational — Reuses RAG citations + notebook; independent via TestClient
- **US4 (P3)**: After Foundational — Reuses XP call sites + prefs; CSS-only mostly, no deps on other stories

### Within Each User Story

- Tests FAIL before implementation → Models/store → Services → Endpoints → Frontend integration

### Parallel Opportunities

- T002, T004, T005 parallel within Setup/Foundational (different files)
- T007||T008, T014||T015, T020||T021 test pairs parallel
- Once Foundational done, US1/US2/US3/US4 can start in parallel by 4 devs (distinct files per story)
- T026 unit + T027/T028 parallel (tests vs code, different files)

---

## Parallel Example: User Story 1

```bash
# Launch tests together:
Task: "Contract test reminders in tests/contract/test_012_reminders.py"   # T007
Task: "Integration test recall+interleave in tests/integration/test_012_recall_interleave.py"  # T008

# Launch services together (different files):
Task: "Enrich due_reviews in review.py + dashboard"  # T009
Task: "Add recall_written kind in assessment.py"      # T010
Task: "Interleaved sampler in adaptation.py"          # T011
```

---

## Implementation Strategy

### MVP Validation Order (single-shot delivery per clarification 2026-09-18)

1. Complete Phase 1+2 (Setup+Foundational)
2. Complete Phase 3 (US1) → STOP and VALIDATE via quickstart Scenario 1
3. Complete Phase 4 (US2) → VALIDATE Scenario 2 (bugs OBC : BEPC/probatoire/bac)
4. Complete Phase 5 (US3) → VALIDATE Scenario 3
5. Complete Phase 6 (US4) → VALIDATE Scenario 4
6. Phase 7 Polish → full suite + OBC human review (T031)

### Incremental Delivery

Each phase adds value without breaking previous SCs; single delivery at the end per user decision (pas de découpage en versions).

### Parallel Team Strategy

With 4 devs post-Foundational:
- Dev A: US1 (mémorisation)
- Dev B: US2 (examens + parent)
- Dev C: US3 (notes + planning + citations)
- Dev D: US4 (rewards + lisibilité)
Merge after each checkpoint, run `quickstart.md`.

---

## Notes

- [P] tasks = different files, no deps
- [Story] label maps to spec.md US order
- T031 is MANUAL (human OBC reading) — cannot be automated, gates pack `validé` status
- Constitution V: stdlib-only validation des packs, CSS pur, aucun poll-thread — T032 guards drift
- 32 tasks total: Setup 2, Foundational 4, US1 7, US2 6, US3 6, US4 4, Polish 3
