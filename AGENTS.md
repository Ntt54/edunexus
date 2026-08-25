# AGENTS.md

## Commands

Run everything through the local venv (`./venv`, Python 3.12; project requires Python ≥ 3.11):

```bash
source venv/bin/activate                  # or prefix commands with venv/bin/
venv/bin/pytest tests/ -q                 # full suite (~100 tests, <1s, no Ollama needed)
venv/bin/pytest tests/unit/test_context_window.py -q    # single file
venv/bin/pytest tests/contract -q -k ndjson             # single test by keyword
./install.sh                              # creates ./venv + editable install
./ollama-tui --model gemma3:1b            # run the TUI (needs `ollama serve` on localhost:11434)
MODEL=gemma3:1b ./benchmark.sh            # throughput benchmark vs `ollama run` (live Ollama required)
```

- No pytest config file exists: async tests MUST use an explicit `@pytest.mark.asyncio` decorator (no auto mode).
- Extras: `pip install -e ".[dev,web]"` — `web` adds fastapi/uvicorn for `src/ollama_tutor/web/`.

## Imports & testing

- Tests import source as `from src.ollama_tutor...`, NOT `from ollama_tutor...`. Always run pytest from the repo root; importing the installed package name in tests exercises the wrong code path.
- The reverse is fatal at runtime: never use `from src.ollama_tutor...` inside `src/` code — use relative imports (`from ..models import ...`). The `src.` form only resolves under pytest and crashes the app with "No module named 'src'".
- All unit/contract/integration tests are offline: NDJSON streams are scripted via `httpx.MockTransport` factories in `tests/conftest.py`. Never point tests at a real Ollama daemon.

## Architecture

- Single src-layout package `src/ollama_tutor` (hatchling). Console scripts: `ollama-tui` (Textual TUI), `ollama-webgui` (FastAPI web GUI, `[web]` extra). Chat view at `/`, dedicated agent workspace at `/agent` (`web/static/agent.html`).
- `core/` holds the UI-agnostic services (`ChatService`, `AgentService`, `ProjectStore`) that BOTH frontends delegate to — no duplicated loop/streaming/persistence logic in `ui/app.py` or `web/server.py`. Enforced by `tests/contract/test_core_imports.py`: never import textual/fastapi inside `core/`.
- `agent/` is deliberately UI-agnostic: `AgentLoop` emits events consumed via `AgentService`. Keep Textual imports out of it. Protocol contracts: `specs/001-perf-agent-mode/contracts/`, `specs/002-split-agent-chat-web/contracts/`, `specs/003-project-build-agent/contracts/`.
- Mode selection is explicit per request (`mode: "chat"|"agent"` in WS frames); config `agent.enabled` only controls surface visibility — a chat request must never construct a ToolRegistry/AgentLoop.
- Projects (`core/projects.py`, persisted in `projects.json`) define the confinement root for agent runs; tool profiles (`plan`=read-only + plan-doc write, `build`=full) filter the registry per run. `edit_file` = exact search/replace with whitespace-normalized fallback + whole-file escalation after 2 failures (research D1/D2 in specs/003).
- Fix loops are budget + progress bounded: attempt cap (`fix_max_attempts`, default 4), identical-error-twice ⇒ `no_progress`; FINISHED payloads carry a named `outcome`. Agent request options floor `num_ctx` at 8192 (D7 guard in loop.py).
- `OllamaClient` accepts an injectable httpx transport — this is how streaming gets mocked in tests.
- Agent safety model: path jail bound to the active project root, confirmation gate on `run_command`, hard iteration cap, tool-output truncation before entering model context. Do not bypass these.
- User state lives outside the repo: `~/.config/ollama-tui/` (config.json, presets.json, history/, projects.json, errors.log — all frontend errors are appended there with traceback).

## Workflow & constraints

- Features follow spec-kit (`.specify/`): each feature gets `specs/<feature-id>/` with plan, tasks, contracts, quickstart. Current: `002-split-agent-chat-web` — its `quickstart.md` is the verification playbook.
- Agent mode targets low-spec machines (≤8 GB RAM): stdlib-only agent core, no new runtime deps, bounded loop (default 8 iterations).
- Throughput parity with `ollama run` is a regression gate — rerun `./benchmark.sh` after touching `client.py` or render paths.
