# Contract: Tutor WebSocket (`/ws/tutor`)

One dedicated socket per browser tab (D9). Same-origin check before upgrade
(identical to `/ws`). All frames are JSON objects with a `type` field.
Streaming mirrors the chat event vocabulary so the frontend renderer is shared:
`thinking_delta`, `content_delta`, `stats`, `end`, `error`,
`connection_error`, `cancelled`.

## Client → Server frames

```json
{"type": "ask", "question": "...", "subject_id": "…", "session_id": "…|null",
 "model": "gemma4:e2b|null", "think": false, "socratic": true,
 "level": "intermediate", "mode": "ask|locate|compare"}
```

- `mode:"ask"` — grounded tutoring answer (default).
- `mode:"locate"` — returns sources only, no LLM call (FR-031).
- `mode:"compare"` — multi-book synthesis (FR-033); requires ≥2 books matching.

```json
{"type": "transcribe", "audio_b64": "<16 kHz mono PCM WAV, base64>",
 "subject_id": "…"}
```

Server writes a temp `.wav`, runs the configured whisper.cpp binary
(`ggml-base-q5_1.bin`), then replies `transcript`. Voice disabled ⇒
`{type:"error", code:"voice_disabled"}`.

```json
{"type": "cancel"}
```

Cancels the in-flight ask/transcription on this socket (one active run per
connection, same invariant as `/ws`).

## Server → Client frames

```json
{"type": "start", "run_id": "…", "mode": "ask|locate|compare"}
{"type": "sources", "sources": [{"book":"…","chapter":"…","page":127,"score":0.83}]}
{"type": "transcript", "text": "…"}          // echo for confirmation (US8)
{"type": "thinking_delta", "text": "…"}      // only when think=true
{"type": "content_delta", "text": "…"}
{"type": "stats", "prompt_tokens": 0, "generated_tokens": 0, "tok_s": 0.0}
{"type": "end", "status": "done|stopped|error", "session_id": "…"}
{"type": "error", "message": "…", "code": "voice_disabled|no_passages|busy|…"}
{"type": "connection_error", "error": "…"}
{"type": "cancelled"}
```

## Ordering & semantics (normative)

1. `sources` MUST precede any `content_delta` on a grounded ask (SC-004);
   empty passages ⇒ `{type:"end",status:"done"}` after an
   `error{code:"no_passages"}` notice frame — the answer must state it is not
   book-grounded (FR edge case).
2. Exactly one active run per connection; a second `ask`/`transcribe` while
   busy yields `error{code:"busy"}`.
3. `cancel` maps to the same `asyncio.Event` mechanism as chat; partial output
   already streamed stays displayed; `end.status="stopped"`.
4. Session id: server creates/returns a `tutoring_sessions` row id on first
   ask of a conversation; client echoes it back to continue (FR-029 resume).
5. Every frame is logged via `_log_error` on failure paths, identical to the
   existing chat/agent error handling.
