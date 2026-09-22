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

## Web Research & Documentation

When working on this project, do not guess about external libraries, APIs, frameworks, models, protocols, or tools.

### Mandatory Web Research

Use Web research when:

* you do not know the answer with confidence;
* an external API or library is involved;
* the behavior of a dependency may depend on its version;
* documentation, syntax, configuration, or CLI options may have changed;
* an error involves an external dependency and the cause is unclear;
* implementing integration with Ollama, OpenAI-compatible APIs, FastAPI, httpx, SQLite, GGUF/llama.cpp, embeddings, rerankers, document parsers, or other external components;
* evaluating whether a proposed dependency or technology is compatible with this project.

### Before Using an External API

1. Check the installed version in the project.
2. Search the official documentation for that version.
3. Prefer official documentation and official GitHub repositories.
4. Verify the API or behavior before implementing it.
5. Do not invent undocumented parameters, methods, endpoints, or configuration options.

### Source Priority

Prefer sources in this order:

1. Official documentation.
2. Official GitHub repository / source code.
3. Official release notes / changelog.
4. Reliable technical documentation.
5. Community sources such as Stack Overflow only when official documentation is insufficient.

### Version Awareness

Always consider the version actually installed in this project.

Do not copy an API example from a newer or older version without verifying compatibility.

When relevant, inspect:

```bash
python --version
venv/bin/pip show <package>
```

and/or:

```bash
venv/bin/pip freeze
```

### Research Before Guessing

If you are uncertain, research first.

Do not compensate for uncertainty by writing speculative code.

If Web research cannot establish a reliable answer, state the uncertainty explicitly and prefer a solution that can be verified locally.

### Local Verification

Web research does not replace testing.

After implementing a solution based on external documentation:

1. Run the relevant tests.
2. Add or update tests when appropriate.
3. Verify that the implementation respects the project's architecture and Constitution.
4. For dependency/API changes, verify that existing offline tests remain offline.

### Avoid Unnecessary Research

Do not search the Web for stable Python language features, standard-library behavior, or project-specific information already documented in this repository.

Repository code, tests, specs, `.specify/`, `pyproject.toml`, and this `AGENTS.md` are the primary sources for project-specific behavior.

