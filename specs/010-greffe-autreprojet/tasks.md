# Tasks: Greffe des modules `autreprojet` (010)

**Input**: Design documents from `/specs/010-greffe-autreprojet/` (spec.md + clarifications 2026-09-07, plan.md, research.md, data-model.md, contracts/, quickstart.md, deepdive.md)

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED — exigés par NFR-002 (tout correctif/fonctionnalité couverte, tests offline `httpx.MockTransport`, `@pytest.mark.asyncio` explicite). Les tâches de test s'écrivent et ÉCHOUENT avant l'implémentation.

**Organization**: par lot/user story (P0-A → P0-B → P1-A → P1-B → P2), chaque phase testable indépendamment (`venv/bin/pytest tests/ -q` verte + quickstart du lot).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallélisable (fichiers différents, pas de dépendance)
- **[Story]**: [US1]..[US7]

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Branche + baseline verte + inventaire des sources à porter

- [X] T001 Create branch 010-greffe-autreprojet from main (git checkout -b)
- [X] T002 [P] Validate baseline suite green via venv/bin/pytest tests/ -q (record count/time)
- [X] T003 [P] Inventory port sources in autreprojet/ (verify paths: runner.py, safety.py, exercises.py, router.py, embedding/registry.py, pipeline.py, upload_processing.py, models/ingestion.py, test_contract_coverage.py, fsrs.py, teaching.md)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Gates d'architecture avant tout lot — BLOQUE toutes les stories

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Verify tutor/ import contract via tests/contract/test_tutor_imports.py (Constitution I gate)
- [ ] T005 [P] Snapshot benchmark.sh baseline (gate for P0-B if client.py touched) in ./benchmark.sh output

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - P0-A Sandbox Python (Priority: P0) 🎯 MVP

**Goal**: `grade_answer` accepte en opt-in une preuve d'exécution sandboxée (FR-001/002/003)

**Independent Test**: `venv/bin/pytest tests/unit/test_safety.py tests/unit/test_sandbox.py tests/unit/test_grade_harness.py -q` green + quickstart §1 scenarios (`print(2+2)` → `4 0 False False`, `import socket` → blocked)

### Tests for User Story 1 (write FIRST, ensure FAIL)

- [X] T006 [P] [US1] Safety scanner tests in tests/unit/test_safety.py (blocked imports/calls, strict mode, syntax_error non-blocking)
- [X] T007 [P] [US1] Sandbox runner tests in tests/unit/test_sandbox.py (echo, timeout kill, 32Ko truncation, blocked, empty env, 0700 cwd)
- [X] T008 [P] [US1] Grade harness tests in tests/unit/test_grade_harness.py (visible/hidden split, sentinel parse, program-did-not-reach-tests)

### Implementation for User Story 1

- [X] T009 [P] [US1] Create safety scanner in src/ollama_tutor/tutor/safety.py (adapt autreprojet safety.py: BLOCKED_MODULES, WARN_MODULES, _DANGEROUS_CALLS, TUTOR_STRICT_IMPORTS)
- [X] T010 [P] [US1] Create sandbox runner in src/ollama_tutor/tutor/sandbox.py (adapt runner.py: run_python, RunResult, RunnerError, _safe_env, rlimits, timeouts)
- [X] T011 [US1] Wire execution evidence into grade_answer in src/ollama_tutor/tutor/assessment.py (execution: RunResult | None = None, FR-020 hook; depends on T009, T010)
- [X] T012 [US1] Expose execution param through TutorService.grade_answer in src/ollama_tutor/tutor/service.py (depends on T011)
- [X] T013 [US1] Run quickstart.md §1 P0-A validation scenarios

**Checkpoint**: US1 fully functional and testable independently (grading with/without execution, suite green)

---

## Phase 4: User Story 2 - P0-B Registre providers (Priority: P0)

**Goal**: `ProviderRegistry` + fallback + circuit breaker + modes d'embedding auto/eager/skip, CPU-only, sans toucher au contrat client.py (FR-004/005, NFR-004/006)

**Independent Test**: `venv/bin/pytest tests/unit/test_provider_registry.py tests/unit/test_embedding_modes.py -q` green + CPU check (`import sentence_transformers` fails, resolved mode is skip/CPU provider) + `./benchmark.sh` if client.py touched

### Tests for User Story 2 (write FIRST, ensure FAIL)

- [X] T014 [P] [US2] Registry unit tests in tests/unit/test_provider_registry.py (fallback chain, unhealthy skip, empty→LLMConfigurationError, large/small/fast variants, ping_all, MockLLMClient)
- [X] T015 [P] [US2] Embedding modes tests in tests/unit/test_embedding_modes.py (auto/eager/skip, NoOp zeros, Fallback chain, dimension auto-detect)

### Implementation for User Story 2

- [X] T016 [P] [US2] Create LLM registry in src/ollama_tutor/tutor/providers/registry.py (ProviderRegistry, CircuitBreakerMixin, LLMClient ABC, MockLLMClient; NO sentence-transformers)
- [X] T017 [P] [US2] Create embedding modes in src/ollama_tutor/tutor/providers/embedding_registry.py (auto/eager/skip, NoOpEmbeddingProvider, FallbackEmbeddingProvider)
- [X] T018 [US2] Wire registry over existing backends in src/ollama_tutor/tutor/providers/__init__.py (Ollama/GGUF lazy adapters, default auto→skip on CPU box; depends on T016, T017)
- [X] T019 [US2] Run benchmark.sh gate + CPU no-torch check per quickstart.md §2 (benchmark: client.py untouched → gate N/A; CPU check done)

**Checkpoint**: US1 AND US2 both work independently (fallback demo, skip mode, suite green)

---

## Phase 5: User Story 3 - P1-A Ingestion à jobs (Priority: P1)

**Goal**: Import async avec job suivi + polling (FR-006, clarification async+polling)

**Independent Test**: `venv/bin/pytest tests/integration/test_ingestion_jobs.py -q` green + quickstart §3 (job_id immédiat, phases monotones, dedup → completed/0 nodes, failure → error_message + errors.log)

### Tests for User Story 3 (write FIRST, ensure FAIL)

- [X] T020 [P] [US3] Ingestion jobs integration tests in tests/integration/test_ingestion_jobs.py (lifecycle, sha256 dedup, failure path, idempotent migration)

### Implementation for User Story 3

- [X] T021 [US3] Add ingestion_jobs table + idempotent migration in src/ollama_tutor/tutor/store.py (PRAGMA table_info style, E-006 columns)
- [X] T022 [US3] Add async import pipeline in src/ollama_tutor/tutor/service.py (import_job returns job_id, background task, phase transitions; import_and_index compat kept; depends on T021)
- [X] T023 [US3] Add polling routes in src/ollama_tutor/web/server.py (GET /api/ingestion/jobs and /{id}; depends on T022)
- [X] T024 [US3] Display job status in web/static/tutor.html (phase_label + progress) + node --check (depends on T023)
- [X] T025 [US3] Run quickstart.md §3 P1-A validation

**Checkpoint**: US1-3 independently functional (import→job→completed, suite green)

---

## Phase 6: User Story 4 - P1-B Contract test web (Priority: P1)

**Goal**: Garde-fou fetch() vs routes avec allowlist motivée (FR-007 clarifié)

**Independent Test**: `venv/bin/pytest tests/contract/test_web_contract.py -q` green on current tree + RED when a fake fetch() is injected (quickstart §4)

- [X] T026 [US4] Create web contract test in tests/contract/test_web_contract.py (fetch() scan of tutor.html, route map of server.py, zero-tolerance + motivated allowlist, forbidden legacy patterns)
- [X] T027 [US4] Fix or allowlist orphans in web/static/tutor.html and src/ollama_tutor/web/server.py (depends on T026)
- [X] T028 [US4] Run quickstart.md §4 P1-B validation (fake endpoint → red)

**Checkpoint**: All P0/P1 stories independently functional

---

## Phase 7: User Story 5 - P2 Adaptatif (Priority: P2)

**Goal**: FSRS réel, classification 0-LLM, diagnostic 5 catégories, mémoire compacte, blocs simplifiés, mini-diagnostic (deepdive §5 items 1-6, 13)

**Independent Test**: `venv/bin/pytest tests/unit/test_p2_adaptive.py -q` green (FSRS vectors, classifier fixtures, memory consolidate overlap cases)

### Tests for User Story 5 (write FIRST, ensure FAIL)

- [X] T029 [P] [US5] P2 adaptive tests in tests/unit/test_p2_adaptive.py (FSRS stability, filename/content heuristics, memory overlap+decay)

### Implementation for User Story 5

- [X] T030 [P] [US5] Create FSRS engine in src/ollama_tutor/tutor/fsrs.py (adapt DEFAULT_W/FSRSCard, stdlib math/dataclasses only)
- [X] T031 [US5] Wire FSRS into spaced repetition in src/ollama_tutor/tutor/review.py (FSRS path added, SM-2 legacy kept; depends on T030)
- [X] T032 [P] [US5] Add 0-LLM classification in src/ollama_tutor/tutor/classifier.py (filename regex + content heuristics + stdlib MIME)
- [X] T033 [P] [US5] Add 5-category error diagnosis in src/ollama_tutor/tutor/assessment.py (prompts + JSON parse, derive-clean; QuizEngine opt-in)
- [X] T034 [P] [US5] Add WrongAnswer/PracticeProblem columns in src/ollama_tutor/tutor/store.py (diagnosis enum, knowledge_points)
- [X] T035 [P] [US5] Create compact memory in src/ollama_tutor/tutor/memory.py (consolidate overlap≥0.5+cosine≥0.85, teaching_state)
- [X] T036 [P] [US5] Create simplified block rules in src/ollama_tutor/tutor/blocks.py (4 rules: deadline/forgetting/weak/prereq)
- [X] T037 [US5] Wire mini-diagnostic 5Q in src/ollama_tutor/tutor/service.py (depends on T033, T034)

**Checkpoint**: Adaptive P2 independently testable (FSRS scheduling, diagnosis flow, suite green)

---

## Phase 8: User Story 6 - P2 Robustesse (Priority: P2)

**Goal**: AppError, durcissement uploads, exports stdlib, CI robuste (deepdive §5 items 3, 10, 12, 14)

**Independent Test**: `venv/bin/pytest tests/unit/test_p2_robustness.py -q` green + `./scripts/smoke_010.sh` passes

### Tests for User Story 6 (write FIRST, ensure FAIL)

- [X] T038 [P] [US6] Robustness tests in tests/unit/test_p2_robustness.py (AppError mapping, upload caps, TSV/ICS goldens)

### Implementation for User Story 6

- [X] T039 [US6] Create error taxonomy in src/ollama_tutor/tutor/errors.py (AppError + handlers, adapt OpenTutor libs/exceptions.py)
- [X] T040 [US6] Wire error handlers in src/ollama_tutor/web/server.py (depends on T039)
- [X] T041 [P] [US6] Harden upload path in src/ollama_tutor/tutor/service.py (64Ko chunks, early 413, ownership check)
- [X] T042 [P] [US6] Create stdlib exporters in src/ollama_tutor/tutor/exporters.py (TSV Anki-style, ICS template — no genanki/icalendar)
- [X] T043 [P] [US6] Add CI smoke script in scripts/smoke_010.sh (venv rebuild check, ports, goldens)

**Checkpoint**: Robustness P2 independently testable

---

## Phase 9: User Story 7 - P2 Pédagogie/UI (Priority: P2)

**Goal**: Garde-fou citations, feedback standard, HITL minimal, KaTeX+ThinkBox (deepdive §5 items 7-9, 11)

**Independent Test**: `venv/bin/pytest tests/unit/test_p2_pedagogy.py -q` green + tutor.html renders KaTeX + node --check passes

### Tests for User Story 7 (write FIRST, ensure FAIL)

- [X] T044 [P] [US7] Pedagogy tests in tests/unit/test_p2_pedagogy.py (allowlist filter, verdict/next_step format, HITL guards)

### Implementation for User Story 7

- [X] T045 [P] [US7] Create citation guard in src/ollama_tutor/tutor/docs_refs.py (DEFAULT_ALLOWED_HOSTS + lookup, offline-first)
- [X] T046 [US7] Wire citations into retrieval in src/ollama_tutor/tutor/retrieval.py (depends on T045)
- [X] T047 [P] [US7] Standardize evaluation feedback in src/ollama_tutor/tutor/assessment.py (passed/needs_work/error + next_step)
- [X] T048 [US7] Add HITL feedback tables in src/ollama_tutor/tutor/store.py (rating/comment, owner-or-admin guard)
- [X] T049 [US7] Wire HITL feedback in src/ollama_tutor/tutor/service.py (depends on T048)
- [X] T050 [P] [US7] Add KaTeX + ThinkBox in web/static/tutor.html (offline-safe, no CDN required) + node --check
- [X] T051 [US7] Run quickstart.md P2 validation + full suite green

**Checkpoint**: All user stories independently functional

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Final gates + docs

- [X] T052 [P] Mark 010 implemented in specs/010-greffe-autreprojet/plan.md (status + dates)
- [X] T053 [P] Final validation: venv/bin/pytest tests/ -q + ./benchmark.sh (if client.py touched) + node --check on tutor.html
- [X] T054 Security pass (no secrets committed, Origin/Host checks intact, STRICT_IMPORTS review, _log_error coverage)
- [X] T055 Run quickstart.md end-to-end validation scenarios

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories (arch gates)
- **User Stories (Phase 3+)**: All depend on Foundational completion
  - Sequential in priority order recommended (P0-A → P0-B → P1-A → P1-B → P2), each independently shippable
  - US5/US6/US7 (P2) can proceed in parallel once US1-US4 done (different files, no cross-deps)
- **Polish (Phase 10)**: Depends on all desired stories complete

### User Story Dependencies

- **US1 (P0-A)**: After Foundational - no story deps - **MVP** 🎯
- **US2 (P0-B)**: After Foundational - no deps on US1 (registry independent of sandbox)
- **US3 (P1-A)**: After Foundational - uses LibraryStore, independent of US1/US2
- **US4 (P1-B)**: After Foundational - pure test, independent (fix task may touch US3 files → run after US3 if both selected)
- **US5/US6/US7 (P2)**: After Foundational - mutually independent (disjoint files except service.py/store.py/server.py/assessment.py touched in SEQUENTIAL phases, never parallel)

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- New modules before wiring (T009/T010 → T011; T016/T017 → T018; T021 → T022 → T023)
- Story complete (suite green + quickstart) before next priority

### Parallel Opportunities

- T002 + T003 (baseline + inventory, different concerns)
- T006/T007/T008, T009+T010 (US1 tests + new modules)
- T014+T015, T016+T017 (US2 tests + new modules)
- T032/T033/T034/T035/T036 (US5 new modules, disjoint files)
- T041+T042+T043 (US6 disjoint files)
- US5/US6/US7 can run in parallel post-P1 (staffed) — same-file edits are phase-sequential only

---

## Parallel Example: User Story 1

```bash
# Launch US1 tests together:
Task: "Safety scanner tests in tests/unit/test_safety.py" [US1]
Task: "Sandbox runner tests in tests/unit/test_sandbox.py" [US1]
Task: "Grade harness tests in tests/unit/test_grade_harness.py" [US1]

# Launch US1 new modules together (after tests fail):
Task: "Create safety scanner in src/ollama_tutor/tutor/safety.py" [US1]
Task: "Create sandbox runner in src/ollama_tutor/tutor/sandbox.py" [US1]
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T005, arch gates)
3. Complete Phase 3: US1 P0-A Sandbox (T006-T013)
4. **STOP and VALIDATE**: quickstart §1 + full suite green
5. Ship: execution evidence for grading, zero regression risk (opt-in param)

### Incremental Delivery

1. Setup + Foundational → gates pass
2. + US1 (sandbox) → MVP, grading with real evidence
3. + US2 (registry) → provider fallback + CPU-safe embeddings
4. + US3 (jobs) → async imports with polling + UI status
5. + US4 (contract test) → front/back drift locked
6. + US5/US6/US7 (P2) → adaptive, robustness, pedagogy — each shippable alone

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Developer A: US1 → US3 (tutor core chain)
3. Developer B: US2 → US4 (providers + contract test)
4. Developer C: US5/US6/US7 (P2, post-P1)
5. Polish together (gates + security + quickstart)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to user story for traceability (US1=P0-A … US7=P2 pedagogy)
- NFR-006 CPU-only enforced in T019, T016/T017 (no torch), T053
- Commit after each task or logical group; stop at any checkpoint to validate
- Same-file edits across stories are sequential by phase (service.py: T012→T022→T041→T049; store.py: T021→T034→T048; assessment.py: T011→T033→T047)
