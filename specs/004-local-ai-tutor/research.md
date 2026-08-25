# Research & Decisions — Local AI Tutor (004-local-ai-tutor)

Date: 2026-08-24. Every decision below resolves a technical question left open by
`spec.md`. Constraints honored throughout: CPU-only ≤8 GB RAM machines, fully local,
minimal new dependencies, UI-agnostic core, offline tests.

## D1 — Storage: SQLite (stdlib) + NumPy vectors behind a `VectorIndex` protocol

- **Decision**: One SQLite database at `~/.config/ollama-tui/tutor/library.db`
  (stdlib `sqlite3`, synchronous, WAL mode). Embeddings stored as float32 BLOBs on
  the chunk row. In-memory search via a small `VectorIndex` protocol whose default
  implementation (`NumpyVectorIndex`) loads one subject's embedding matrix lazily,
  caches it, and invalidates on writes.
- **Rationale**: zero server process, matches the spec's mandated reference stack,
  trivially backupable (one file). A 50-book library ≈ 30–60 k chunks × 768 dims
  float32 ≈ 90–180 MB worst case; per-subject lazy loading keeps resident memory
  far below that, and cosine similarity over such matrices is <100 ms with NumPy.
- **Alternatives considered**: sqlite-vec extension (extra native dep, unnecessary at
  this scale); ChromaDB/LanceDB (heavy, violates light-storage constraint);
  pure-Python brute force (too slow past ~10 k chunks without NumPy).

## D2 — Embeddings generated through Ollama's `/api/embed`

- **Decision**: Extend `OllamaClient` with a non-streaming `embed(model, inputs)`
  method calling `POST /api/embed` (batch input, JSON response). Default model:
  `embeddinggemma` (user-confirmed). Vectors cached in an `embeddings` table keyed
  by `sha256(chunk_text)` so identical text (e.g., same book imported into two
  subjects) never recomputes.
- **Rationale**: reuses the existing connection pool, transport-injection testing
  story, and model management; no Python inference stack (no sentence-transformers/
  onnxruntime) keeps RAM and install size small.
- **Alternatives considered**: ONNX/GGERs local runtime (new heavy deps, duplicates
  a running Ollama); hashing-only lexical fallback (fails semantic-search
  requirement).

## D3 — Text extraction: pypdf for PDF, stdlib for EPUB/TXT/MD

- **Decision**: `.txt`/`.md` read directly (MD headings become section metadata);
  `.pdf` via **pypdf** (pure Python — the only new runtime dependency besides
  NumPy); `.epub` via stdlib `zipfile` + `xml.etree` + `html.parser` (EPUB is a zip
  of XHTML; spine order gives chapters, no dependency needed).
- **Rationale**: honors FR-002's four formats with exactly one added package.
  Per-page PDF extraction yields real page numbers for chunk metadata; EPUB spine
  items yield chapter titles.
- **Alternatives considered**: PyMuPDF (faster but AGPL + native wheel); ebooklib
  (extra dep for what stdlib already covers); external `pdftotext` binary (not
  guaranteed present).

## D4 — Voice: whisper.cpp invoked as a subprocess; browser captures 16 kHz PCM

- **Decision**: The web page records mono 16 kHz PCM via the Web Audio API, wraps a
  minimal WAV header (stdlib `wave` server-side), and the server runs the
  user-provided whisper.cpp CLI as a subprocess:
  `<whisper_bin> -m ggml-base-q5_1.bin -f <tmp.wav> -nt -l fr -otxt` (paths
  configurable via `tutor.whisper_binary` / `tutor.whisper_model`). Transcript text
  enters the normal ask pipeline.
- **Rationale**: the user already holds `ggml-base-q5_1.bin` (GGML ⇒ whisper.cpp
  family). Subprocess = zero Python bindings, crashes can't take the server down,
  tiny/base quantized runs in a few hundred MB RAM. Browser-side PCM capture
  avoids any ffmpeg dependency.
- **Alternatives considered**: faster-whisper/ctranslate2 (big native dep);
  pywhispercpp (bindings to maintain); sending audio to Ollama (not supported).

## D5 — Document identity & embedding reuse

- **Decision**: `books.fingerprint = sha256(normalized extracted text)` (FR-006).
  Import checks the fingerprint *within the subject*; the same content may attach
  to several subjects (join table), and chunk rows are per-subject while their
  vectors come from the shared hash-keyed embedding cache (D2).
- **Rationale**: reliable across renames/moves (clarified Q3), cheap to compute
  once at extraction, and makes cross-subject import free of re-embedding.

## D6 — Background indexing as asyncio tasks with DB-visible status

- **Decision**: On import (FR-004) the server spawns one `asyncio.Task` per book:
  extract → chunk → embed (batches of 16) → insert. Progress lives on the book row
  (`status: pending|indexing|done|failed`, `chunks_done/chunks_total`), polled by
  the UI; an `asyncio.Event` per task implements cancellation. Chunks become
  searchable only when status flips to `done`.
- **Rationale**: keeps the UI responsive on modest hardware, gives visible
  cancellable progress (clarified Q5), survives as plain state (no job framework).

## D7 — Mastery: continuous 0–100 score + derived label

- **Decision**: `progress.score REAL 0–100` per (subject, concept). Update rule:
  weighted exponential moving average — exercise success ±12, quiz correction ±10,
  exam result ±8, successful review +4, failed review −6 (clamped). Label
  thresholds: no row ⇒ `non étudié`; <40 `faible`; <70 `moyen`; ≥70 `maîtrisé`.
- **Rationale**: implements clarified Q4 (both bar and label from one source of
  truth); simple deterministic arithmetic is testable offline and auditable.

## D8 — Spaced repetition: fixed expanding ladder, no LLM

- **Decision**: On flashcard success the due offset moves up the ladder
  [1, 2, 5, 12, 30] days (index = consecutive-success count, capped); any failure
  resets the index to 0. Due reviews = simple SQL query on `next_due <= today`.
- **Rationale**: exactly the cadence the spec cites (day 1/2/5/12/30), pure
  arithmetic (SC-008: instant, zero model calls), trivially tunable later.

## D9 — Dedicated `/ws/tutor` WebSocket instead of extending the chat frame

- **Decision**: New WebSocket endpoint `/ws/tutor` with its own frame protocol
  (`contracts/tutor-ws-protocol.md`), reusing the existing same-origin guard,
  `safe_send` pattern, and `_log_error`. REST endpoints under `/api/tutor/*` for
  CRUD-shaped resources (subjects, documents, progress, glossary, reviews).
- **Rationale**: the existing `/ws` handler is already at its complexity budget
  carrying chat+agent; the spec's own architecture separates tutoring concerns.
  Explicit-mode discipline (chat must never build tutor machinery) is preserved
  structurally rather than by another `mode:` branch.
- **Alternatives considered**: `mode:"tutor"` on the shared socket (grows an
  already-large handler, mixes protocols).

## D10 — Think toggle plumbing

- **Decision**: `chat_stream(think=...)` already exists. TutorService forwards a
  per-request `think` flag (WS frame field) defaulting to persistent
  `config.tutor.think` (default **false** per FR-045). Thinking deltas, when
  enabled, stream as `thinking_delta` frames the UI may collapse.
- **Rationale**: zero client changes to the streaming path; satisfies the
  clarified speed toggle without touching OllamaClient semantics.

## D11 — Prompting & context budget

- **Decision**: System prompt variants: base tutor persona (FR-012 behaviors) ×
  socratic on/off (FR-013) × level (FR-014). Retrieved context = top-k 5 chunks
  (cosine ≥ 0.25 floor), hard cap ≈ 6000 chars, each block prefixed
  `[Livre X — chapitre Y, p. Z]` so citations are mechanical. Conversation window:
  last 6 turns + rolling session summary line. Tutor request options floor
  `num_ctx` at 8192 (mirrors specs/001 D7 guard).
- **Rationale**: bounds prompt size on CPU machines, makes source citation
  reliable (SC-004), prevents whole-book leakage (FR-015).

## D12 — New dependencies

- **Decision**: add `numpy>=1.26` and `pypdf>=4.0` to base dependencies;
  everything else (sqlite3, zipfile, html.parser, wave, hashlib, subprocess) is
  stdlib. No dev-dependency changes.
- **Rationale**: smallest set satisfying FR-002/FR-007/FR-008; both wheels are
  pure/native-light and CPU-friendly.

## D13 — Package placement & purity

- **Decision**: new UI-agnostic subpackage `src/ollama_tui/tutor/` (mirrors
  `agent/`): `models.py`, `store.py`, `vector.py`, `extractors.py`,
  `embeddings.py`, `retrieval.py`, `progress.py`, `review.py`, `assessment.py`,
  `voice.py`, `prompts.py`, `service.py`. A new contract test extends the
  core-purity lint to `tutor/` (no textual/fastapi imports). Web layer
  (`web/server.py`, `web/static/tutor.html`) delegates exclusively to
  `TutorService`/`LibraryStore`.
- **Rationale**: follows the established agent/core split; keeps both frontends
  thin; the lint makes the boundary enforceable.

## D14 — Testing strategy (offline)

- **Decision**: `/api/embed` mocked via a JSON-response `MockTransport` factory
  added to `conftest.py` (`create_embed_transport(vectors)`); chat streaming reuses
  existing NDJSON factories; `NumpyVectorIndex` tested against synthetic vectors;
  whisper transcription tested by faking the subprocess (monkeypatched runner);
  all store/service tests run against a `tmp_path` config dir. No test touches a
  live Ollama daemon.
- **Rationale**: consistent with repo-wide offline-test invariant; deterministic
  and fast.
