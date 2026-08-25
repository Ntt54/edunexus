# Data Model — Local AI Tutor (004-local-ai-tutor)

Storage: single SQLite DB `~/.config/ollama-tui/tutor/library.db` (WAL mode),
plus tutor keys in `~/.config/ollama-tui/config.json` (`tutor.*`, mirroring the
existing `agent.*` pattern). All IDs are `uuid4().hex[:8]`; all timestamps are
UTC ISO-8601 strings (same convention as `core/projects.py`).

## Entity–Relationship overview

```text
Subject 1─* Book (via subject_books)      Subject 1─* Concept
Book    1─* Chunk                         Concept 1─1 SkillProgress
Chunk   *─1 EmbeddingCache (text hash)    Concept 1─* Exercise / Flashcard
Subject 1─* TutoringSession ─1 SessionSummary
Flashcard 1─1 ReviewItem                 Subject 1─* Quiz / ExamSession
Subject 1─* GlossaryTerm / KnowledgeRelation
```

## Tables

### subjects
| field | type | rules |
|---|---|---|
| id | TEXT PK | uuid8 |
| name | TEXT | UNIQUE (case-insensitive), non-empty, ≤80 chars |
| created_at / last_used_at | TEXT | ISO-8601 |

Deleting a subject cascades: chunks, concepts, progress, exercises, flashcards,
reviews, quizzes, exams, sessions, glossary, relations belonging to it.

### books
| field | type | rules |
|---|---|---|
| id | TEXT PK | uuid8 |
| title | TEXT | file stem or EPUB/PDF metadata title; editable |
| source_path | TEXT | original import location (informational) |
| format | TEXT | `txt` \| `md` \| `pdf` \| `epub` |
| fingerprint | TEXT | sha256 hex of normalized extracted text; UNIQUE |
| status | TEXT | `pending` → `indexing` → `done` \| `failed` |
| error | TEXT NULL | failure message when status=failed |
| chunks_done / chunks_total | INTEGER | live indexing progress |
| created_at | TEXT | |

**State transitions**: `pending → indexing` when the background task starts;
`indexing → done` when every chunk has a vector (from cache or freshly computed);
any exception ⇒ `failed` with `error` set; re-import of same fingerprint into the
same subject is a no-op; cancellation returns the row to `pending` and removes
partial chunks. A book reaches searchable state only via its join rows once
`done`.

### subject_books
| field | type | rules |
|---|---|---|
| subject_id / book_id | TEXT FK | composite PK; ON DELETE CASCADE |

Same book content may attach to several subjects without re-extraction
(fingerprint hit) and without re-embedding (hash-keyed vector cache).

### chunks
| field | type | rules |
|---|---|---|
| id | TEXT PK | uuid8 |
| subject_id / book_id | TEXT FK | cascade |
| ordinal | INTEGER | reading order within book |
| text | TEXT | non-empty, ≤4000 chars |
| chapter / section | TEXT NULL | from headings/spine/page markers |
| page | INTEGER NULL | real page for PDF; null otherwise |
| position | REAL | 0.0–1.0 relative offset in document |
| difficulty | TEXT NULL | `easy`\|`medium`\|`hard` when determinable |
| content_type | TEXT | `prose`\|`code`\|`definition`\|`list` |
| text_hash | TEXT | sha256 of text → joins embeddings cache |
| embedding | BLOB NULL | float32 little-endian vector; NULL until embedded |

Index: `(subject_id)` for scoped search; `(book_id, ordinal)` for ordered reads.

### embeddings (shared cache)
| field | type | rules |
|---|---|---|
| text_hash | TEXT PK | sha256(chunk text + model name) |
| model | TEXT | e.g. `embeddinggemma` (part of key semantics) |
| dim | INTEGER | vector length |
| vector | BLOB | float32 bytes |

Guarantees FR-006/D2: identical text never recomputes, across subjects and
re-imports.

### concepts
| field | type | rules |
|---|---|---|
| id | TEXT PK | |
| subject_id | TEXT FK | |
| name | TEXT | UNIQUE per subject, ≤80 chars |
| path_rank | INTEGER NULL | order in the learning path (FR-023); NULL = hors parcours |
| summary | TEXT NULL | one-line description (cache, FR-040) |

### progress
| field | type | rules |
|---|---|---|
| subject_id / concept_id | TEXT FK | composite PK |
| score | REAL | 0.0–100.0 continuous (clarified Q4) |
| updated_at | TEXT | |

Label derived at read time: no row = `non étudié`; <40 `faible`; <70 `moyen`;
≥70 `maîtrisé`. Updates follow research D7 weights.

### exercises
| field | type | rules |
|---|---|---|
| id | TEXT PK | |
| subject_id / concept_id | TEXT FK | |
| difficulty | TEXT | `easy`\|`medium`\|`hard` (FR-016) |
| statement | TEXT | generated prompt (may embed code block) |
| solution | TEXT | never sent unless explicitly requested (FR-018) |
| hint_level | INTEGER | 0–3 escalation stage (light hint, second hint, explanation) |
| hints | TEXT (JSON list) | pre-generated ladder so hints need no extra LLM call |
| status | TEXT | `open` → `solved` \| `given_up` |
| created_at | TEXT | |

### exercise_attempts
| field | type | rules |
|---|---|---|
| id | TEXT PK | |
| exercise_id | TEXT FK | |
| answer | TEXT | learner input (may be code) |
| verdict | TEXT | `correct`\|`incorrect`\|`partial` |
| feedback | TEXT | tutor analysis (why wrong — FR-019) |
| created_at | TEXT | |

Feeds progress updates (D7) and gap detection (3 consecutive incorrect on one
concept ⇒ flagged, FR-022).

### flashcards
| field | type | rules |
|---|---|---|
| id | TEXT PK | |
| subject_id / concept_id | TEXT FK | |
| book_id / chapter | TEXT NULL | provenance (FR-024) |
| level | TEXT | `beginner`\|`intermediate`\|`advanced`\|`expert` |
| question / answer | TEXT | non-empty |
| source_hash | TEXT | dedup key: hash(question+concept) — prevents regeneration (FR-024) |
| created_at | TEXT | |

UNIQUE(subject_id, source_hash).

### review_schedule
| field | type | rules |
|---|---|---|
| flashcard_id | TEXT PK FK | |
| streak_index | INTEGER | 0–4 → ladder [1,2,5,12,30] days (D8) |
| next_due | TEXT | ISO date; due query = `next_due <= today` |
| last_result | TEXT NULL | `success`\|`failure` |

### quizzes / quiz_questions / quiz_answers
quizzes: id, subject_id, kind (`quiz`\|`exam`), status
(`created`→`in_progress`→`completed`), allow_help (false for exams), time_limit_s
NULL except exams, started_at, finished_at, score (REAL NULL), report (JSON:
strengths/weaknesses by concept — FR-027).
quiz_questions: id, quiz_id FK, type (`mcq`\|`true_false`\|`open`\|`matching`\|
`code`), payload JSON (choices/pairs…), answer JSON, concept_id FK, points.
quiz_answers: question_id FK, response JSON, verdict (`correct`\|`incorrect`\|
`partial`), awarded points. Exam expiry auto-completes with unanswered =
incorrect (edge case).

### tutoring_sessions / session_summaries
tutoring_sessions: id, subject_id, started_at, last_active_at, status
(`active`→`closed`), transcript_path (JSON file under
`~/.config/ollama-tui/tutor/sessions/`).
session_summaries: session_id PK FK, concepts_studied JSON, concepts_mastered
JSON, to_review JSON, produced_at. Persisted for resume (FR-028/FR-029).

### glossary_terms
id, subject_id FK, term (UNIQUE per subject, case-insensitive), definition,
book_id/chapter provenance, created_at. Generated during preparation (FR-034,
cached — regeneration skips existing terms).

### knowledge_relations
id, subject_id FK, from_concept_id FK, to_concept_id FK, relation
(`prerequisite`\|`related`\|`part_of`), source (`indexing`\|`manual`). Feeds the
knowledge map from already-extracted data (FR-035).

## Config keys (`config.json` → `tutor.*`)

| key | default | meaning |
|---|---|---|
| enabled | false | gates `/tutor` surface visibility (mirrors agent.enabled) |
| embedding_model | `embeddinggemma` | D2 |
| tutor_model | `gemma4:e2b` | main LLM (user decision) |
| socratic | true | default socratic-mode state (FR-013) |
| level | `intermediate` | beginner\|intermediate\|advanced\|expert (FR-014) |
| think | false | extended-thinking default OFF (FR-045) |
| top_k | 5 | retrieved passages per question (D11) |
| whisper_binary | "" | path to whisper.cpp CLI (empty ⇒ voice disabled) |
| whisper_model | "" | path to `ggml-base-q5_1.bin` |

Request-level overrides (WS frame): think, socratic, level, model. `num_ctx`
floored at 8192 for tutor requests (D11).
