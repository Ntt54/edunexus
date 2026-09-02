/**
 * useTutorSocket — WebSocket composable for the tutor chat.
 *
 * Manages a single persistent connection to /ws/tutor with automatic
 * reconnection, and exposes reactive state for streaming messages.
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

export function useTutorSocket() {
  let ws: WebSocket | null = null;
  let pendingAsk: AskFrame | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  const RECONNECT_DELAY = 2000;

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
        status.value = "génération…";
        break;

      case "sources":
        currentSources.value = (f.sources as TutorSource[]) ?? [];
        break;

      case "thinking_delta":
        currentThinking.value += (f.content as string) ?? (f.text as string) ?? "";
        break;

      case "content_delta":
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

      case "end": {
        streaming.value = false;
        status.value = (f.status as string) === "stopped" ? "annulé" : "prêt";
        // Commit the accumulated content to the last tutor message
        const lastTutor = [...messages].reverse().find((m) => m.role === "tutor" && m.id === "_streaming");
        if (lastTutor && currentContent.value) {
          lastTutor.content = currentContent.value;
          lastTutor.thinking = currentThinking.value || undefined;
          lastTutor.sources = currentSources.value.length ? [...currentSources.value] : undefined;
          lastTutor.stats = currentStats.value ?? undefined;
          lastTutor.id = "t-" + Date.now();
        }
        currentContent.value = "";
        currentThinking.value = "";
        currentSources.value = [];
        currentStats.value = null;
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
    if (opts.bookIds) frame.book_ids = opts.bookIds;
    else if (opts.sessionId) frame.session_id = opts.sessionId;

    // Add user message
    messages.push({ id: "u-" + Date.now(), role: "user", content: question });

    // Add streaming tutor placeholder
    messages.push({ id: "_streaming", role: "tutor", content: "" });

    status.value = "génération…";
    streaming.value = true;

    sendRaw(frame);
  }

  function cancel() {
    sendRaw({ type: "cancel" });
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
    connect,
    disconnect,
    sendRaw,
    ask,
    cancel,
    onFrame,
  };
}
