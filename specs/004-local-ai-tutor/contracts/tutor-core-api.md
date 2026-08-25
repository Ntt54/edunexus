# Contract: Tutor core API (`src/ollama_tui/tutor/`)

UI-agnostic by contract — no textual/fastapi imports anywhere under
`src/ollama_tui/tutor/` (enforced by a lint mirroring
`tests/contract/test_core_imports.py`). All async methods are `async def`;
store methods are synchronous (SQLite). Signatures are normative for tasks.

## LibraryStore (`tutor/store.py`)

```python
class LibraryStore:
    def __init__(self, config_dir: Path) -> None: ...
    # Subjects
    def create_subject(self, name: str) -> Subject
    def list_subjects(self) -> list[Subject]
    def rename_subject(self, subject_id: str, name: str) -> Subject
    def delete_subject(self, subject_id: str) -> None          # cascades
    def select_subject(self, subject_id: str) -> Subject       # active subject
    def active_subject(self) -> Subject | None
    # Documents
    def import_document(self, subject_id: str, path: Path) -> Book
        # raises FileNotFoundError / ValueError(unsupported format)
        # no-op returns existing Book when fingerprint already in subject
    def cancel_indexing(self, book_id: str) -> None
    def list_books(self, subject_id: str) -> list[Book]
    def remove_book(self, subject_id: str, book_id: str) -> None  # removes chunks+derived
    # Concepts & progress
    def upsert_concept(self, subject_id: str, name: str, path_rank: int | None = None) -> Concept
    def list_concepts(self, subject_id: str) -> list[Concept]
    def get_progress(self, subject_id: str) -> list[tuple[Concept, float | None]]
    def record_progress(self, concept_id: str, delta: float) -> None   # clamped 0..100
    # Glossary / relations
    def upsert_glossary_term(self, subject_id: str, term: str, definition: str,
                             book_id: str | None, chapter: str | None) -> GlossaryTerm
    def list_glossary(self, subject_id: str) -> list[GlossaryTerm]
    def upsert_relation(self, subject_id: str, from_c: str, to_c: str, relation: str) -> None
    def list_relations(self, subject_id: str) -> list[KnowledgeRelation]
```

## VectorIndex protocol (`tutor/vector.py`)

```python
class VectorIndex(Protocol):
    def search(self, subject_id: str, query_vec: list[float],
               k: int, floor: float) -> list[ScoredChunk]: ...
    def invalidate(self, subject_id: str) -> None: ...

class NumpyVectorIndex:      # default implementation over chunks+embeddings
    def __init__(self, store: LibraryStore): ...
```

`ScoredChunk` = chunk row + cosine score. Lazy per-subject matrix cache;
`invalidate` called by the indexer after writes.

## EmbeddingService (`tutor/embeddings.py`)

```python
class EmbeddingService:
    def __init__(self, client: OllamaClient, model: str): ...
    async def embed_texts(self, texts: list[str]) -> list[list[float]]
        # batched POST /api/embed via client.embed(); OllamaConnectionError propagates
    async def vectors_for_chunks(self, chunks: list[Chunk]) -> int
        # fills cache misses; returns count computed (0 when all cached)
```

## Extraction & chunking (`tutor/extractors.py`)

```python
SUPPORTED_FORMATS = {".txt", ".md", ".pdf", ".epub"}

def extract(path: Path) -> ExtractedDocument   # pages/blocks + per-block page/chapter
def fingerprint(text: str) -> str              # sha256 of normalized text
def chunk_document(doc: ExtractedDocument, target_chars=1000,
                   overlap=150) -> list[RawChunk]  # keeps chapter/section/page/position
```

## TutorService (`tutor/service.py`) — the façade used by both frontends

```python
class TutorService:
    def __init__(self, client: OllamaClient, history: HistoryManager,
                 config: Config, store: LibraryStore, index: VectorIndex): ...

    def import_and_index(self, subject_id: str, path: Path) -> asyncio.Task
    async def ask(self, question: str, *, model: str | None = None,
                  think: bool | None = None, socratic: bool | None = None,
                  level: str | None = None, session_id: str | None = None,
                  cancel: asyncio.Event | None = None,
                  ) -> AsyncIterator[TutorEvent]
        # TutorEvent.kind: "sources" | "thinking" | "content" | "done" | "error"
        # "sources" fires first with [{book,chapter,page,score}, ...]

    async def generate_exercise(self, concept_id: str, difficulty: str) -> Exercise
    async def grade_answer(self, exercise_id: str, answer: str) -> AttemptResult
        # escalates hint_level; never leaks solution before explicit request
    async def request_solution(self, exercise_id: str) -> str
    async def analyze_code(self, code: str, context: str) -> AsyncIterator[TutorEvent]

    async def prepare_knowledge(self, subject_id: str) -> PrepareReport
        # idempotent: flashcards/glossary/concepts/relations; skips existing (FR-024/034)
    def due_reviews(self, subject_id: str) -> list[ReviewItem]          # pure SQL, no LLM
    def grade_review(self, flashcard_id: str, success: bool) -> None    # D8 ladder

    async def create_quiz(self, subject_id: str, size: int, kinds: list[str]) -> Quiz
    async def create_exam(self, subject_id: str, size: int, time_limit_s: int) -> ExamSession
    def submit_answers(self, quiz_id: str, answers: dict[str, Any]) -> QuizReport
        # exam expiry auto-completes; updates progress (D7)

    def close_session(self, session_id: str) -> SessionSummary         # FR-028
    def resume_briefing(self, subject_id: str) -> ResumeBriefing       # FR-029
```

## Voice transcription (`tutor/voice.py`)

```python
class WhisperTranscriber:
    def __init__(self, binary: str, model_path: str, language: str = "fr"): ...
    @property
    def available(self) -> bool            # both paths configured and binary executable
    async def transcribe_wav(self, wav_path: Path) -> str
        # subprocess run; raises VoiceError on failure; runner injectable for tests
```

## Invariants (checked by tests)

1. No textual/fastapi imports under `src/ollama_tui/tutor/`.
2. Search is always subject-scoped: `VectorIndex.search` receives a subject_id.
3. Solution text of an open exercise is never returned by `grade_answer`.
4. Scheduling (`due_reviews`, ladder math) performs zero model calls.
5. Every grounded answer emits `sources` before any content delta.
