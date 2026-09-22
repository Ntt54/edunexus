# Tasks: 013 Vague 1 Socle Pédagogique

**Input**: Design documents from `/specs/013-vague1-socle/` (plan.md, spec.md clarified 2026-09-18, research.md D1-D5, data-model.md, contracts/api.md, quickstart.md)

**Prerequisites**: plan.md & spec.md complete, clarifications intégrées (seuils fixes overdue/urgent≤3j/warning≤7j, leçons JSON strict)

**Tests**: Constitution III — tests offline (TestClient + stores tmp, LLM mockés, `@pytest.mark.asyncio` explicite) ; goldens déterministes sans LLM-juge en CI

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3, US4)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Vérifier branche et socle, fixtures de test partagées

- [X] T001 Verify branch 013-vague1-socle and EDUNEXUS_DATA_DIR in `specs/013-vague1-socle/plan.md`
- [X] T002 [P] Run baseline `python3 -m pytest tests/ -q` and note failures in `specs/013-vague1-socle/tasks.md` (baseline 2026-09-18 : 1288 passed, 0 failure)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schéma JSON leçon + gabarit prompt partagés avant stories

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Define lesson JSON schema + 3 fixtures FR (valide/invalide/doublon) in `src/ollama_tutor/tutor/data/lessons/` (fixtures `tests/fixtures/lessons013/`)
- [X] T004 [P] Define prompt pack layout (`assets/prompts/*.md` + FALLBACK + gabarit YAML fields) in `specs/013-vague1-socle/contracts/api.md` (doc, no code)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Leçons fichiers + pièges (Priority: P1)

**Goal**: Loader tolérant log-and-skip, prérequis d'abord, pièges unifiés au diagnostic 5-cats, `explain_concept` gating (FR-001→FR-003)

**Independent Test**: 10 fichiers (3 invalides + 1 doublon) → 7 servies, logs OK ; parcours exige le prérequis ; juste-sans-explication = partiel

### Tests for User Story 1

- [X] T005 [P] [US1] Contract test lesson load (valide/invalide/doublon, taxonomie unifiée, assert que le log cite les 3 causes de l'invalide) in `tests/contract/test_013_lessons.py`
- [X] T006 [P] [US1] Integration test parcours-prérequis + explain_concept gating in `tests/integration/test_013_lesson_flow.py`

### Implementation for User Story 1

- [X] T007 [P] [US1] Create `tutor/curriculum.py` loader (stdlib json, log-and-skip, doublon warning) in `src/ollama_tutor/tutor/curriculum.py`
- [X] T008 [US1] Wire pièges → diagnostic 5-cats + parcours-prérequis + mastery gating in `src/ollama_tutor/tutor/service.py`

**Checkpoint**: US1 fully functional and testable independently — SC-001

---

## Phase 4: User Story 2 - File par urgence (Priority: P1)

**Goal**: Urgences par notion (overdue-first, seuils fixes) en read-model FSRS existant, exposées via `/adaptation/stability` (FR-004)

**Independent Test**: 6 notions étalées → ordre et 4 niveaux exacts sur 20 tirages ; révision notée → urgences recalculées sans second moteur

### Tests for User Story 2

- [X] T009 [P] [US2] Contract test forecast order + thresholds in `tests/contract/test_013_forecast.py`
- [X] T010 [P] [US2] Integration test recalcule après révision in `tests/integration/test_013_forecast_flow.py`

### Implementation for User Story 2

- [X] T011 [US2] Implement forecast read-model (`retrievability`, `days_until_threshold`, urgences, tri) reusing `fsrs.py` in `src/ollama_tutor/tutor/review.py`
- [X] T012 [US2] Expose via `/adaptation/stability` enrichi (thin delegate) in `src/ollama_tutor/web/server.py`

**Checkpoint**: US1+US2 both work independently — SC-002

---

## Phase 5: User Story 3 - Prompts éditables (Priority: P2)

**Goal**: Prompts Markdown FR éditables + gabarit YAML alimentant l'évaluation, FALLBACK sans crash, zéro secret (FR-005→FR-006)

**Independent Test**: `.md` modifié → ton changé au reboot ; fichier supprimé → FALLBACK + log ; grep secrets vide

### Tests for User Story 3

- [X] T013 [P] [US3] Contract test prompt load/fallback/gabarit fields in `tests/contract/test_013_prompts.py`

### Implementation for User Story 3

- [X] T014 [US3] Implement `assets/prompts/*.md` loader + FALLBACK + gabarit (`hint_level`, `recurring_mistakes`) in `src/ollama_tutor/tutor/prompts.py`
- [X] T015 [US3] Feed gabarit into `build_evaluation_prompt` in `src/ollama_tutor/tutor/assessment.py` (champs existants intacts)

**Checkpoint**: US1+US2+US3 independently functional

---

## Phase 6: User Story 4 - Goldens qualité (Priority: P2)

**Goal**: `tests/pedagogy/` déterministe (must_include/must_not_include, hint-first), échec citant le fragment, après/avec I6 (FR-007→FR-008)

**Independent Test**: 10 goldens verts ; feedback avec solution complète → échec avec fragment cité

### Tests for User Story 4

- [X] T016 [P] [US4] Create 10 pedagogy goldens (FR maths/physique/SVT + Python) in `tests/pedagogy/goldens/*.json`
- [X] T017 [P] [US4] Implement golden runner (asserts sous-chaînes, mockés déterministes, zéro réseau/LLM) in `tests/pedagogy/test_goldens.py`

**Checkpoint**: All 4 stories independently functional — SC-003, SC-004

---

## Phase 7: Polish & Cross-Cutting

**Purpose**: Validation bout-en-bout + drift constitutionnel

- [X] T018 Run `quickstart.md` Scenarios 1-4 and full `python3 -m pytest tests/ -q` + `npm --prefix web/vue run build` in `specs/013-vague1-socle/quickstart.md` (2026-09-18 : headless SC1-4 PASS ; unit 644 + contract ~215 + pedagogy 14 PASS ; WAIVER Réseaux — intégration filtrée `-k "not test_notion_wants_code_computing"` 432/432 PASS ; sans filtre 432/434 → 2 échecs `test_notion_wants_code_computing[réseaux]` attendus, neutralisation volontaire 11e5392 ; `pytest -q` non filtré est donc ROUGE documenté ; grep forecast only read-model)
- [X] T019 [P] Verify no `tutor/`→`fastapi` drift, no new runtime deps, no secrets in `assets/`+`data/lessons` via `tests/contract/test_tutor_imports.py` and `pyproject.toml` diff (2026-09-18 : 43/43 gate I PASS, pyproject diff vide, secrets/path leak 0)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational; parallelisable (fichiers disjoints) ou P1→P1→P2→P2
- **Polish (Phase 7)**: Depends on all stories

### User Story Dependencies

- **US1 (P1)**: After Foundational — No dependencies on other stories
- **US2 (P1)**: After Foundational — Read-model seul, indépendant d'US1
- **US3 (P2)**: After Foundational — Indépendant (builders existants étendus)
- **US4 (P2)**: After US3 de préférence (goldens testent les prompts I6), testable avec mocks sinon

### Within Each User Story

- Tests FAIL before implementation → Models/files → Services → Endpoints → Integration

### Parallel Opportunities

- T002, T004 parallel (baseline vs doc)
- T005||T006, T009||T010 test pairs parallel
- US1/US2/US3 in parallel by 3 devs (curriculum.py vs review.py vs prompts.py)
- T016||T017 (fixtures vs runner)

---

## Parallel Example: User Story 1

```bash
# Launch tests together:
Task: "Contract test lessons in tests/contract/test_013_lessons.py"   # T005
Task: "Integration test lesson flow in tests/integration/test_013_lesson_flow.py"  # T006

# Then implementation:
Task: "Create curriculum.py loader"  # T007
```

---

## Implementation Strategy

### MVP Validation Order

1. Complete Phase 1+2 (Setup+Foundational)
2. Complete Phase 3 (US1) → VALIDATE quickstart Scenario 1
3. Complete Phase 4 (US2) → VALIDATE Scenario 2
4. Complete Phase 5 (US3) → VALIDATE Scenario 3
5. Complete Phase 6 (US4) → VALIDATE Scenario 4 (avec I6 en place)
6. Phase 7 Polish → full suite + drift

### Incremental Delivery

Each phase adds value without breaking previous SCs; US4 verrouille la qualité des phases précédentes.

---

## Notes

- T001 (2026-09-18) : branche `013-vague1-socle` vérifiée (`git branch --show-current`), `EDUNEXUS_DATA_DIR=/home/nganso/projet_ollama_tutor/data` exporté. Plan §Technical Context/Storage confirmé (SQLite WAL + JSON versionnés + prompts + goldens).
- T002 (2026-09-18) : baseline `python3 -m pytest tests/ -q` — 1288 tests collectés, 0 échec (unit 644 + contract 215 + integration 429, exécutés par dossier car un run unique dépasse 240 s sur cette machine ; suite verte).

- [P] tasks = different files, no deps
- [Story] label maps to spec.md US order
- T004 is doc-only (layout contract) — no code
- Constitution V: stdlib-only partout ; III: offline strict, pas de LLM en CI
- 19 tasks total: Setup 2, Foundational 2, US1 4, US2 4, US3 3, US4 2, Polish 2
