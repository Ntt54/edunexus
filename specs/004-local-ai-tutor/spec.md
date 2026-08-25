# Feature Specification: Local AI Tutor & Personal Learning System

**Feature Branch**: `004-local-ai-tutor`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description (condensed from French original): "Intégrer au projet un professeur IA local : l'utilisateur crée des matières (Java, Python, Réseaux…), y ajoute des dizaines de livres/documents qui sont découpés en passages métadonnés et transformés en embeddings une seule fois (modèle d'embedding léger local, référence EmbeddingGemma ; stockage léger type SQLite + NumPy derrière une abstraction remplaçable par une base vectorielle dédiée). Le professeur répond aux questions par recherche sémantique filtrée sur la matière active, cite ses sources, répond en streaming, adopte un comportement pédagogique (mode socratique activable, adaptation au niveau débutant→expert), génère exercices avec échelle d'indices, analyse le code, suit les compétences par notion, détecte les lacunes, crée un parcours, des flashcards, des révisions espacées (algorithme léger sans LLM), des quiz et un mode examen. Il produit des résumés de session conservés, permet la reprise d'apprentissage, génère glossaire et carte des connaissances, retrouve et compare les explications entre livres. L'interaction vocale passe par une transcription locale légère (référence Whisper Tiny/Base quantifié). Le tout doit fonctionner hors ligne sur CPU modeste (≤8 Go RAM), minimiser CPU/RAM (pré-calcul, cache, pas de recalcul inutile), rester indépendant du fournisseur de modèle, modulaire, et privilégier simplicité et performance pour une première version simple, locale, légère et fonctionnelle."

## Clarifications

### Session 2026-08-24

- Q: Which document formats must version 1 accept when importing books into a subject? → A: TXT, Markdown, PDF, and EPUB (all four in v1).
- Q: Which interface should version 1 expose for the tutoring feature? → A: Web GUI only; core stays UI-agnostic so a TUI surface can follow later.
- Q: How should the system recognize that an imported document is the same as one already processed? → A: By a content fingerprint (hash of the extracted text), independent of file name or location.
- Q: How should per-notion mastery be represented, given the source shows both percentage bars and named levels? → A: A continuous 0–100 score, displayed as a progress bar plus a derived named label (non étudié / faible / moyen / maîtrisé).
- Q: When a book is imported into a subject, should indexing start automatically or wait for an explicit user action? → A: Automatically in the background right after import, with visible cancellable progress; content becomes searchable only once processing completes.
- Q: Which local models will serve as the default v1 configuration? → A: EmbeddingGemma for embeddings and Gemma 4 E2B as the main tutor LLM (user decision, 2026-08-24); both remain swappable per FR-039.
- Q: Should the model's extended thinking (reasoning) mode be disableable for faster answers? → A: Yes — a toggle (per request or persistent setting) turns off thinking mode to prioritize response speed on modest hardware.
- Q: Which model will handle local voice transcription in v1? → A: Whisper Base quantized Q5_1 (`ggml-base-q5_1.bin`, GGML format) — spoken question in, streamed text answer out (user decision, 2026-08-24).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build a personal learning library (Priority: P1)

A learner creates subjects (e.g., Java, Python, Networks, Mathematics) and adds their own books and course documents to each subject — several dozen documents per subject is normal. Each document is processed once: split into coherent passages that keep their place in the document, tagged with metadata (subject, book, chapter, section, page, position, difficulty when determinable, content type), and converted into searchable semantic representations. Re-adding an unchanged document does nothing.

**Why this priority**: Without the learner's own books becoming queryable knowledge, nothing else in the feature exists. This is the foundation slice.

**Independent Test**: Create a subject, import a few documents, verify passages carry correct metadata and that re-importing the same document performs no new processing. Delivers a browsable, reusable knowledge base.

**Acceptance Scenarios**:

1. **Given** an empty library, **When** the user creates a subject named "Java" and imports two books, **Then** the subject lists both books and each has been processed into passages with subject/book metadata attached.
2. **Given** a subject with an already-imported book, **When** the user imports the identical book again, **Then** the system recognizes it as already processed and does not regenerate passages or semantic representations.
3. **Given** a subject, **When** the user removes a book from it, **Then** all passages and derived data belonging to that book disappear from that subject's searchable knowledge.

---

### User Story 2 - Ask questions answered from your own books (Priority: P1)

While studying a subject, the learner asks a question in natural language ("What's the difference between an interface and an abstract class?"). The system automatically finds the most relevant passages **across all books of the active subject** — the learner never names a book — and the tutor answers using those passages, streaming the answer progressively as it is produced, and citing which book, chapter, and page each piece of information came from.

**Why this priority**: Grounded Q&A over personal books is the primary daily-use value and the heart of the RAG flow described by the user.

**Independent Test**: With two books imported into one subject, ask a question whose answer appears in both; verify the answer draws on both, cites sources, and starts appearing before completion. Search in another subject returns nothing irrelevant.

**Acceptance Scenarios**:

1. **Given** the active subject is Java with several indexed books, **When** the learner asks a conceptual question, **Then** relevant passages from more than one book can be retrieved automatically and the answer cites book/chapter/page references.
2. **Given** the active subject is Java, **When** the learner asks a question, **Then** books from other subjects (Python, Networks…) are not searched.
3. **Given** any grounded answer, **When** it is displayed, **Then** it begins appearing progressively from its first words rather than arriving all at once.
4. **Given** a question whose answer is not present in the subject's books, **When** the tutor responds, **Then** it clearly indicates the answer is not drawn from the learner's books.

---

### User Story 3 - A tutor that teaches instead of just answering (Priority: P1)

The assistant behaves as a personal teacher: it explains, simplifies, deepens, gives examples and analogies, asks questions, checks understanding, proposes exercises, analyzes answers, detects errors, gives hints, offers retries, and adapts difficulty — favoring understanding over handing out answers. A socratic mode can be switched on so the tutor guides with questions before revealing anything ("Before I answer — what do you think `==` compares in Java?"). The tutor adapts to the learner's level (beginner, intermediate, advanced, expert) and honors in-conversation requests like "explain like I'm a beginner".

**Why this priority**: This behavioral contract is what distinguishes the feature from the chat that already exists in the project; it defines the product's philosophy.

**Independent Test**: Enable socratic mode, ask a direct question, verify the tutor responds with guiding questions rather than the full answer; disable it and ask again to receive a direct explanation. Request a beginner-level and an expert-level explanation of the same notion and observe different depth.

**Acceptance Scenarios**:

1. **Given** socratic mode enabled, **When** the learner asks a direct question, **Then** the tutor replies with guiding questions leading the learner's reasoning instead of immediately giving the answer.
2. **Given** socratic mode disabled, **When** the learner asks the same question, **Then** the tutor gives a direct, complete explanation.
3. **Given** learner level set to beginner, **When** the learner asks about a notion, **Then** the explanation uses accessible language; **when** the learner explicitly asks for an advanced technical explanation, **Then** the depth adapts for that response.
4. **Given** any tutoring exchange, **When** the learner makes an error, **Then** the tutor identifies the mistake, explains why it is wrong, and invites a retry rather than merely correcting it.

---

### User Story 4 - Practice: exercises, hints, code analysis, skill tracking (Priority: P2)

The learner practices a notion: the tutor generates exercises (easy/medium/hard) based on the subject's books, the notion being studied, the learner's level, and past mistakes. On a wrong answer the tutor escalates gently: light hint → new attempt → second hint → new attempt → explanation → full solution only if explicitly requested. For programming subjects the learner can submit code; the tutor explains syntax/runtime/logic errors, bad practices, misuse, and misunderstood concepts — why it's wrong — without systematically replacing the learner's code with a complete solution. Every success, failure, answer, assessment, and revision updates per-notion mastery; repeated failures on a notion are detected and the tutor offers remediation (re-explanation, example, simpler exercise, revision, relevant book passages). Each subject has an ordered learning path (Syntaxe → Variables → … → Collections) that evolves with performance.

**Why this priority**: Practice plus measurement turns answering into learning; this is the second pillar of the product philosophy (understand → practice → err → hint → retry → master).

**Independent Test**: In a subject with indexed content, request an exercise on a known notion, answer wrongly twice, verify escalating hints and no unsolicited full solution; then verify the notion's mastery level decreased and a remediation offer appeared.

**Acceptance Scenarios**:

1. **Given** a studied notion, **When** the learner requests an exercise, **Then** difficulty reflects the requested tier and the solution is not shown upfront.
2. **Given** a wrong exercise answer, **When** the learner retries, **Then** hints escalate progressively and the complete solution appears only after explicit request.
3. **Given** a programming subject, **When** the learner submits faulty code, **Then** the tutor names the error category, explains why the code fails, and does not impose a full replacement solution.
4. **Given** graded interactions (exercise results, quiz answers, revisions), **When** they occur, **Then** the affected notions' mastery levels update accordingly.
5. **Given** several consecutive failures on the same notion, **When** the pattern is detected, **Then** the system flags the notion as a difficulty and the tutor proposes remediation options including relevant book passages.

---

### User Story 5 - Revision: flashcards, spaced repetition, quizzes, exam mode (Priority: P2)

The system generates flashcards from the books (mainly once, during knowledge preparation, not repetitively), each tied to a subject, notion, book, chapter, and level. A lightweight scheduling algorithm — not the LLM — plans reviews over increasing intervals (e.g., day 1, 2, 5, 12, 30) and surfaces due reviews. Quizzes can be generated from the knowledge (multiple choice, true/false, open question, matching, programming exercise) and their corrections feed skill tracking. An exam mode runs without any tutor assistance: a fixed number of questions within a time limit, no help, no hints, ending with a score plus strengths and weaknesses by notion.

**Why this priority**: Retention mechanics (cards, spacing, quizzes, exams) make learning stick and close the loop between practice and progression data.

**Independent Test**: Prepare knowledge for a subject, verify flashcards exist and are reused (not regenerated); advance the schedule to a due date and verify a review is proposed without any model call for the scheduling itself; launch an exam and verify the no-assistance rules and final report.

**Acceptance Scenarios**:

1. **Given** indexed knowledge for a notion, **When** preparation runs, **Then** flashcards are created with their associations and subsequent preparations reuse them instead of regenerating duplicates.
2. **Given** flashcards exist, **When** the review schedule comes due, **Then** the system proposes the due reviews, computed without invoking the LLM.
3. **Given** a quiz session, **When** answers are submitted, **Then** they are corrected and the results update the corresponding notions' mastery.
4. **Given** exam mode started (e.g., 20 questions, 30 minutes), **When** the learner attempts to get help or hints, **Then** none is provided; **when** the exam ends or time expires, **Then** a score and strengths/weaknesses per notion are reported.

---

### User Story 6 - Learning memory and session continuity (Priority: P2)

The system maintains a per-subject picture of the learner's progress — each notion with a mastery level (e.g., Variables ████████░░ 80%) — kept strictly separate from the books' knowledge: it describes the learner, not the documents. At the end of a session it produces a summary (notions studied, mastered, to review) that is stored. When the learner returns ("I want to continue learning Java"), the tutor recalls the last session's topic and difficulties and offers to review them or move on.

**Why this priority**: Continuity is what makes it a *personal* learning environment across days and weeks rather than isolated chats.

**Independent Test**: Study two notions with mixed success, end the session, verify the persisted summary; start a new session asking to continue and verify accurate recall of last topic and difficulties.

**Acceptance Scenarios**:

1. **Given** a finished tutoring session, **When** the learner ends it, **Then** a structured summary (studied / mastered / to-review notions) is produced and saved.
2. **Given** past sessions exist for a subject, **When** the learner asks to continue that subject, **Then** the tutor recalls the last session's topic and noted difficulties and proposes reviewing or continuing.
3. **Given** the same subject viewed at any time, **When** the learner consults progression, **Then** per-notion mastery is shown and is visibly distinct from book content.

---

### User Story 7 - Navigate and compare book knowledge (Priority: P3)

The learner can interrogate the library directly: "In my books, where is polymorphism explained?" returns the relevant locations (book, chapter, page); "Which of my books explain this best?" ranks sources. The tutor can compare several books on the same notion and synthesize, flagging differences between sources. A glossary is generated automatically from the documents with direct term lookup, and a map of relations between notions can be built primarily from information already extracted at indexing time.

**Why this priority**: High-value study tools, but usable only once the core loop (stories 1–3) exists; they enrich rather than enable.

**Independent Test**: With at least two books covering a notion, ask where it is explained and verify located sources with chapter/page; ask for a comparison and verify a synthesis mentioning divergences; open the glossary and request a term explanation.

**Acceptance Scenarios**:

1. **Given** a notion present in several books, **When** the learner asks where it is explained, **Then** the system lists each source with book, chapter, and page.
2. **Given** the same notion, **When** the learner asks which books explain it best, **Then** sources are ranked by relevance.
3. **Given** multiple relevant sources, **When** the learner asks for a multi-book explanation, **Then** the tutor produces a synthesis citing the sources and signals any disagreements between them.
4. **Given** indexed documents, **When** the learner opens the glossary, **Then** terms extracted from the documents are listed and any term can be explained on demand.

---

### User Story 8 - Talk to the tutor (Priority: P3)

The learner can speak a question instead of typing it. Speech is transcribed locally by a lightweight speech-recognition model suited to small machines (reference: a Whisper Tiny/Base-class quantized variant), and the transcript enters the exact same pipeline as typed text (active subject → retrieval → tutor → streamed answer). Small, fast models are preferred to keep latency low.

**Why this priority**: Convenience layer; the entire value chain already works through text.

**Independent Test**: Provide an audio question, verify a local transcription is produced, shown/confirmable, and the resulting answer follows the standard grounded flow.

**Acceptance Scenarios**:

1. **Given** microphone input available, **When** the learner speaks a question, **Then** it is transcribed locally without any cloud service and becomes a normal textual question.
2. **Given** a transcription, **When** it is ambiguous or imperfect, **Then** the learner can see the transcript before it is sent.

---

### Edge Cases

- What happens when a document cannot be read or is an unsupported format? The import of that document fails with a clear message; the rest of the library and any ongoing operation are unaffected.
- What happens when the same book is imported into two different subjects? Passages are tracked per subject so each subject searches only its own copy's knowledge.
- What happens when a very large book (or dozens of books) is indexed? Processing is incremental and resumable; the application remains responsive and does not need to hold whole books in memory.
- What happens when the embedding model or the tutor model is unavailable? Indexing and questioning fail gracefully with an explicit local error; previously indexed knowledge remains intact and searchable once the model returns.
- What happens when a question clearly belongs to another subject? The active-subject restriction holds; the system may suggest switching subjects but does not silently search elsewhere.
- What happens when an exam's time limit expires? Answered questions are auto-submitted and scored; unanswered ones count as incorrect.
- What happens when a session is interrupted mid-answer? State up to the interruption is preserved so the session can be resumed without losing progression updates already earned.
- What happens when a subject has no documents yet? The tutor says so plainly and offers general-knowledge help clearly labeled as not grounded in the learner's books.
- How does the system handle flashcard or glossary generation for poor-quality/scanned documents? It generates only from extractable text and reports what could not be processed.

## Requirements *(mandatory)*

### Functional Requirements

**Library**

- **FR-001**: System MUST allow creating, renaming, and deleting learning subjects.
- **FR-002**: System MUST allow adding documents to a subject, supporting several dozen documents per subject, accepting TXT, Markdown, PDF, and EPUB formats in v1.
- **FR-003**: System MUST remove a document's passages and derived data when the document is removed from a subject.

**Indexing**

- **FR-004**: System MUST start processing a document automatically when it is imported — in the background, with visible cancellable progress — and split it into coherent passages suitable for precise retrieval, preserving document order; the document's content becomes searchable only once processing completes.
- **FR-005**: System MUST attach to each passage the metadata: subject, book, chapter, section, page, position in document, difficulty level when determinable, and content type.
- **FR-006**: System MUST process each document once — recognizing already-processed documents by a content fingerprint computed from the extracted text, independent of file name or location — and MUST NOT recompute passages or embeddings for such documents.

**Semantic search & storage**

- **FR-007**: System MUST represent passages as embeddings generated by a lightweight local embedding model at indexing time (reference choice: EmbeddingGemma-class model), not at question time.
- **FR-008**: System MUST store documents, books, chapters, chunks, metadata, and embedding information in a lightweight embedded store, with the vector-search mechanism behind a replaceable abstraction so a dedicated vector engine can be substituted later without rewriting the application.
- **FR-009**: System MUST automatically retrieve the most relevant passages for a question across the books of the active subject, without the user naming a book.
- **FR-010**: System MUST restrict knowledge search to the active subject; semantic search decides relevance only within that subject.
- **FR-011**: System design MUST permit adding hybrid keyword + semantic search later (important for technical terms like `NullPointerException`, `HashMap`, `TCP`) without architectural rework; hybrid search itself is optional in v1.

**Tutor behavior**

- **FR-012**: System MUST provide a tutor persona able to: explain, simplify, deepen, give examples and analogies, ask questions, check understanding, propose exercises, analyze answers, detect errors, give hints, offer retries, and adapt difficulty — prioritizing understanding over simply providing answers.
- **FR-013**: System MUST allow socratic mode (guiding by questions before answering) to be toggled on and off.
- **FR-014**: System MUST adapt explanations to four levels (beginner, intermediate, advanced, expert) and honor in-conversation level requests such as "explain like I'm a beginner".
- **FR-015**: System MUST NOT send whole books to the model; only retrieved passages plus bounded context (progression, recent history) MAY enter the prompt.

**Practice**

- **FR-016**: System MUST generate exercises from indexed knowledge, the studied notion, the learner's level, and previous errors, at three difficulty tiers (easy, medium, hard).
- **FR-017**: System MUST NOT reveal an exercise's solution immediately.
- **FR-018**: System MUST escalate on incorrect answers: light hint → retry → second hint → retry → explanation → full solution only on explicit learner request.
- **FR-019**: For programming subjects, system MUST analyze submitted code and explain syntax, runtime, and logic errors, bad practices, feature misuse, design problems, and misunderstood concepts — why the code is wrong — without systematically replacing it with a complete solution.
- **FR-020**: Where an execution/testing environment exists, the system MAY use execution results to improve code diagnosis (optional in v1).

**Progression**

- **FR-021**: System MUST track per-notion mastery per subject as a continuous 0–100 score (rendered as a progress bar with a derived named label: non étudié, faible, moyen, maîtrisé), updated by successful exercises, failed exercises, question answers, assessments, and revisions.
- **FR-022**: System MUST detect notions causing repeated difficulty and offer remediation: new explanation, example, simpler exercise, revision, or relevant book passages.
- **FR-023**: System MUST maintain an ordered learning path per subject that evolves according to learner performance.

**Revision**

- **FR-024**: System MUST generate flashcards from the books, associated with subject, notion, book, chapter, and level, generated mainly during knowledge preparation and not regenerated redundantly.
- **FR-025**: System MUST schedule spaced reviews with a lightweight algorithmic scheduler (no LLM required), proposing reviews at expanding intervals (e.g., day 1, 2, 5, 12, 30).
- **FR-026**: System MUST generate quizzes (at minimum: multiple choice, true/false, open question; optionally matching and programming exercises), correct them, and integrate results into skill tracking.
- **FR-027**: System MUST provide an exam mode functioning without tutor assistance: fixed question count and time limit, no help, no hints, ending with a score and strengths/weaknesses per notion.

**Memory & continuity**

- **FR-028**: System MUST produce an end-of-session summary (notions studied, mastered, to review) and persist it for future sessions.
- **FR-029**: System MUST allow resuming learning: recalling the last session's topic and difficulties and offering to review or continue.
- **FR-030**: System MUST keep the learning memory (learner state) distinct from book knowledge (document content).

**Knowledge tools**

- **FR-031**: System MUST locate where a notion is explained across the library, returning book, chapter, and page for each source.
- **FR-032**: System MUST rank books by how well they explain a given notion.
- **FR-033**: System MUST compare several books on a notion via retrieval followed by a synthesized explanation, signaling differences between sources.
- **FR-034**: System MUST build a glossary automatically from the documents and allow direct on-demand explanation of any glossary term.
- **FR-035**: System SHOULD offer a knowledge map of notion relations, built primarily from information already extracted during indexing (optional in v1).

**Voice**

- **FR-036**: System MUST accept spoken questions transcribed locally by a lightweight speech-recognition model — v1 default: Whisper Base quantized Q5_1 (`ggml-base-q5_1.bin`, GGML format for efficient CPU inference) — feeding the same pipeline as text (spoken question in, streamed text answer out); heavy video processing is excluded.

**Model & resource management**

- **FR-037**: System MUST stream tutor responses progressively, displaying content from the first available tokens.
- **FR-038**: System MUST minimize resource use: no recomputation of existing embeddings, no searching outside the active subject, no whole-book contexts, no unnecessary simultaneous loading of multiple large models, no redundant flashcard/summary regeneration, and bounded conversation context.
- **FR-039**: System MUST allow selecting models per task (everyday tasks vs. general tutoring vs. complex reasoning/programming vs. transcription) and MUST remain provider- and model-independent — configurable, extensible to new models, never hardcoded to one model (reference configuration: small local model for common tasks, Gemma E2B-class for general tutoring, E4B-class or stronger for complex work, Whisper for transcription).
- **FR-040**: System MUST cache reusable artifacts (embeddings, summaries, flashcards, glossary, concepts, search results, session information) to avoid needless model invocations.
- **FR-041**: System MUST preserve and display the sources used for every grounded answer.
- **FR-042**: System MUST run entirely locally on a modest CPU-only machine with limited RAM (≤ 8 GB class), without cloud services.
- **FR-043**: System MUST integrate modularly with the existing application without imposing a rewrite, keeping components (indexing, retrieval, tutoring, evaluation, transcription, progression) independently replaceable or improvable.
- **FR-044**: System MUST NOT preclude future extensions (speech synthesis, full voice conversation, image/schema analysis, code execution, diagrams, course generation, rewards, daily goals, statistics, recommendations, multiple tutor personalities, dedicated vector engine), while none of them are required in v1.
- **FR-045**: System MUST allow disabling the model's extended thinking (reasoning) mode — per request or as a persistent setting — trading deeper reasoning for faster responses on modest hardware.

### Key Entities *(include if feature involves data)*

- **Subject (Matière)**: A learning domain (e.g., Java, Networks); owns its documents, knowledge, learning path, progression, and sessions.
- **Document (Book)**: A user-imported resource belonging to one or more subjects; source of all derived knowledge; identified by a content fingerprint of its extracted text.
- **Chunk (Passage)**: A coherent excerpt of a document with metadata (subject, book, chapter, section, page, position, difficulty, content type) and its embedding.
- **Embedding Record**: The numeric representation of a chunk, created once at indexing time, stored alongside chunk metadata.
- **Concept (Notion)**: A teachable unit within a subject (e.g., Héritage); links chunks, exercises, flashcards, path steps, and mastery.
- **Learning Path**: Ordered sequence of concepts for a subject, adjustable by performance.
- **Skill Progress**: Per-subject, per-concept continuous mastery score (0–100) with a derived named label, describing the learner (distinct from document content).
- **Exercise**: A generated practice item with difficulty tier, attempt history, hint stage, and resolution state.
- **Flashcard**: Question/answer revision card tied to subject, concept, book, chapter, level.
- **Review Schedule Item**: A flashcard's next-due date computed by the lightweight scheduler.
- **Quiz / Quiz Question**: Generated assessment sets (various types) with corrections feeding Skill Progress.
- **Exam Session**: Timed, assistance-free assessment with final score and strengths/weaknesses.
- **Tutoring Session**: A bounded period of interaction with a subject; produces a Session Summary.
- **Session Summary**: Persisted record of notions studied, mastered, and to review.
- **Glossary Term**: Term extracted from documents with definition and origin.
- **Knowledge Relation**: Directed relation between concepts, mainly derived from indexing.
- **Learner Profile**: Level (beginner→expert), socratic-mode preference, per-task model selections.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Importing a typical book completes in a single pass, and re-importing the same unchanged book performs no new processing (completes near-instantly).
- **SC-002**: Searching knowledge in a library of several dozen books returns relevant passages in under 2 seconds on the target modest hardware.
- **SC-003**: The first words of a tutor answer appear on screen within 5 seconds of asking on the target hardware, with the remainder streaming progressively.
- **SC-004**: For at least 90% of factual questions whose answers exist in the indexed books, the tutor's answer cites the correct book/chapter/page sources.
- **SC-005**: A notion's mastery percentage updates immediately after each graded interaction (exercise result, quiz correction, revision).
- **SC-006**: Three consecutive failures on the same notion reliably trigger a detected-difficulty flag and a remediation offer.
- **SC-007**: Flashcards for a notion are generated once; subsequent preparations reuse them with zero duplicate creation.
- **SC-008**: Computing due reviews for any number of scheduled cards completes instantly without any LLM invocation.
- **SC-009**: A 20-question timed exam runs to completion and always produces a score plus strengths/weaknesses breakdown, including on time expiry.
- **SC-010**: The complete feature operates on a CPU-only machine with 8 GB RAM without exhausting memory or requiring any network access.
- **SC-011**: In socratic sessions, learners reach answers through their own reasoning (tutor guides with questions) rather than receiving immediate solutions, observable across a full session.
- **SC-012**: Returning learners receive an accurate recap of their previous session (topic + difficulties) in under 10 seconds without re-explaining their history.
- **SC-013**: Switching the active subject changes retrieval scope immediately: questions in subject A never surface passages from subject B.

## Assumptions

- v1 accepts TXT, Markdown, PDF, and EPUB documents (clarified 2026-08-24); scanned image-only PDFs remain out of scope for v1.
- Single-user, single-machine usage; no accounts, no multi-user concurrency.
- Local model serving already exists in the project (Ollama-based); the tutor reuses it rather than introducing a new runtime. New runtime dependencies stay minimal, consistent with the project's low-spec target.
- Default v1 model configuration confirmed by the user (2026-08-24): **EmbeddingGemma** for embeddings, **Gemma 4 E2B** as the main tutor LLM, and **Whisper Base Q5_1** (`ggml-base-q5_1.bin`, GGML format) for local voice transcription. These remain swappable defaults, not hard dependencies (FR-039): any equivalent local model meeting the size/latency constraints satisfies the requirement.
- Lightweight storage (SQLite + NumPy per the request) is the reference implementation of the storage abstraction in FR-008; the abstraction — not the technology — is the requirement.
- v1 exposes the feature through the existing web GUI only (clarified 2026-08-24); all tutoring logic lives in UI-agnostic core services so a TUI surface can be added later without rework.
- Spaced-repetition intervals follow the classic expanding pattern (≈1, 2, 5, 12, 30 days) as a default schedule, tunable later.
- v1 deliberately excludes all §37 future-evolution items (speech synthesis, image analysis, code execution, dedicated vector DB, etc.) while keeping the architecture open to them.
