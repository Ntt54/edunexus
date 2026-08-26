# AGENTS.md

## Commands

Run everything through the local venv (`./venv`, Python 3.12; project requires Python ≥ 3.11):

```bash
source venv/bin/activate                  # or prefix commands with venv/bin/
venv/bin/pytest tests/ -q                 # full suite (~377 tests, <20s, no Ollama needed)
venv/bin/pytest tests/unit/test_bm25.py -q    # single file
venv/bin/pytest tests/contract -q -k ndjson   # single test by keyword
./install.sh                              # creates ./venv + editable install
edunexus                                  # run the web GUI (http://127.0.0.1:9215/tutor)
```

- `pythonpath = ["."]` in pyproject.toml: async tests MUST use explicit `@pytest.mark.asyncio` decorator (no auto mode).
- Extras: `pip install -e ".[dev,web]"` — `web` adds fastapi/uvicorn; `pip install -e ".[office]"` — adds python-docx/python-pptx.

## Imports & testing

- Tests import source as `from src.ollama_tutor...`, NOT `from ollama_tutor...`. Always run pytest from the repo root; importing the installed package name in tests exercises the wrong code path.
- The reverse is fatal at runtime: never use `from src.ollama_tutor...` inside `src/` code — use relative imports (`from ..models import ...`). The `src.` form only resolves under pytest and crashes the app with "No module named 'src'".
- All unit/contract/integration tests are offline: NDJSON streams are scripted via `httpx.MockTransport` factories in `tests/conftest.py`. Never point tests at a real Ollama daemon.

## Architecture

- Single src-layout package `src/ollama_tutor` (hatchling). Entry point: `edunexus` command → FastAPI web GUI on `127.0.0.1:9215/tutor`.
- **Constitution v1.0.0** at `.specify/memory/constitution.md` — 6 principles: I. Cœur découplé, II. Préservation, III. Tests hors-ligne, IV. Sécurité locale, V. Légèreté, VI. Observabilité.
- **Principle I**: `tutor/` must NEVER import textual or fastapi. `web/` is thin transport only. Enforced by `tests/contract/test_core_imports.py`.
- `tutor/service.py` — `TutorService`: unified service layer for both web and TUI frontends (import, index, ask, quiz, exercises, diagnostic, revision sheets, summaries, exams, gamification).
- `tutor/store.py` — `LibraryStore`: SQLite persistence (books, chunks, embeddings, subjects, categories, learning paths, diagnostic sessions, error history, learner profile).
- `tutor/providers/` — provider interfaces + adapters: `EmbeddingProvider`, `GGUFEmbeddingProvider`, `GGUFLLMProvider`, `OpenAICompatProvider`, `LlamaServerManager`, `DocumentParser`.
- `tutor/retrieval.py` — `Retriever`: hybrid BM25 + cosine search with RRF fusion, optional SimpleReranker.
- `tutor/assessment.py` — `QuizEngine`, `ExerciseEngine`: quiz/exercise evaluation with error recording.
- `tutor/prompts.py` — prompt builders: summary, revision sheet, diagnostic, exam analysis, learning path.
- `tutor/reranker.py` — `SimpleReranker`: post-retrieval reranking (0.7*cosine + 0.3*jaccard).
- `tutor/embeddings.py` — embedding cache + parallel batch support (US8).
- `tutor/extractors.py` — PDF/EPUB/DOCX/PPTX/TXT/MD extraction with metadata.
- `tutor/classifier.py` — hybrid import classification (rules + LLM).
- `tutor/progress.py` — mastery tracking per concept.
- `tutor/review.py` — spaced repetition review.
- `web/server.py` — FastAPI routes (thin transport, delegates to `TutorService`).
- `web/static/tutor.html` — single-file autonomous web UI (9 spaces: Accueil, Conversations, Bibliothèque, Apprentissage, Entraîner, Quiz/Examens, Progression, Explorer, Parcours, Réglages).
- `client.py` — Ollama client (injectable httpx transport for tests).
- `config.py` — persisted config (`~/.config/ollama-tui/config.json`).
- User state: `~/.config/ollama-tui/` (config.json, presets.json, history/, projects.json, errors.log).

## Workflow & constraints

- Features follow spec-kit (`.specify/`): each feature gets `specs/<feature-id>/` with plan, tasks, contracts, quickstart.
- Feature 005: multi-space UI (36/36 tasks, committed). Feature 006: adaptive learning (47 tests, committed). Feature 007: MVP audit+evolution (95 tasks, 18 phases, committed).
- Agent mode targets low-spec machines (≤8 GB RAM): stdlib-only agent core, no new runtime deps, bounded loop (default 8 iterations).
- Throughput parity with `ollama run` is a regression gate — rerun `./benchmark.sh` after touching `client.py` or render paths.
