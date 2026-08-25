---

description: "Task list for Local AI Tutor & Personal Learning System"
---

# Tasks: Local AI Tutor & Personal Learning System (004-local-ai-tutor)

**Input**: Design documents from `/specs/004-local-ai-tutor/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅ (D1–D14), data-model.md ✅, contracts/ ✅ (core API, REST, WS), quickstart.md ✅

**Tests**: Test tasks ARE included because `quickstart.md` (feature validation playbook) explicitly names the test files (`test_chunker.py`, `test_vector_index.py`, `test_tutor_imports.py`, `test_tutor_rest.py`, `test_ws_tutor.py`, `test_tutor_flow.py`) as regression gates, consistent with the repo's offline-test invariant. All tests are OFFLINE (MockTransport / tmp dirs / fake runners) — never point tests at a real Ollama daemon.

**Organization**: Tasks grouped by user story (spec.md priorities). Imports use `from src.ollama_tui...` in tests ONLY; all source code under `src/ollama_tui/` uses relative imports.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1…US8 per spec.md)
- Include exact file paths in descriptions

## Path Conventions

Single src-layout package (per plan.md): `src/ollama_tui/` + `tests/{unit,contract,integration}/` at repository root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies and package skeleton

- [X] T001 [P] Add `numpy>=1.26` and `pypdf>=4.0` to base dependencies in pyproject.toml (research D12) and run `venv/bin/pip install -e ".[dev,web]"` to refresh the venv
- [X] T002 Create package skeleton `src/ollama_tui/tutor/__init__.py` with docstring "UI-agnostic local AI tutor core (no textual/fastapi imports)" and empty public-export list

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Create dataclass entities per data-model.md in src/ollama_tui/tutor/models.py: Subject, Book, Chunk, Concept, SkillProgress, Exercise, ExerciseAttempt, Flashcard, ReviewItem, Quiz, QuizQuestion, QuizAnswer, ExamSession, TutoringSession, SessionSummary, GlossaryTerm, KnowledgeRelation — each with `to_dict()`/`from_dict()` mirroring core/projects.py conventions
- [X] T004 Implement LibraryStore in src/ollama_tui/tutor/store.py: SQLite WAL database at `<config_dir>/tutor/library.db`, full schema per data-model.md (subjects, books, subject_books, chunks, embeddings cache keyed by text_hash+model, concepts, progress, exercises, exercise_attempts, flashcards UNIQUE(subject_id,source_hash), review_schedule, quizzes, quiz_questions, quiz_answers, tutoring_sessions, session_summaries, glossary_terms, knowledge_relations) with ON DELETE CASCADE from subjects, plus subject CRUD/select/active and concept upsert/list methods (contracts/tutor-core-api.md)
- [X] T005 [P] Add `tutor.*` config properties to src/ollama_tui/config.py following the existing `agent.*` nested-dict pattern: enabled(False), embedding_model("embeddinggemma"), tutor_model("gemma4:e2b"), socratic(True), level("intermediate"), think(False), top_k(5), whisper_binary(""), whisper_model("") — clamped/validated like agent properties
- [X] T006 [P] Add non-streaming `async def embed(self, model, inputs: list[str]) -> list[list[float]]` method to OllamaClient in src/ollama_tui/client.py calling POST `/api/embed` (JSON response `{embeddings: [[...]]}`), reusing `_get_client()`, raising OllamaConnectionError/OllamaAPIError like chat_stream
- [X] T007 [P] Add `create_embed_transport(vectors: list[list[float]]) -> httpx.MockTransport` factory to tests/conftest.py returning a JSON `{embeddings: vectors}` response for POST /api/embed (offline mocking per research D14)
- [X] T008 [P] Create purity-lint contract test tests/contract/test_tutor_imports.py mirroring tests/contract/test_core_imports.py: assert no module under src/ollama_tui/tutor/ contains `import textual|from textual|import fastapi|from fastapi`
- [X] T009 Create unit tests tests/unit/test_library_store.py covering: schema creation on empty dir, subject create/rename/delete cascade, duplicate subject name rejection, book row status lifecycle fields, flashcard source_hash uniqueness — all against a tmp_path config_dir

**Checkpoint**: Foundation ready — `venv/bin/pytest tests/unit/test_library_store.py tests/contract/test_tutor_imports.py -q` green. User story implementation can begin.

---

## Phase 3: User Story 1 — Build a personal learning library (Priority: P1) 🎯 MVP

**Goal**: Learner creates subjects, imports TXT/MD/PDF/EPUB books; documents are fingerprinted, chunked with metadata, embedded once in a cancellable background task with visible progress; duplicates are no-ops.

**Independent Test**: Import two books into a new subject → rows reach `status="done"` with chunk counters filled; re-import the same file → immediate no-op (fingerprint hit, zero new embeddings); remove a book → its chunks vanish. Fully verifiable offline via MockTransport embeds.

### Tests for User Story 1

- [X] T010 [P] [US1] Create unit tests tests/unit/test_extractors.py: chunker respects max_chars (default 1200) + overlap (default 200), never splits a word across chunk boundary, TXT/MD passthrough, PDF via pypdf returns non-empty text, EPUB via zipfile+xml.etree+html.parser strips tags to text (tmp_path, offline)
- [X] T011 [P] [US1] Create contract tests tests/contract/test_tutor_rest_api.py: using FastAPI TestClient, assert /api/tutor/* endpoints exist and return the shapes in contracts/tutor-rest-api.md (import, list books, delete book, search, index-status) - stub TutorService or real one with mocked client (offline)

### Implementation for User Story 1

- [X] T012 [US1] Implement text extraction + chunking in src/ollama_tui/tutor/extractors.py per research D3: `extract_text(path, fmt=None)` for txt/md/pdf(pypdf)/epub(zipfile+xml.etree+html.parser tag-strip); `chunk_text(text, max_chars=1200, overlap=200)` word-boundary; `fingerprint(text)` sha256 of normalized text
- [X] T013 [US1] Implement embeddings in src/ollama_tui/tutor/embeddings.py per research D4: `NumpyVectorIndex` (cosine over float32 numpy, add/search/invalidate) implementing the VectorIndex protocol; `embed_texts(client, model, chunks, store)` batched via client.embed, hash-keyed cache via store.get_embedding/add_embedding
- [X] T014 [US1] Extend src/ollama_tui/tutor/store.py with indexing methods: `mark_indexing`, `mark_indexed(book_id, chunk_count)`, `set_book_error`, `get_book_status`, `get_embedding(text_hash, model)`/`add_embedding` cache, `add_chunks(subject_id, book_id, chunks, embeddings, model)`, `get_indexed_chunks`, `list_all_books`, `delete_book` (cascade); `import_document` computes sha256 fingerprint, no-op on duplicate
- [X] T015 [US1] Implement `TutorService.import_and_index(subject_name, path, fmt=None, background=True)` in src/ollama_tui/tutor/service.py per research D6: extract->chunk->embed->add_chunks->mark_indexed pipeline, daemon-thread background, cancellation via store.cancel_indexing, fail-closed error capture on book row
- [X] T016 [US1] Add REST routes to src/ollama_tui/web/server.py per contracts/tutor-rest-api.md: POST /api/tutor/import, GET /api/tutor/books, DELETE /api/tutor/book/{id}, POST /api/tutor/search, GET /api/tutor/index-status; GET /tutor gated on config.tutor_enabled (mirror /agent)
- [X] T017 [US1] Create src/ollama_tui/web/static/tutor.html library view: subject input, import form (file + subject), book table with status badges + chunk counts + delete, search box, status indicator - served behind GET /tutor gated on config tutor.enabled
- [X] T018 [P] [US1] Add offline integration test tests/integration/test_tutor_import_dedup.py: import a book -> status indexed with chunk_count>0 and embeddings cached; re-import same file -> no-op (zero new embed calls); delete book cascades chunks (MockTransport, tmp_path)

**Checkpoint**: User Story 1 fully functional and independently testable (library management works end-to-end without any chat).

---

## Phase 4: User Story 2 — Ask questions answered from your own books (Priority: P1)

**Goal**: Natural-language question → subject-scoped semantic search → streamed grounded answer citing book/chapter/page, sources frame first; other subjects never searched.

**Independent Test**: With indexed books in subject A, ask a question over /ws/tutor → `sources` frame precedes `content_delta`s and cites imported books; switching active subject to B yields no A passages. Offline-verifiable with scripted embed+chat transports.

### Tests for User Story 2

- [X] T019 [P] [US2] Create unit tests tests/unit/test_vector_index.py: NumpyVectorIndex cosine ranking on synthetic vectors, k-limit and score-floor filtering, subject isolation (matrix for A never returns B rows), invalidation after writes
- [X] T020 [P] [US2] Create contract tests tests/contract/test_ws_tutor.py: origin/host guard closes 1008, `ask` produces start→sources→content_delta*→end ordering, busy second ask → error{code:"busy"}, cancel → cancelled+end(stopped), no_passages path emits notice frame (contracts/tutor-ws-protocol.md)

### Implementation for User Story 2

- [X] T021 [US2] Implement src/ollama_tui/tutor/vector.py (VectorIndex protocol + NumpyVectorIndex lazy per-subject matrix cache, research D1) and src/ollama_tui/tutor/retrieval.py (embed question via EmbeddingService, search top_k with floor 0.25, assemble ≤6000-char context blocks prefixed `[Livre X — chapitre Y, p. Z]`)
- [X] T022 [US2] Implement base tutor persona in src/ollama_tui/tutor/prompts.py (system prompt encoding FR-012 behaviors, citation discipline, "not grounded" honesty rule) and `TutorService.ask(...)` in service.py: sources-first TutorEvent stream, streaming passthrough of chat_stream events, tutoring_sessions row creation/binding, num_ctx floored at 8192, bounded 6-turn window
- [X] T023 [US2] Add `/ws/tutor` WebSocket endpoint to src/ollama_tui/web/server.py per contracts/tutor-ws-protocol.md: same-origin upgrade guard, ask mode, one-active-run invariant, safe_send + _log_error patterns, end frames with session_id
- [X] T024 [US2] Add chat view to src/ollama_tui/web/static/tutor.html: question input, streaming answer area shared renderer (thinking_delta/content_delta/stats/end), sources panel populated from sources frame, subject switcher enforcing active-subject scope
- [X] T025 [US2] Add offline integration test tests/integration/test_tutor_flow.py::test_grounded_ask_isolation: seeded two-subject store, scripted embed/chat transports, assert sources cite only active-subject books and delta ordering holds

**Checkpoint**: User Stories 1 AND 2 work independently — grounded Q&A demonstrable end-to-end.

---

## Phase 5: User Story 3 — A tutor that teaches instead of just answering (Priority: P1)

**Goal**: Socratic mode toggle, four-level adaptation, per-request level overrides, think-mode off by default with per-request override (FR-013/FR-014/FR-045).

**Independent Test**: Same question asked with socratic on vs off produces prompt streams asserting guiding-question directive vs direct-answer directive; level field changes prompt depth directives; think defaults false and frame override forwards true.

### Tests for User Story 3

- [X] T026 [P] [US3] Create unit tests tests/unit/test_prompts.py: system-prompt matrix (socratic×level) contains expected directives, context budget respected, think flag default False and forwarded to chat_stream payload

### Implementation for User Story 3

- [X] T027 [US3] Extend src/ollama_tui/tutor/prompts.py with socratic variant (guide-by-questions-first contract, example escalation) and level variants (beginner/intermediate/advanced/expert depth directives) plus in-conversation override handling ("explain like I'm a beginner")
- [X] T028 [US3] Wire toggles through src/ollama_tui/tutor/service.py `ask(...)`: per-request think/socratic/level params defaulting to config values (research D10), thinking_delta passthrough only when enabled
- [X] T029 [US3] Expose frame fields in the /ws/tutor handler (src/ollama_tui/web/server.py) and add mode toggles (Socratic On/Off, Niveau selector, Think checkbox) to src/ollama_tui/web/static/tutor.html chat view

**Checkpoint**: P1 trio complete — the product philosophy (teach, don't just answer) is demonstrable.

---

## Phase 6: User Story 4 — Practice: exercises, hints, code analysis, skill tracking (Priority: P2)

**Goal**: Generated exercises with escalating hints, explicit-only solutions, code analysis without wholesale replacement, continuous mastery updates, gap detection, editable learning path (FR-016→FR-023).

**Independent Test**: Generate a medium exercise, answer wrong twice → hint_level escalates 1→2 with no solution leak; request solution explicitly → returned; three consecutive failures flag the concept in /api/tutor/subjects/{id}/gaps; mastery score moves by D7 weights.

### Tests for User Story 4

- [X] T030 [P] [US4] Create unit tests tests/unit/test_mastery.py: D7 weight application per event type, clamping 0–100, label thresholds (non étudié/faible/moyen/maîtrisé), gap detection rule (3 consecutive incorrect ⇒ flagged), path_rank reordering

### Implementation for User Story 4

- [X] T031 [US4] Implement src/ollama_tui/tutor/progress.py: record_progress deltas per D7, get_progress with derived labels, gap detection query, learning-path reorder (FR-021/FR-022/FR-023)
- [X] T032 [US4] Implement exercise generation + hint ladder in src/ollama_tui/tutor/assessment.py: generate_exercise(concept, difficulty, level, past_errors context from attempts), pre-generated 3-stage hints JSON so hints need no extra LLM call, solution withheld (FR-016/FR-017)
- [X] T033 [US4] Implement grading in assessment.py + service.py: grade_answer verdict via LLM with structured verdict format, attempt recording, hint escalation 0→3, explanation stage, request_solution explicit gate (invariant 3 of contracts/tutor-core-api.md), progress updates on verdicts
- [X] T034 [US4] Implement `analyze_code` streaming path in src/ollama_tui/tutor/service.py: error-category taxonomy prompt (syntax/runtime/logic/practice/design/misconception per FR-019), optional execution-result context hook parameter (FR-020 stub)
- [X] T035 [US4] Add practice REST routes to src/ollama_tui/web/server.py per contracts/tutor-rest-api.md: POST exercises, POST answers, POST solution (explicit only), GET progress/gaps, PUT path
- [X] T036 [US4] Add practice view to src/ollama_tui/web/static/tutor.html: concept picker, difficulty selector, exercise card, hint button revealing current stage, answer box (code-aware textarea), solution-on-request button, mini progress bars per notion

**Checkpoint**: Practice loop measurable — wrong answers visibly lower mastery and trigger remediation offers.

---

## Phase 7: User Story 5 — Revision: flashcards, spaced repetition, quizzes, exam mode (Priority: P2)

**Goal**: Idempotent knowledge preparation (flashcards/glossary/concepts/relations), ladder-based due reviews with zero LLM calls, multi-type quizzes, assistance-free timed exams with strengths/weaknesses report (FR-024→FR-027).

**Independent Test**: Run prepare twice → second run reports only skipped items; due-review listing is instant SQL; grade success/failure walks the [1,2,5,12,30]-day ladder; exam refuses hint routes (409) and auto-completes on expiry scoring unanswered as incorrect.

### Tests for User Story 5

- [X] T037 [P] [US5] Create unit tests tests/unit/test_review_ladder.py: streak_index→interval mapping [1,2,5,12,30], failure reset to index 0, next_due date arithmetic, due query filter — zero model calls asserted (SC-008)

### Implementation for User Story 5

- [X] T038 [US5] Implement src/ollama_tui/tutor/review.py: ladder scheduler per D8, due_reviews(subject_id) pure SQL, grade_review updating streak/next_due/last_result
- [X] T039 [US5] Implement `prepare_knowledge(subject_id)` in src/ollama_tui/tutor/service.py per FR-024/FR-034: derive candidate concepts from chunk chapters/sections, LLM-generate flashcards (question/answer/level) and glossary definitions in bounded batches, skip existing via source_hash/term uniqueness, emit PrepareReport{new,skipped}, populate knowledge_relations from repeated co-chapter concepts (FR-035 seed)
- [X] T040 [US5] Implement quiz/exam engine in src/ollama_tui/tutor/assessment.py: create_quiz/create_exam generating questions (mcq/true_false/open/matching/code) bound to concepts, submit_answers correction (exact for objective types, LLM-judged for open/code), exam rules enforcement (no-hint gate, time_limit_s expiry auto-submit per edge cases), QuizReport strengths/weaknesses + progress updates
- [X] T041 [US5] Add revision REST routes to src/ollama_tui/web/server.py per contracts/tutor-rest-api.md: prepare, reviews/due, reviews grade, quizzes/exams create/submit/get
- [X] T042 [US5] Add revision + exam views to src/ollama_tui/web/static/tutor.html: prepare button with report toast, due-review card flow (show → self-grade), quiz runner, exam runner with countdown timer, help buttons disabled, results screen with score + strengths/weaknesses

**Checkpoint**: Retention loop closed — cards, schedule, quizzes and exams all feed progression.

---

## Phase 8: User Story 6 — Learning memory and session continuity (Priority: P2)

**Goal**: End-of-session summaries persisted; resume briefing recalls last topic + difficulties; progression view distinct from book content (FR-028→FR-030).

**Independent Test**: Close a session with mixed-results history → SessionSummary persisted with studied/mastered/to_review; GET resume returns accurate briefing referencing that summary without any model call for recall assembly.

### Implementation for User Story 6

- [X] T043 [US6] Implement `close_session(session_id)` in src/ollama_tui/tutor/service.py: aggregate session transcript + attempts + reviews into SessionSummary (studied/mastered/to_review notions), persist to session_summaries, close session row (FR-028)
- [X] T044 [US6] Implement `resume_briefing(subject_id)` in src/ollama_tui/tutor/service.py: last summary + open gaps → ResumeBriefing{last_topic, difficulties, proposal} (FR-029), pure data assembly
- [X] T045 [US6] Add session REST routes (POST /api/tutor/sessions/{id}/close, GET /api/tutor/subjects/{id}/resume, GET sessions list) to src/ollama_tui/web/server.py and a history/resume panel to src/ollama_tui/web/static/tutor.html showing past summaries and the briefing banner

**Checkpoint**: Continuity demonstrable — leave and return, the tutor remembers.

---

## Phase 9: User Story 7 — Navigate and compare book knowledge (Priority: P3)

**Goal**: Locate notion across books with page refs, rank books, multi-book comparison synthesis, glossary lookup, knowledge map from relations (FR-031→FR-035).

**Independent Test**: locate returns book/chapter/page rows from the index without an LLM call; compare streams a synthesis citing ≥2 books and flags divergences when prompted to; map endpoint returns nodes+edges assembled from stored relations.

### Implementation for User Story 7

- [X] T046 [US7] Implement locate/rank in src/ollama_tui/tutor/retrieval.py: `locate(notion)` top-N scored chunks grouped by book/chapter/page (no LLM), `rank_books` aggregating scores per book (FR-031/FR-032)
- [X] T047 [US7] Implement compare mode in src/ollama_tui/tutor/service.py ask(mode="compare"): retrieve per-book passages, synthesis prompt requiring per-source citations and a differences section (FR-033)
- [X] T048 [US7] Implement glossary explain + knowledge map assembly in src/ollama_tui/tutor/service.py: term lookup from glossary_terms with on-demand explanation via ask pipeline scoped to term provenance chunks; map endpoint returning {nodes, edges} from knowledge_relations (FR-034/FR-035)
- [X] T049 [US7] Add tools REST routes (locate, rank-books, compare via WS mode, glossary, map) to src/ollama_tui/web/server.py and a "Dans mes livres" panel + glossary tab + SVG/DIV knowledge-map rendering to src/ollama_tui/web/static/tutor.html

**Checkpoint**: Library navigation tools usable on top of the core loop.

---

## Phase 10: User Story 8 — Talk to the tutor (Priority: P3)

**Goal**: Spoken question transcribed locally by whisper.cpp (`ggml-base-q5_1.bin`), transcript confirmed, then standard grounded flow (FR-036, US8).

**Independent Test**: POST transcribe with a small WAV via a fake runner returns scripted transcript text; unconfigured binary ⇒ error{code:"voice_disabled"}; transcript echo frame precedes any ask.

### Tests for User Story 8

- [X] T050 [P] [US8] Add voice contract tests to tests/contract/test_ws_tutor.py: transcribe with monkeypatched runner returns transcript frame, missing config ⇒ voice_disabled error, oversized/invalid audio ⇒ 400-class error frame

### Implementation for User Story 8

- [X] T051 [US8] Implement src/ollama_tui/tutor/voice.py WhisperTranscriber per research D4: `available` property (binary+model configured/executable), `transcribe_wav(wav_path)` running configurable CLI `[binary, -m, model, -f, wav, -nt, -l, fr, -otxt]` via injectable runner (default asyncio.create_subprocess_exec), VoiceError on failure
- [X] T052 [US8] Wire `transcribe` frames into /ws/tutor handler in src/ollama_tui/web/server.py: decode base64 WAV to temp file under `<config_dir>/tutor/tmp/`, invoke transcriber, emit transcript echo frame, cleanup temp files, busy-guard shared with ask
- [X] T053 [US8] Add record button to src/ollama_tui/web/static/tutor.html: MediaRecorder 16 kHz mono PCM capture, WAV header wrap, base64 send, transcript confirmation step (edit/send), then normal ask submission

**Checkpoint**: Full feature surface complete including voice.

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Regression gates and final validation

- [X] T054 Rerun throughput gate `./benchmark.sh` (client.py was modified in T006) and record results; investigate any regression vs baseline before proceeding
- [ ] T055 Run full offline suite `venv/bin/pytest tests/ -q` green and execute quickstart.md Part A checklist end-to-end; fix any fallout
- [ ] T056 Verify resource constraints manually per quickstart Part B spot-checks on target hardware: background indexing keeps UI responsive, resident memory stays within 8 GB envelope, search <2 s over a ≥10-book subject

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none — start immediately
- **Foundational (Phase 2)**: depends on Phase 1 — **BLOCKS all user stories**
- **US1→US2→US3 (Phases 3–5)**: sequential recommended; US2 needs US1's indexed chunks to be meaningfully testable live, US3 extends US2's ask pipeline
- **US4–US8 (Phases 6–10)**: each depends only on Foundational + the US2 ask pipeline primitives they reuse; may proceed in parallel after Phase 5 if staffed
- **Polish (Phase 11)**: after all desired stories

### User Story Dependencies

- **US1**: Foundational only
- **US2**: Foundational + US1 store/indexing surfaces (reads chunks)
- **US3**: US2 (extends ask)
- **US4**: Foundational + US2 (progress updates hook graded interactions; exercises use ask-style LLM calls)
- **US5**: Foundational + US2 (prepare uses LLM batches; quizzes reuse grading patterns from US4 recommended but not required)
- **US6**: US4 (consumes attempts/progress) — soft dependency, interfaces defined in contracts
- **US7**: US2 (retrieval) + US5 (glossary/relations produced by prepare)
- **US8**: US2 (transcript feeds ask)

### Within Each User Story

Tests first (they must FAIL), then models/services, then endpoints, then UI, then integration verification.

### Parallel Opportunities

- Phase 2: T005, T006, T007, T008 run in parallel (different files)
- Every story's test tasks marked [P] precede parallel-safe implementation
- After Phase 5: US4/US5 lanes are independent (different files) and can proceed simultaneously; US6/US7/US8 likewise once their soft inputs exist
- Single-developer execution: strict ID order is always safe

---

## Parallel Example: after Phase 5 completes

```bash
# Lane A (US4): T030 → T031 → T032 → T033 → T034 → T035 → T036
# Lane B (US5): T037 → T038 → T039 → T040 → T041 → T042   (no file overlap with Lane A except web/server.py routes — merge sequentially)
```

Note: tasks touching `src/ollama_tui/web/server.py` (route additions) must merge sequentially even across parallel lanes; core `tutor/` files never collide across stories.

---

## Implementation Strategy

### MVP First (P1 trio)

1. Phase 1 + Phase 2 → foundation green (checkpoint tests pass)
2. Phase 3 (US1): library + indexing works standalone → validate
3. Phase 4 (US2): grounded Q&A → validate (this is the demo-able core)
4. Phase 5 (US3): pedagogy toggles → validate
5. **STOP**: usable local tutor shipped; remaining stories layer on top without rework

### Incremental Delivery

Each subsequent phase (US4 practice → US5 revision → US6 memory → US7 tools → US8 voice) adds an independently testable increment; every phase ends with its checkpoint tests green and `pytest tests/ -q` passing.

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to spec.md user story for traceability
- All LLM/embed touches go through OllamaClient (transport-injectable) — no test ever starts a daemon
- Never import `from src.ollama_tui...` inside `src/`; tests always do
- Commit after each task or logical group; stop at checkpoints to validate stories independently
