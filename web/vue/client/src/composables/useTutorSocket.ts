/**
 * useTutorSocket — WebSocket composable for the tutor chat.
 *
 * Manages a single persistent connection to /ws/tutor with automatic
 * reconnection, and exposes reactive state for streaming messages.
 * Handles all 12 WS frames: start, sources, thinking_delta/content_delta,
 * reasoning, stats, definition, citation_warnings, error, end, cancelled,
 * transcript, pleias_sections — plus session_id persistence (tutor.html#4266).
 */
import { reactive, ref, onUnmounted } from "vue";

/* ── Frame types received from the server ────────────────────── */
export interface TutorFrame {
  type: string;
  [key: string]: unknown;
}

export interface TutorSource {
  book?: string;
  book_id?: string;
  title?: string;
  chapter?: string;
  page?: number | null;
  excerpt?: string;
  score?: number;
}

export interface TutorStats {
  token_count?: number;
  tokens_per_sec?: number;
  generated_tokens?: number;
  tok_s?: number;
  prompt_tokens?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "tutor";
  content: string;
  thinking?: string;
  sources?: TutorSource[];
  stats?: TutorStats;
  error?: string;
  warning?: string;
  kind?: "answer" | "hint";
}

export interface CitationWarning {
  message?: string;
  detail?: string;
  index?: number;
  [key: string]: unknown;
}

export interface AskFrame {
  type: "ask";
  question: string;
  subject_id?: string;
  socratic?: boolean;
  level?: string;
  think?: boolean;
  conversation_id?: string;
  book_ids?: string[];
  session_id?: string;
}

const SESSION_IDS_KEY = "edunexus:sessionIds";

function loadSessionIds(): Record<string, string> {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(SESSION_IDS_KEY) : null;
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, string>;
      if (parsed && typeof parsed === "object") return parsed;
    }
  } catch {
    /* ignore */
  }
  return {};
}

function persistSessionIds(map: Record<string, string>) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(SESSION_IDS_KEY, JSON.stringify(map));
    }
  } catch {
    /* ignore quota */
  }
}

export function useTutorSocket() {
  let ws: WebSocket | null = null;
  let pendingAsk: AskFrame | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  const RECONNECT_DELAY = 2000;

  // Remember last ask subject to associate session_id on end (tutor.html S.activeId)
  let lastAskSubjectId: string | null = null;
  let lastAskConversationId: string | null = null;

  /* ── Reactive state ─────────────────────────────────────────── */
  const connected = ref(false);
  const streaming = ref(false);
  const messages = reactive<ChatMessage[]>([]);
  const currentContent = ref("");
  const currentThinking = ref("");
  const currentSources = ref<TutorSource[]>([]);
  const currentStats = ref<TutorStats | null>(null);
  const status = ref("prêt");
  const error = ref<string | null>(null);

  // ── Missing frames state ──
  const transcript = ref("");
  const citationWarnings = ref<CitationWarning[]>([]);
  const validCitations = ref<unknown[]>([]);
  const pleiasSections = ref<Record<string, unknown>>({});
  const currentSessionId = ref<string | null>(null);
  const sessionIds = reactive<Record<string, string>>(loadSessionIds());

  /* ── Callbacks ──────────────────────────────────────────────── */
  type FrameHandler = (frame: TutorFrame) => void;
  const frameHandlers: FrameHandler[] = [];

  function onFrame(handler: FrameHandler) {
    frameHandlers.push(handler);
    return () => {
      const idx = frameHandlers.indexOf(handler);
      if (idx >= 0) frameHandlers.splice(idx, 1);
    };
  }

  /* ── WebSocket lifecycle ───────────────────────────────────── */
  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const proto = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(proto + location.host + "/ws/tutor");

    ws.onopen = () => {
      connected.value = true;
      status.value = "prêt";
      if (pendingAsk) {
        ws!.send(JSON.stringify(pendingAsk));
        pendingAsk = null;
      }
    };

    ws.onmessage = (e) => {
      try {
        const frame = JSON.parse(e.data) as TutorFrame;
        handleFrame(frame);
      } catch {
        // ignore unparseable messages
      }
    };

    ws.onerror = () => {
      error.value = " connexion WebSocket interrompue";
      status.value = "erreur";
    };

    ws.onclose = () => {
      connected.value = false;
      ws = null;
      // Schedule reconnection
      if (!reconnectTimer) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connect();
        }, RECONNECT_DELAY);
      }
    };
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null; // prevent auto-reconnect
      ws.close();
      ws = null;
    }
    connected.value = false;
  }

  /* ── Frame handler ─────────────────────────────────────────── */
  function handleFrame(f: TutorFrame) {
    // Dispatch to registered handlers
    for (const h of frameHandlers) h(f);

    switch (f.type) {
      case "start":
        streaming.value = true;
        currentContent.value = "";
        currentThinking.value = "";
        currentSources.value = [];
        currentStats.value = null;
        citationWarnings.value = [];
        validCitations.value = [];
        pleiasSections.value = {};
        // transcript persists until next transcript frame; do not clear here
        status.value = "génération…";
        // capture session_id if echoed on start (some server impl)
        if (typeof f.session_id === "string" && f.session_id) {
          currentSessionId.value = f.session_id as string;
        }
        break;

      case "sources":
        currentSources.value = (f.sources as TutorSource[]) ?? [];
        break;

      case "thinking_delta":
      case "reasoning":
        currentThinking.value += (f.content as string) ?? (f.text as string) ?? "";
        break;

      case "content_delta":
      case "delta":
        currentContent.value += (f.content as string) ?? (f.text as string) ?? "";
        break;

      case "definition": {
        // Append definition to content as a styled block
        const term = f.term as string | undefined;
        const def = f.definition as string | undefined;
        if (def) {
          currentContent.value += (term ? `\n**${term}** : ` : "") + def + "\n";
        }
        break;
      }

      case "stats":
        currentStats.value = f as unknown as TutorStats;
        break;

      case "citation_warnings": {
        const warnings = (f.warnings as CitationWarning[]) ?? [];
        const valid = (f.valid_citations as unknown[]) ?? (f.validCitations as unknown[]) ?? [];
        citationWarnings.value = Array.isArray(warnings) ? warnings : [];
        validCitations.value = Array.isArray(valid) ? valid : [];
        // Also surface as warning on the streaming bubble for UI visibility
        const lastTutorW = [...messages].reverse().find((m) => m.role === "tutor" && m.id === "_streaming");
        if (lastTutorW && warnings.length) {
          const first = warnings[0] as CitationWarning;
          lastTutorW.warning = (first.message as string) ?? (first.detail as string) ?? "Citation invalide";
        }
        break;
      }

      case "pleias_sections": {
        const sections = (f.sections as Record<string, unknown>) ?? {};
        pleiasSections.value = sections && typeof sections === "object" ? (sections as Record<string, unknown>) : {};
        break;
      }

      case "transcript": {
        const text = (f.text as string) ?? (f.transcript as string) ?? "";
        transcript.value = text;
        status.value = "transcription reçue — vérifiez puis envoyez";
        break;
      }

      case "end": {
        streaming.value = false;
        status.value = (f.status as string) === "stopped" ? "annulé" : "prêt";
        // Persist session_id (tutor.html:4266 S.sessionIds[S.activeId]=sid) — only for quick sessions (no conversation)
        const sid = f.session_id as string | undefined;
        if (sid && typeof sid === "string" && sid) {
          currentSessionId.value = sid;
          // Persist per-subject if we know the subject and there was no conversation scope
          const shouldPersistPerSubject = !lastAskConversationId;
          if (shouldPersistPerSubject && lastAskSubjectId) {
            sessionIds[lastAskSubjectId] = sid;
            persistSessionIds(sessionIds as unknown as Record<string, string>);
          } else if (!lastAskSubjectId) {
            // Fallback generic key when subject unknown
            sessionIds["_last"] = sid;
            persistSessionIds(sessionIds as unknown as Record<string, string>);
          }
        }
        // Also handle echoed think/socratic/level on end frame (D10)
        // Commit the accumulated content to the last tutor message
        const lastTutor = [...messages].reverse().find((m) => m.role === "tutor" && m.id === "_streaming");
        if (lastTutor) {
          if (currentContent.value) lastTutor.content = currentContent.value;
          if (currentThinking.value) lastTutor.thinking = currentThinking.value || undefined;
          if (currentSources.value.length) lastTutor.sources = [...currentSources.value];
          if (currentStats.value) lastTutor.stats = currentStats.value ?? undefined;
          if (citationWarnings.value.length) lastTutor.warning = (citationWarnings.value[0] as CitationWarning).message ?? lastTutor.warning;
          // attach pleiasSections/citationWarnings as extra for consumers via onFrame
          lastTutor.id = "t-" + Date.now();
        }
        currentContent.value = "";
        currentThinking.value = "";
        currentSources.value = [];
        currentStats.value = null;
        // keep citationWarnings/pleiasSections for inspection until next start
        break;
      }

      case "error": {
        streaming.value = false;
        status.value = "erreur";
        const msg = (f.message as string) ?? (f.code as string) ?? "erreur inconnue";
        const lastTutorE = [...messages].reverse().find((m) => m.role === "tutor" && m.id === "_streaming");
        if (lastTutorE) {
          lastTutorE.error = msg;
          lastTutorE.id = "t-" + Date.now();
        } else {
          messages.push({ id: "e-" + Date.now(), role: "tutor", content: "", error: msg });
        }
        currentContent.value = "";
        currentThinking.value = "";
        break;
      }

      case "warning": {
        const wmsg = (f.message as string) ?? "Avertissement.";
        const lastTutorW = [...messages].reverse().find((m) => m.role === "tutor" && m.id === "_streaming");
        if (lastTutorW) {
          lastTutorW.warning = wmsg;
        }
        break;
      }

      case "cancelled":
        streaming.value = false;
        status.value = "annulé";
        // Remove the streaming placeholder if present
        {
          const idx = messages.findIndex((m) => m.id === "_streaming");
          if (idx >= 0) messages.splice(idx, 1);
        }
        currentContent.value = "";
        currentThinking.value = "";
        break;
    }
  }

  /* ── Public actions ────────────────────────────────────────── */
  function sendRaw(frame: unknown) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame));
    } else if ((frame as Record<string, unknown>).type === "ask") {
      pendingAsk = frame as AskFrame;
      connect();
    }
  }

  function ask(
    question: string,
    opts: {
      subjectId?: string;
      conversationId?: string;
      socratic?: boolean;
      level?: string;
      think?: boolean;
      bookIds?: string[];
      sessionId?: string;
    } = {},
  ) {
    if (streaming.value) return; // already busy
    const frame: AskFrame = {
      type: "ask",
      question,
      subject_id: opts.subjectId,
      socratic: opts.socratic ?? false,
      level: opts.level ?? "Débutant",
      think: opts.think ?? false,
    };
    if (opts.conversationId) frame.conversation_id = opts.conversationId;
    if (opts.bookIds && opts.bookIds.length) {
      frame.book_ids = opts.bookIds;
    } else if (opts.sessionId) {
      frame.session_id = opts.sessionId;
    } else if (!opts.conversationId) {
      // Quick session continuity: reuse stored session_id per subject (tutor.html buildAskFrame)
      const stored = opts.subjectId ? (sessionIds[opts.subjectId] ?? null) : currentSessionId.value;
      const fallback = stored ?? sessionIds["_last"] ?? null;
      if (fallback) frame.session_id = fallback;
    }

    lastAskSubjectId = opts.subjectId ?? null;
    lastAskConversationId = opts.conversationId ?? null;

    // Add user message
    messages.push({ id: "u-" + Date.now(), role: "user", content: question });

    // Add streaming tutor placeholder
    messages.push({ id: "_streaming", role: "tutor", content: "" });

    status.value = "génération…";
    streaming.value = true;

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame));
    } else {
      pendingAsk = frame;
      connect();
    }
  }

  function cancel() {
    sendRaw({ type: "cancel" });
  }

  function transcribe(audioB64: string) {
    sendRaw({ type: "transcribe", audio: audioB64 });
  }

  function clearTranscript() {
    transcript.value = "";
  }

  function getSessionId(subjectId?: string): string | null {
    if (subjectId) return sessionIds[subjectId] ?? null;
    return currentSessionId.value ?? sessionIds["_last"] ?? null;
  }

  function setSessionId(subjectId: string, sid: string) {
    if (!subjectId || !sid) return;
    sessionIds[subjectId] = sid;
    currentSessionId.value = sid;
    persistSessionIds(sessionIds as unknown as Record<string, string>);
  }

  function clearSessionId(subjectId?: string) {
    if (subjectId) {
      delete sessionIds[subjectId];
    } else {
      currentSessionId.value = null;
      delete sessionIds["_last"];
    }
    persistSessionIds(sessionIds as unknown as Record<string, string>);
  }

  /* ── Cleanup ───────────────────────────────────────────────── */
  onUnmounted(() => {
    disconnect();
  });

  return {
    connected,
    streaming,
    messages,
    currentContent,
    currentThinking,
    currentSources,
    currentStats,
    status,
    error,
    // new reactive state for missing frames
    transcript,
    citationWarnings,
    validCitations,
    pleiasSections,
    currentSessionId,
    sessionIds,
    connect,
    disconnect,
    sendRaw,
    ask,
    cancel,
    transcribe,
    clearTranscript,
    getSessionId,
    setSessionId,
    clearSessionId,
    onFrame,
  };
}
