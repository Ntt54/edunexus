# Quickstart — Local AI Tutor (004-local-ai-tutor)

Validation playbook. Two parts: **offline** (no Ollama needed — must pass
before any commit) and **live** (needs `ollama serve` + models). Run all
commands from the repo root with the venv active:

```bash
source venv/bin/activate
```

## Prerequisites

- Python ≥ 3.11 venv (`./install.sh`), dev + web extras installed:
  `pip install -e ".[dev,web]"`
- Live part only: Ollama running on `localhost:11434` with
  `ollama pull embeddinggemma` and `ollama pull gemma4:e2b`;
  a whisper.cpp binary + `ggml-base-q5_1.bin` for the voice scenario.
- Test library: place 2–3 small books (a `.md`, a `.pdf`, an `.epub`) in a
  scratch folder, e.g. `/tmp/tutor-books/`.

## Part A — Offline test suite (regression gate)

```bash
venv/bin/pytest tests/ -q
```

Expected: full suite green (<60 s, no daemon). Feature-specific suites:

```bash
venv/bin/pytest tests/unit/test_chunker.py tests/unit/test_vector_index.py -q
venv/bin/pytest tests/contract/test_tutor_imports.py -q          # purity lint
venv/bin/pytest tests/contract/test_tutor_rest.py -q             # REST shapes
venv/bin/pytest tests/contract/test_ws_tutor.py -q               # WS protocol
venv/bin/pytest tests/integration/test_tutor_flow.py -q          # import→ask→quiz
```

Key offline proofs (see contracts/): subject-scoped retrieval, fingerprint
dedup no-op re-import, solution never leaks before explicit request,
review ladder math with zero model calls, exam expiry auto-submit,
`sources` frame precedes content deltas.

## Part B — Live end-to-end

```bash
ollama serve &
ollama-webgui &                    # binds 127.0.0.1
```

Enable the surface once:
`~/.config/ollama-tui/config.json` → `"tutor": {"enabled": true}`.

1. **Library & indexing** — open `http://127.0.0.1:8000/tutor`, create subject
   "Java", import the three scratch books. Expect: rows appear with status
   `indexing` then `done`, chunk counters fill, re-importing the same PDF is
   rejected as duplicate (fingerprint) without any new processing.
2. **Grounded ask** — ask *"Quelle est la différence entre une interface et
   une classe abstraite ?"* Expect: `sources` panel lists book/chapter/page
   from at least one imported book, answer streams progressively, citations
   match the sources panel.
3. **Subject isolation** — create subject "Réseaux" (empty or unrelated),
   make it active, repeat the Java question. Expect: no passages from Java
   books; notice that the answer is not grounded when nothing matches.
4. **Socratic toggle** — enable socratic mode, ask a direct question ⇒ guiding
   questions first; disable ⇒ direct explanation. Toggle think off ⇒ first
   visible tokens clearly faster than with think on.
5. **Practice loop** — request a medium exercise on a studied notion; answer
   wrong twice ⇒ escalating hints, no unsolicited solution; request the
   solution explicitly ⇒ shown; progress bar for the notion drops.
6. **Revision** — run *prepare knowledge* twice ⇒ second run reports only
   skipped items (no duplicate flashcards); due-review list appears instantly;
   grade one card ⇒ next due date follows the 1/2/5/12/30-day ladder.
7. **Exam** — launch a 5-question timed exam, try to obtain a hint (must be
   refused), let the timer expire or finish ⇒ score + strengths/weaknesses by
   notion.
8. **Session continuity** — close the session ⇒ summary persisted; return to
   the subject later ⇒ resume briefing recalls last topic + difficulties.
9. **Voice** *(optional)* — configure `tutor.whisper_binary` +
   `tutor.whisper_model`, hold the record button, speak a question ⇒
   transcript shown, then standard grounded streamed answer.

## Performance spot-checks (SC targets)

- Search latency: question on a ≥10-book subject returns sources visibly
  under ~2 s (before LLM tokens).
- First tokens: under ~5 s after sources with think off on the target
  hardware.
- RAM: watch the process stay comfortably within an 8 GB machine while a
  large PDF indexes in the background.

## Failure drills

- Stop Ollama mid-indexing ⇒ book row ends `failed` with error, UI stays up,
  rest of library searchable; restart Ollama, cancel + re-import resumes.
- Import a corrupted PDF ⇒ import error message, other imports unaffected.
