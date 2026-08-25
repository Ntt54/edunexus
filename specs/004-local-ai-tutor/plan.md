# Implementation Plan: Local AI Tutor & Personal Learning System

**Branch**: `004-local-ai-tutor` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-local-ai-tutor/spec.md`

## Summary

Add a local AI tutor to ollama-tui: user-imported books (TXT/MD/PDF/EPUB) are
extracted, fingerprinted, chunked with rich metadata, and embedded once
(EmbeddingGemma via Ollama `/api/embed`) into a SQLite+NumPy store behind a
swappable `VectorIndex` abstraction. A UI-agnostic `tutor/` package serves
subject-scoped semantic retrieval, a pedagogical LLM loop (Gemma 4 E2B,
socratic toggle, level adaptation, think-off fast path), exercises with hint
ladders, code analysis, mastery tracking with gap detection, flashcards +
algorithmic spaced repetition, quizzes/exams, session summaries/resume,
glossary/knowledge-map tools, and whisper.cpp voice input (`ggml-base-q5_1.bin`).
The web GUI gets a `/tutor` page plus REST + dedicated WebSocket surfaces that
delegate entirely to the core services.

## Technical Context

**Language/Version**: Python ≥ 3.11 (repo venv: 3.12), hatchling src-layout

**Primary Dependencies**: existing — httpx, FastAPI+uvicorn ([web] extra),
textual/rich/pygments/Pillow (untouched); **new** — `numpy>=1.26` (vector math),
`pypdf>=4.0` (PDF text extraction). Everything else stdlib (sqlite3, zipfile,
xml.etree, html.parser, wave, hashlib, subprocess) — see research D3/D12.

**Storage**: SQLite `~/.config/ollama-tui/tutor/library.db` (WAL) + float32
embedding BLOBs; shared embedding cache keyed by text hash; tutor keys under
`config.json → "tutor"`; session transcripts under `~/.config/ollama-tui/tutor/sessions/`

**Testing**: pytest (+pytest-asyncio, explicit markers), offline only —
httpx.MockTransport factories extended with an `/api/embed` JSON factory;
whisper subprocess runner injectable; tmp config_dir fixtures

**Target Platform**: Linux desktop, CPU-only, ≤8 GB RAM, fully local
(Ollama on localhost:11434; whisper.cpp binary user-provided)

**Project Type**: desktop app (Textual TUI) + local web service (FastAPI);
feature ships web-GUI-first per clarified scope

**Performance Goals**: subject-scoped search <2 s over dozens of books
(SC-002); first streamed tokens <5 s with think off (SC-003); indexing runs in
background without blocking the UI (FR-004)

**Constraints**: no cloud calls; whole books never enter prompts (top-k 5
chunks ≈ ≤6000 chars, D11); `num_ctx` floored at 8192 for tutor requests;
embeddings computed once (hash-keyed cache); review scheduling does zero LLM
calls; core purity (no textual/fastapi in `core/` **and** `tutor/`)

**Scale/Scope**: single user, dozens of subjects/books, ~30–60 k chunks at
full library size; 45 functional requirements across 8 user stories

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

No `.specify/memory/constitution.md` exists. Gates below are derived from the
binding invariants in AGENTS.md (project rules) — each is satisfied by this
design:

| Gate (AGENTS.md invariant) | Status |
|---|---|
| UI-agnostic services: both frontends delegate to shared core; no textual/fastapi inside `core/` | ✅ new logic lives in `src/ollama_tui/tutor/` (mirrors `agent/`); purity lint extended to cover it (research D13); `web/server.py` only adds thin routes |
| Tests import `from src.ollama_tui...`; src code uses relative imports | ✅ plan follows both rules; quickstart runs pytest from repo root |
| All tests offline via MockTransport; never point tests at a real daemon | ✅ research D14; embed endpoint mocked as JSON transport |
| Explicit mode selection; chat request must never construct tutor machinery | ✅ dedicated `/ws/tutor` socket + separate service instance (D9) — structurally impossible to leak |
| Low-spec target (≤8 GB RAM, stdlib-only agent core, minimal deps) | ✅ exactly two new deps (D12); lazy per-subject vector matrices; subprocess whisper |
| Throughput parity gate after touching client.py/render paths | ⚠️ noted: `client.py` gains one non-streaming method; `./benchmark.sh` must be rerun during implementation tasks |

No violations requiring justification in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/004-local-ai-tutor/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions D1–D14
├── data-model.md        # Phase 1 output — entities, tables, config keys
├── contracts/
│   ├── tutor-core-api.md      # LibraryStore / TutorService / VectorIndex signatures
│   ├── tutor-rest-api.md      # /api/tutor/* endpoints
│   └── tutor-ws-protocol.md   # /ws/tutor frames & ordering rules
├── quickstart.md        # Offline suite + live end-to-end validation playbook
└── tasks.md             # Phase 2 output (/speckit.tasks) — NOT created here
```

### Source Code (repository root)

```text
src/ollama_tui/
├── client.py                  # + embed() method (POST /api/embed, non-streaming)
├── config.py                  # + tutor.* properties (enabled, models, socratic,
│                              #   level, think, top_k, whisper paths)
├── tutor/                     # NEW — UI-agnostic core (purity-linted)
│   ├── __init__.py            #    public exports
│   ├── models.py              #    dataclasses: Subject, Book, Chunk, Concept, …
│   ├── store.py               #    LibraryStore (SQLite, WAL, cascades)
│   ├── vector.py              #    VectorIndex protocol + NumpyVectorIndex
│   ├── extractors.py          #    txt/md/pdf(pypdf)/epub(stdlib) + chunker + fingerprint
│   ├── embeddings.py          #    EmbeddingService (batch, hash-keyed cache)
│   ├── retrieval.py           #    subject-scoped search assembly
│   ├── prompts.py             #    persona × socratic × level system prompts
│   ├── service.py             #    TutorService façade (ask/exercises/quizzes/exams…)
│   ├── progress.py            #    mastery updates, gap detection, learning path
│   ├── review.py              #    SM-2-lite ladder scheduler [1,2,5,12,30]
│   ├── assessment.py          #    quiz/exam generation + correction
│   └── voice.py               #    WhisperTranscriber (subprocess, injectable runner)
└── web/
    ├── server.py              # + /tutor page, /api/tutor/* routes, /ws/tutor socket
    └── static/tutor.html      # NEW — library, chat, practice, revision, exam views

tests/
├── conftest.py                # + create_embed_transport(vectors) JSON factory
├── unit/                      # + chunker, fingerprint dedup, vector index, ladder,
│                              #   mastery labels, prompt budget
├── contract/                  # + test_tutor_imports.py (purity), test_tutor_rest.py,
│                              #   test_ws_tutor.py, test_client_embed.py
└── integration/               # + test_tutor_flow.py (import→index→ask→quiz, offline)
```

**Structure Decision**: Single-package extension following the established
`agent/` precedent — a new UI-agnostic `tutor/` subpackage owns all domain
logic; `web/server.py` grows thin transport layers only; TUI integration is
explicitly out of v1 scope (clarified Q2) but unblocked because nothing above
touches textual.

## Complexity Tracking

> No constitution violations to justify — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## Post-design re-check

All gates still hold after Phase 1: purity lint covers `tutor/`, offline test
strategy covers every new surface (REST/WS/store/vector/voice), dependency
count unchanged since research D12, and the benchmark-parity caveat is tracked
as an explicit implementation task.
