/**
 * Minimal SSE incremental reader (no dependencies) for the future course
 * stream contract:
 *   GET /api/tutor/lesson-discussions/{id}/course/stream?learner_id=…
 *   → `text/event-stream`, `data: {"delta":"…"}` … then
 *     `data: {"done":true,"fallback":bool}` or `data: {"error":"…"}`.
 *
 * `openEventStream` resolves the contract strictly: any non-2xx status,
 * non-SSE content-type or missing body yields `null` so callers fall back
 * to the classic wait-for-final-text behaviour — never a broken screen.
 */

export type SSEStreamEvent =
  | { type: "delta"; text: string }
  | { type: "done"; fallback: boolean }
  | { type: "error"; message: string };

export interface SSEFeed {
  events: SSEStreamEvent[];
  /** Incomplete tail to prepend to the next chunk. */
  rest: string;
}

/** Feed a raw text chunk; only complete `\n`-terminated lines are parsed. */
export function feedSSE(buffer: string): SSEFeed {
  const events: SSEStreamEvent[] = [];
  const parts = buffer.split("\n");
  const rest = parts.pop() ?? "";
  for (const raw of parts) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (!line || line.startsWith(":")) continue; // blank / heartbeat comment
    if (!line.startsWith("data:")) continue; // ignore event:/id:/retry: fields
    const payload = line.slice(5).trimStart();
    if (!payload) continue;
    let data: unknown;
    try {
      data = JSON.parse(payload);
    } catch {
      continue; // malformed payload: skip, keep the stream alive
    }
    if (typeof data !== "object" || data === null) continue;
    const obj = data as Record<string, unknown>;
    if (typeof obj.delta === "string" && obj.delta) {
      events.push({ type: "delta", text: obj.delta });
    } else if (obj.done === true) {
      events.push({ type: "done", fallback: obj.fallback === true });
    } else if (typeof obj.error === "string" && obj.error) {
      events.push({ type: "error", message: obj.error });
    }
  }
  return { events, rest };
}

/** Open an SSE stream, or `null` when the endpoint cannot stream (404,
 *  non-SSE answer, network failure) → caller must use the classic path. */
export async function openEventStream(
  url: string,
  signal: AbortSignal,
): Promise<ReadableStream<Uint8Array> | null> {
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: "text/event-stream" },
      signal,
    });
  } catch {
    return null;
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok || !contentType.includes("text/event-stream") || !response.body) {
    try {
      await response.body?.cancel();
    } catch {
      /* ignore cleanup failure */
    }
    return null;
  }
  return response.body;
}

/** Elapsed seconds → `mm:ss` (hours fold into minutes, no false precision). */
export function formatElapsed(totalSeconds: number): string {
  const total = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
