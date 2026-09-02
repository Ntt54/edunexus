<!-- EduNexus UI direction: Atelier de progression — tuteur IA avec WebSocket streaming, conversations et citations sourcées. -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import {
  ArrowUp, FileText, Plus, X, Loader2, StopCircle, RefreshCw,
} from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";
import { useTutorSocket } from "@/composables/useTutorSocket";
import type { ChatMessage, TutorSource } from "@/composables/useTutorSocket";

/* ── Stores ──────────────────────────────────────────────────── */
const { state } = useLearningStore();
const { t } = usePreferences();
const subject = computed(() => state.data?.subject.name ?? t("tutor.defaultSubject"));
const subjectId = computed(() => state.data?.subject.id ?? "");

/* ── WebSocket ───────────────────────────────────────────────── */
const {
  connected, streaming, messages, currentContent, currentThinking,
  currentSources, currentStats, status, connect, ask, cancel,
} = useTutorSocket();

/* ── Conversations ───────────────────────────────────────────── */
interface Conversation {
  id: string;
  title: string;
  subject_id: string;
  subject_name?: string;
  message_count?: number;
  source_count?: number;
  updated_at?: string;
  started_at?: string;
}

const conversations = ref<Conversation[]>([]);
const activeConvId = ref<string | null>(null);
const activeBookIds = ref<string[] | null>(null);
const showNewConvForm = ref(false);
const newConvTitle = ref("");
const renamingId = ref<string | null>(null);
const renamingTitle = ref("");
const loadingConversations = ref(false);

async function loadConversations() {
  loadingConversations.value = true;
  try {
    const data = await tutorApi.getConversations();
    conversations.value = data.conversations ?? [];
  } catch {
    conversations.value = [];
  } finally {
    loadingConversations.value = false;
  }
}

async function createConversation() {
  const title = newConvTitle.value.trim();
  try {
    const data = await tutorApi.createConversation(subjectId.value, title || undefined);
    showNewConvForm.value = false;
    newConvTitle.value = "";
    await loadConversations();
    if (data.conversation?.id) selectConversation(data.conversation.id);
  } catch {
    // silent
  }
}

async function selectConversation(id: string) {
  activeConvId.value = id;
  messages.length = 0;
  // Load history
  try {
    const data = await tutorApi.getConversation(id);
    const conv = data.conversation ?? (data as unknown as { messages: Array<{ role: string; content: string }> });
    const msgs = conv.messages ?? [];
    for (const m of msgs) {
      const role = (m.role ?? "").toLowerCase();
      const isUser = role === "user" || role === "student" || role === "élève";
      messages.push({
        id: "hist-" + Math.random().toString(36).slice(2),
        role: isUser ? "user" : "tutor",
        content: m.content ?? (m as unknown as { text?: string }).text ?? "",
      });
    }
  } catch {
    // degradation: empty thread
  }
  // Load sources
  await loadConvSources(id);
}

async function renameConversation(conv: Conversation) {
  if (!renamingTitle.value.trim() || renamingTitle.value.trim() === conv.title) {
    renamingId.value = null;
    return;
  }
  try {
    await tutorApi.renameConversation(conv.id, renamingTitle.value.trim());
    conv.title = renamingTitle.value.trim();
  } catch {
    // silent
  }
  renamingId.value = null;
  await loadConversations();
}

async function deleteConversation(conv: Conversation) {
  if (!confirm(`Supprimer « ${conv.title || "Sans titre"} » et tout son historique ?`)) return;
  try {
    await tutorApi.deleteConversation(conv.id);
    if (activeConvId.value === conv.id) {
      activeConvId.value = null;
      activeBookIds.value = null;
      messages.length = 0;
    }
    await loadConversations();
  } catch {
    // silent
  }
}

async function loadConvSources(convId: string) {
  try {
    const data = await tutorApi.getConversationSources(convId);
    activeBookIds.value = data.book_ids?.slice() ?? null;
  } catch {
    activeBookIds.value = null;
  }
}

/* ── Pedagogy controls ───────────────────────────────────────── */
const socratic = ref(false);
const level = ref("Débutant");
const think = ref(false);

/* ── Composer ────────────────────────────────────────────────── */
const prompt = ref("");
const textareaRef = ref<HTMLTextAreaElement | null>(null);

function submitQuestion() {
  const q = prompt.value.trim();
  if (!q) return;
  prompt.value = "";
  ask(q, {
    subjectId: subjectId.value,
    conversationId: activeConvId.value ?? undefined,
    socratic: socratic.value,
    level: level.value,
    think: think.value,
    bookIds: activeBookIds.value !== null ? activeBookIds.value : undefined,
  });
  scrollToBottom();
}

function regenerate() {
  if (streaming.value) return;
  // Find the last user message
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  if (!lastUser) return;
  ask(lastUser.content, {
    subjectId: subjectId.value,
    conversationId: activeConvId.value ?? undefined,
    socratic: socratic.value,
    level: level.value,
    think: think.value,
    bookIds: activeBookIds.value !== null ? activeBookIds.value : undefined,
  });
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submitQuestion();
  }
}

/* ── Auto-scroll ─────────────────────────────────────────────── */
const threadRef = ref<HTMLDivElement | null>(null);

function scrollToBottom() {
  nextTick(() => {
    if (threadRef.value) threadRef.value.scrollTop = threadRef.value.scrollHeight;
  });
}

watch(currentContent, scrollToBottom);
watch(messages, scrollToBottom, { deep: true });

/* ── Sources modal ───────────────────────────────────────────── */
const showSourcesModal = ref(false);
const allBooks = computed(() => state.data?.books ?? []);
const tempBookSelection = ref<Set<string>>(new Set());

function openSourcesModal() {
  tempBookSelection.value = new Set(activeBookIds.value ?? []);
  showSourcesModal.value = true;
}

async function saveSources() {
  if (!activeConvId.value) return;
  const ids = [...tempBookSelection.value];
  try {
    await tutorApi.setConversationSources(activeConvId.value, ids);
    activeBookIds.value = ids.length ? ids : null;
  } catch {
    // silent
  }
  showSourcesModal.value = false;
}

function toggleBook(bookId: string) {
  if (tempBookSelection.value.has(bookId)) {
    tempBookSelection.value.delete(bookId);
  } else {
    tempBookSelection.value.add(bookId);
  }
}

/* ── Simple markdown renderer ────────────────────────────────── */
function renderMarkdown(raw: string): string {
  if (!raw) return "";
  let html = raw;

  // Code blocks: ```lang\n...\n```
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, lang: string, code: string) => {
    const escaped = escapeHtml(code.replace(/\n$/, ""));
    const langLabel = lang
      ? `<div class="code-lang">${escapeHtml(lang)}</div>`
      : "";
    return `<pre class="code-block">${langLabel}<code>${highlightSyntax(escaped, lang)}</code></pre>`;
  });

  // Headings
  html = html.replace(/^######\s+(.+)$/gm, '<div class="md-h md-h6">$1</div>');
  html = html.replace(/^#####\s+(.+)$/gm, '<div class="md-h md-h5">$1</div>');
  html = html.replace(/^####\s+(.+)$/gm, '<div class="md-h md-h4">$1</div>');
  html = html.replace(/^###\s+(.+)$/gm, '<div class="md-h md-h3">$1</div>');
  html = html.replace(/^##\s+(.+)$/gm, '<div class="md-h md-h2">$1</div>');
  html = html.replace(/^#\s+(.+)$/gm, '<div class="md-h md-h1">$1</div>');

  // Horizontal rule
  html = html.replace(/^(\s*[-*_]{3,})\s*$/gm, '<hr class="md-hr" />');

  // Unordered lists
  html = html.replace(/^[\-\*+]\s+(.+)$/gm, '<li class="md-li">$1</li>');

  // Ordered lists
  html = html.replace(/^\d+[.)]\s+(.+)$/gm, '<li class="md-oli">$1</li>');

  // Wrap consecutive <li> in <ul>, consecutive <li class="md-oli"> in <ol>
  html = html.replace(/((?:<li class="md-li">[\s\S]*?<\/li>\n?)+)/g, (match) => {
    return `<ul class="md-list">${match.replace(/<\/?li class="md-li">/g, (li) => li.replace(' class="md-li"', ""))}</ul>`;
  });
  html = html.replace(/((?:<li class="md-oli">[\s\S]*?<\/li>\n?)+)/g, (match) => {
    return `<ol class="md-list">${match.replace(/<li class="md-oli">/g, "<li>").replace(/<\/li>\n?/g, "</li>\n")}</ol>`;
  });

  // Inline: bold, italic, inline code, links
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // Block quotes
  html = html.replace(/^&gt;\s+(.+)$/gm, '<blockquote class="md-quote">$1</blockquote>');

  // Paragraphs: wrap bare text lines
  html = html.replace(/^(?!<[a-z])((?!$).+)$/gm, '<div class="tutor-para">$1</div>');

  return html;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ── Simple syntax highlighter (Python, JS, Bash, JSON) ─────── */
function highlightSyntax(code: string, lang: string): string {
  const l = lang.toLowerCase();
  if (l === "python" || l === "py") return highlightPython(code);
  if (l === "javascript" || l === "js" || l === "ts" || l === "typescript") return highlightJS(code);
  if (l === "bash" || l === "sh" || l === "shell") return highlightBash(code);
  if (l === "json") return highlightJSON(code);
  return code;
}

function highlightPython(code: string): string {
  const kw = /\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|pass|break|continue|and|or|not|in|is|None|True|False|global|nonlocal|yield|raise|assert|del)\b/g;
  const str = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/g;
  const cmt = /(#[^\n]*)/g;
  const num = /\b(\d+(?:\.\d+)?)\b/g;
  const fn = /\b([A-Za-z_][A-Za-z0-9_]*(?=\())/g;
  return applyHighlight(code, [
    { re: cmt, cls: "tok c" },
    { re: str, cls: "tok s" },
    { re: kw, cls: "tok k" },
    { re: num, cls: "tok n" },
    { re: fn, cls: "tok f" },
  ]);
}

function highlightJS(code: string): string {
  const kw = /\b(function|const|let|var|return|if|else|for|while|class|import|export|from|new|try|catch|finally|throw|async|await|typeof|instanceof|this|true|false|null|undefined)\b/g;
  const str = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)/g;
  const cmt = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)/g;
  const num = /\b(\d+(?:\.\d+)?)\b/g;
  const fn = /\b([A-Za-z_$][A-Za-z0-9_$]*(?=\())/g;
  return applyHighlight(code, [
    { re: cmt, cls: "tok c" },
    { re: str, cls: "tok s" },
    { re: kw, cls: "tok k" },
    { re: num, cls: "tok n" },
    { re: fn, cls: "tok f" },
  ]);
}

function highlightBash(code: string): string {
  const kw = /\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|return|export|local|echo|cd|sudo|apt|pip|python|git|mkdir|rm|cp|mv)\b/g;
  const str = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/g;
  const cmt = /(#[^\n]*)/g;
  const varRef = /(\$[A-Za-z_][A-Za-z0-9_]*)/g;
  return applyHighlight(code, [
    { re: cmt, cls: "tok c" },
    { re: str, cls: "tok s" },
    { re: kw, cls: "tok k" },
    { re: varRef, cls: "tok v" },
  ]);
}

function highlightJSON(code: string): string {
  const key = /("(?:\\.|[^"\\])*")(\s*:)/g;
  const str = /("(?:\\.|[^"\\])*")/g;
  const kw = /\b(true|false|null)\b/g;
  const num = /(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)/g;
  // Apply key first, then the rest with a wrapper
  let result = code;
  result = result.replace(key, '<span class="tok p">$1</span>$2');
  // Wrap remaining strings (not already highlighted)
  result = result.replace(/(?<!<[^>]*)("(?:\\.|[^"\\])*")/g, '<span class="tok s">$1</span>');
  result = result.replace(kw, '<span class="tok k">$1</span>');
  result = result.replace(num, '<span class="tok n">$1</span>');
  return result;
}

interface TokenRule { re: RegExp; cls: string }

function applyHighlight(code: string, rules: TokenRule[]): string {
  // Simple sequential replacement — not perfect for overlapping matches but good enough
  let result = code;
  for (const rule of rules) {
    result = result.replace(rule.re, `<span class="${rule.cls}">$1</span>`);
  }
  return result;
}

/* ── Lifecycle ───────────────────────────────────────────────── */
onMounted(async () => {
  connect();
  await loadConversations();
  // Restore last active conversation from localStorage
  const saved = localStorage.getItem("edunexus.activeConv");
  if (saved && conversations.value.some((c) => c.id === saved)) {
    selectConversation(saved);
  }
});

watch(activeConvId, (id) => {
  if (id) localStorage.setItem("edunexus.activeConv", id);
  else localStorage.removeItem("edunexus.activeConv");
});

/* ── Helpers ─────────────────────────────────────────────────── */
function formatDate(ts?: string): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString("fr-FR", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function sourceLabel(s: TutorSource, i: number): string {
  const bits = [s.book ?? s.title ?? "Source"];
  if (s.chapter) bits.push(s.chapter);
  if (s.page != null) bits.push("p." + s.page);
  return bits.join(" — ") || `Source ${i + 1}`;
}

function sourceChipLabel(s: TutorSource, i: number): string {
  const where = [s.chapter, s.page != null ? "p. " + s.page : null].filter(Boolean).join(" · ");
  return (s.book ?? s.title ?? "Source") + (where ? " — " + where : "");
}

const expandedCiteMap = ref<Record<string, number | undefined>>({});

function toggleCiteDetail(msgId: string, i: number) {
  if (expandedCiteMap.value[msgId] === i) {
    expandedCiteMap.value[msgId] = undefined;
  } else {
    expandedCiteMap.value[msgId] = i;
  }
}

const sourcesLabel = computed(() => {
  if (activeBookIds.value === null) return "Sources : toutes";
  if (!activeBookIds.value.length) return "Aucune source active";
  return `Sources : ${activeBookIds.value.length} document${activeBookIds.value.length > 1 ? "s" : ""}`;
});

const sourcesClass = computed(() => {
  return activeBookIds.value !== null && !activeBookIds.value.length ? "none" : "";
});
</script>

<template>
  <section class="page tutor-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('tutor.kicker') }}</p>
        <h1>{{ t('tutor.title') }}</h1>
        <p>{{ t('tutor.copy') }}</p>
      </div>
    </header>

    <section class="tutor-layout">
      <!-- ═══════════════════ LEFT: Chat shell ═══════════════════ -->
      <article class="chat-shell content-panel">
        <div class="conv-layout">
          <!-- ── Conversation sidebar ── -->
          <aside class="conv-side" aria-label="Liste des conversations">
            <div class="conv-side-head">
              <h3>Conversations</h3>
              <button type="button" class="btn-primary-sm" @click="showNewConvForm = !showNewConvForm" aria-label="Nouvelle conversation">
                <Plus :size="14" aria-hidden="true" /> Nouvelle
              </button>
            </div>

            <form v-if="showNewConvForm" class="newconv-form" @submit.prevent="createConversation">
              <input v-model="newConvTitle" type="text" placeholder="Titre (optionnel)…" aria-label="Titre de la nouvelle conversation" />
              <div class="row-btns">
                <button type="submit" class="btn-primary-sm">Créer</button>
                <button type="button" class="btn-ghost-sm" @click="showNewConvForm = false">Annuler</button>
              </div>
            </form>

            <div class="conv-list" role="listbox" aria-label="Conversations existantes">
              <div v-if="!conversations.length" class="empty-note">
                Aucune conversation.<br>Cliquez « + Nouvelle » pour démarrer.
              </div>
              <div
                v-for="conv in conversations"
                :key="conv.id"
                class="conv-item"
                :class="{ active: conv.id === activeConvId }"
                role="option"
                :aria-selected="conv.id === activeConvId"
                @click="selectConversation(conv.id)"
              >
                <div class="ci-top">
                  <span v-if="renamingId !== conv.id" class="ci-title">{{ conv.title || "Sans titre" }}</span>
                  <input
                    v-else
                    v-model="renamingTitle"
                    type="text"
                    class="ci-title-input"
                    @keydown.enter.prevent="renameConversation(conv)"
                    @keydown.escape="renamingId = null"
                    @blur="renameConversation(conv)"
                  />
                  <span class="ci-actions">
                    <button
                      type="button" class="ci-act ren" title="Renommer"
                      @click.stop="renamingId = conv.id; renamingTitle = conv.title || 'Sans titre'"
                    >✎</button>
                    <button
                      type="button" class="ci-act del" title="Supprimer"
                      @click.stop="deleteConversation(conv)"
                    >×</button>
                  </span>
                </div>
                <div class="ci-meta">
                  <span>{{ conv.subject_name || "espace" }}</span>
                  <span v-if="conv.message_count != null">{{ conv.message_count }} msg</span>
                </div>
              </div>
            </div>
          </aside>

          <!-- ── Main conversation area ── -->
          <main class="conv-main" aria-label="Fil de conversation">
            <!-- Pedagogy controls -->
            <div class="pedagogy-row" role="group" aria-label="Réglages pédagogiques et sources">
              <label class="chip-toggle">
                <input v-model="socratic" type="checkbox" /> Socratique
              </label>
              <label class="chip-toggle">
                Niveau
                <select v-model="level" aria-label="Niveau pédagogique">
                  <option value="Débutant">Débutant</option>
                  <option value="Intermédiaire">Intermédiaire</option>
                  <option value="Avancé">Avancé</option>
                  <option value="Expert">Expert</option>
                </select>
              </label>
              <label class="chip-toggle">
                <input v-model="think" type="checkbox" /> Think (réflexion)
              </label>
              <button
                type="button" class="sources-btn" :class="sourcesClass"
                title="Choisir les sources qui alimentent les réponses"
                @click="openSourcesModal"
              >
                <FileText :size="12" aria-hidden="true" />
                <span>{{ sourcesLabel }}</span>
              </button>
            </div>

            <!-- Thread -->
            <div ref="threadRef" class="thread" role="log" aria-live="polite" aria-label="Fil de la conversation">
              <div v-if="!messages.length" class="thread-empty">
                <div class="big">Votre conversation commence ici.</div>
                Posez vos questions : avec des sources actives, les réponses sont citées
                page par page ; sans source, le tuteur répond aussi, hors documents.
                <br /><br />
                Ouvrez ou créez une conversation à gauche — sinon la discussion
                reste une séance rapide non enregistrée.
              </div>

              <template v-for="msg in messages" :key="msg.id">
                <!-- User message -->
                <div v-if="msg.role === 'user'" class="msg msg-student">
                  <div class="bubble-student">{{ msg.content }}</div>
                </div>

                <!-- Tutor message (streaming or complete) -->
                <div v-else class="msg msg-tutor">
                  <div v-if="msg.id === '_streaming' && !currentContent && !currentThinking" class="msg-kind">
                    <Loader2 :size="12" class="spin" aria-hidden="true" /> Tuteur — réponse en cours…
                  </div>
                  <div v-else class="msg-kind">Tuteur — réponse sourcée</div>

                  <div class="bubble-tutor">
                    <!-- Source citations -->
                    <div
                      v-if="(msg.id === '_streaming' ? currentSources : msg.sources)?.length"
                      class="cite-row"
                    >
                      <button
                        v-for="(src, si) in (msg.id === '_streaming' ? currentSources : msg.sources)"
                        :key="si"
                        type="button"
                        class="cite-chip"
                        :title="sourceChipLabel(src, si)"
                        @click="toggleCiteDetail(msg.id, si)"
                      >
                        <FileText :size="10" aria-hidden="true" />
                        {{ si + 1 }}<template v-if="src.page != null"> p.{{ src.page }}</template>
                      </button>
                    </div>

                    <!-- Citation detail -->
                    <div
                      v-if="expandedCiteMap[msg.id] != null && (msg.sources ?? currentSources)?.[expandedCiteMap[msg.id]!]"
                      class="cite-detail"
                    >
                      Livre : {{ (msg.sources ?? currentSources)![expandedCiteMap[msg.id]!].book ?? "?" }}<br />
                      <template v-if="(msg.sources ?? currentSources)![expandedCiteMap[msg.id]!].chapter">
                        Chapitre : {{ (msg.sources ?? currentSources)![expandedCiteMap[msg.id]!].chapter }}<br />
                      </template>
                      <template v-if="(msg.sources ?? currentSources)![expandedCiteMap[msg.id]!].page != null">
                        Page : {{ (msg.sources ?? currentSources)![expandedCiteMap[msg.id]!].page }}<br />
                      </template>
                    </div>

                    <div class="tutor-body">
                      <!-- Thinking -->
                      <div
                        v-if="msg.id === '_streaming' ? currentThinking : msg.thinking"
                        class="tutor-think"
                      >{{ msg.id === '_streaming' ? currentThinking : msg.thinking }}</div>

                      <!-- Content (markdown rendered) -->
                      <div
                        v-if="msg.id === '_streaming' ? currentContent : msg.content"
                        class="tutor-text"
                        v-html="renderMarkdown(msg.id === '_streaming' ? currentContent : msg.content)"
                      ></div>
                    </div>

                    <!-- Error -->
                    <div v-if="msg.error" class="error-line">{{ msg.error }}</div>

                    <!-- Warning -->
                    <div v-if="msg.warning" class="warn-line">{{ msg.warning }}</div>

                    <!-- Stats -->
                    <div v-if="msg.id === '_streaming' ? currentStats : msg.stats" class="msg-stats">
                      {{
                        (() => {
                          const s = msg.id === '_streaming' ? currentStats : msg.stats;
                          if (!s) return "";
                          const gen = Math.max(0, Number(s.generated_tokens ?? s.token_count) || 0);
                          const speed = Math.max(0, Number(s.tok_s ?? s.tokens_per_sec) || 0);
                          const bits: string[] = [];
                          if (gen > 0) bits.push(gen + " token" + (gen > 1 ? "s" : ""));
                          if (speed > 0) bits.push(speed.toFixed(1).replace(".", ",") + " tok/s");
                          return bits.join(" · ");
                        })()
                      }}
                    </div>
                  </div>

                  <!-- Regenerate button (only on last tutor message) -->
                  <div
                    v-if="msg.id === '_streaming' || msg === [...messages].reverse().find(m => m.role === 'tutor')"
                    class="hint-under"
                  >
                    <button
                      type="button"
                      class="btn-ghost-sm regen-btn"
                      :disabled="streaming"
                      @click="regenerate"
                    >
                      <RefreshCw :size="13" aria-hidden="true" /> Régénérer
                    </button>
                  </div>
                </div>
              </template>
            </div>

            <!-- Composer -->
            <form class="composer" @submit.prevent="submitQuestion">
              <textarea
                ref="textareaRef"
                v-model="prompt"
                rows="1"
                placeholder="Interrogez le tuteur… (Entrée : envoyer · Maj+Entrée : saut de ligne)"
                aria-label="Votre question"
                :disabled="streaming"
                @keydown="handleKeydown"
              />
              <button
                v-if="!streaming"
                type="submit"
                class="btn-primary-send"
                :disabled="!prompt.trim()"
                aria-label="Envoyer"
              >
                <ArrowUp :size="16" aria-hidden="true" />
              </button>
              <button
                v-else
                type="button"
                class="btn-danger-send"
                aria-label="Annuler la génération"
                @click="cancel"
              >
                <StopCircle :size="16" aria-hidden="true" />
              </button>
            </form>
          </main>
        </div>
      </article>

      <!-- ═══════════════════ RIGHT: Info sidebar ═══════════════════ -->
      <aside class="tutor-side">
        <article class="content-panel">
          <p class="eyebrow">{{ t('tutor.tip') }}</p>
          <h2>{{ t('tutor.askCitation') }}</h2>
          <p>{{ t('tutor.citationReason') }}</p>
        </article>
        <article class="content-panel">
          <p class="eyebrow">{{ t('tutor.mode') }}</p>
          <h2>{{ t('tutor.hint') }}</h2>
          <p>{{ t('tutor.hintReason') }}</p>
        </article>
        <article class="content-panel tutor-status-panel">
          <p class="eyebrow">Connexion</p>
          <div class="status-row">
            <span class="status-dot" :class="connected ? 'online' : 'offline'"></span>
            <span>{{ connected ? 'Connecté' : 'Déconnecté' }}</span>
          </div>
          <div v-if="status !== 'prêt'" class="status-row">
            <span class="status-label">{{ status }}</span>
          </div>
        </article>
      </aside>
    </section>

    <!-- ═══════════════════ Sources modal ═══════════════════ -->
    <Teleport to="body">
      <div v-if="showSourcesModal" class="modal-backdrop" @click.self="showSourcesModal = false">
        <div class="modal-panel sources-modal">
          <div class="modal-head">
            <h3>Sources actives</h3>
            <button type="button" class="icon-btn" aria-label="Fermer" @click="showSourcesModal = false">
              <X :size="18" />
            </button>
          </div>
          <div class="modal-body">
            <div v-if="!allBooks.length" class="empty-note">Aucune source disponible.</div>
            <label
              v-for="book in allBooks"
              :key="book.id"
              class="book-row"
            >
              <input
                type="checkbox"
                :checked="tempBookSelection.has(book.id)"
                @change="toggleBook(book.id)"
              />
              <span class="book-title">{{ book.title }}</span>
              <span class="book-chapter">{{ book.chapter }}</span>
            </label>
          </div>
          <div class="modal-foot">
            <button type="button" class="btn-ghost-sm" @click="showSourcesModal = false">Annuler</button>
            <button type="button" class="btn-primary-sm" @click="saveSources">Enregistrer</button>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
/* ═══════════════════════════════════════════════════════════════
   TutorView — Chat with streaming, conversation sidebar, sources
   Uses existing design system: --indigo, --ink, --muted, etc.
   ═══════════════════════════════════════════════════════════════ */

/* ── Layout ────────────────────────────────────────────────── */
.tutor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.24fr) minmax(250px, 0.55fr);
  gap: 16px;
  align-items: start;
}

/* ── Chat shell (full-height card) ─────────────────────────── */
.chat-shell {
  min-height: 520px;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.conv-layout {
  flex: 1;
  display: flex;
  min-height: 0;
}

/* ── Conversation sidebar ──────────────────────────────────── */
.conv-side {
  width: 300px;
  flex: none;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--line);
  background: var(--panel-soft, #f8f9ff);
}

.conv-side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px 8px;
}

.conv-side-head h3 {
  margin: 0;
  font-size: 15px;
  font-family: "Fraunces", Georgia, serif;
}

.btn-primary-sm {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 30px;
  padding: 5px 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: #fff;
  background: linear-gradient(135deg, var(--indigo), var(--indigo-deep));
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: filter 0.15s;
}
.btn-primary-sm:hover { filter: brightness(1.08); }

.btn-ghost-sm {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 30px;
  padding: 5px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--muted);
  background: transparent;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-ghost-sm:hover { border-color: var(--indigo); color: var(--ink); }

.newconv-form {
  padding: 0 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  border-bottom: 1px solid var(--line);
}

.newconv-form input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  font-size: 13px;
  background: #fff;
}

.row-btns {
  display: flex;
  gap: 6px;
}

.conv-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.conv-list::-webkit-scrollbar { width: 7px; }
.conv-list::-webkit-scrollbar-thumb {
  background: var(--line);
  border-radius: 99px;
}

.empty-note {
  padding: 20px 14px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
  line-height: 1.5;
}

.conv-item {
  padding: 10px 14px 9px;
  border-bottom: 1px solid var(--line-soft, #eaecf5);
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}
.conv-item:hover { background: rgba(255,255,255,0.7); }
.conv-item.active {
  background: var(--indigo-soft);
  border-left-color: var(--indigo);
}

.ci-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.ci-title {
  font-weight: 600;
  font-size: 13.5px;
  overflow-wrap: anywhere;
}

.ci-title-input {
  font-size: 13px;
  padding: 2px 6px;
  border: 1px solid var(--indigo);
  border-radius: 4px;
  flex: 1;
  min-width: 0;
}

.ci-actions {
  display: flex;
  gap: 2px;
  flex: none;
  opacity: 0;
  transition: opacity 0.15s;
}
.conv-item:hover .ci-actions,
.conv-item.active .ci-actions { opacity: 1; }

.ci-act {
  border: none;
  background: none;
  color: var(--line);
  border-radius: 6px;
  padding: 2px 5px;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}
.ci-act:hover { color: var(--indigo); background: var(--indigo-soft); }
.ci-act.del:hover { color: #b23a41; background: #fbe9e9; }

.ci-meta {
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 10.5px;
  color: var(--muted);
  margin-top: 3px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* ── Main area ─────────────────────────────────────────────── */
.conv-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

/* ── Pedagogy row ──────────────────────────────────────────── */
.pedagogy-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--panel-soft, #f8f9ff);
}

.chip-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 11px;
  font-size: 12.5px;
  color: var(--muted);
  background: #fff;
}
.chip-toggle input { accent-color: var(--indigo); width: 14px; height: 14px; margin: 0; }
.chip-toggle select {
  border: none;
  background: transparent;
  padding: 0;
  width: auto;
  font-size: 12.5px;
  color: var(--ink);
}

.sources-btn {
  margin-left: auto;
  border: 1px solid #e8d5ae;
  background: #faf3e2;
  color: #a86a12;
  border-radius: 999px;
  padding: 3px 13px;
  font-size: 12.5px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: filter 0.15s;
}
.sources-btn:hover { filter: brightness(1.04); }
.sources-btn.none {
  border-color: var(--line);
  background: #fff;
  color: var(--muted);
}

/* ── Thread ────────────────────────────────────────────────── */
.thread {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 18px 8px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.thread::-webkit-scrollbar { width: 9px; }
.thread::-webkit-scrollbar-thumb {
  background: var(--line);
  border-radius: 999px;
  border: 2px solid #fff;
}

.thread-empty {
  margin: auto;
  text-align: center;
  color: var(--muted);
  max-width: 400px;
  border: 1px dashed var(--line);
  border-radius: 16px;
  background: var(--panel-soft, #f8f9ff);
  padding: 34px 22px;
}
.thread-empty .big {
  font-family: "Fraunces", Georgia, serif;
  font-size: 21px;
  color: var(--ink);
  margin-bottom: 6px;
}

/* ── Messages ──────────────────────────────────────────────── */
.msg {
  display: flex;
  flex-direction: column;
  max-width: 88%;
  animation: rise 0.22s ease;
}
@keyframes rise { from { opacity: 0; transform: translateY(6px); } }

.msg-student { align-self: flex-end; align-items: flex-end; }
.msg-tutor { align-self: flex-start; align-items: stretch; }

.bubble-student {
  background: linear-gradient(160deg, var(--indigo-soft), #e3e0f8);
  border: 1px solid #cfcaf1;
  border-radius: 16px 16px 5px 16px;
  padding: 10px 14px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.5;
}

.bubble-tutor {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 5px 16px 16px 16px;
  padding: 11px 15px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.msg-kind {
  font-size: 10.5px;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 5px;
}

.tutor-body {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tutor-think {
  color: var(--muted);
  font-style: italic;
  display: block;
  margin-bottom: 6px;
}

.tutor-text {
  display: block;
}

.hint-under {
  margin-top: 6px;
}

.regen-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

/* ── Markdown rendering ────────────────────────────────────── */
:deep(.tutor-para) {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 2px 0;
}

:deep(.md-h) {
  font-family: "Fraunces", Georgia, serif;
  font-weight: 700;
  line-height: 1.3;
  margin: 10px 0 4px;
}
:deep(.md-h1) { font-size: 17px; }
:deep(.md-h2) { font-size: 15.5px; }
:deep(.md-h3) { font-size: 14.5px; }
:deep(.md-h4), :deep(.md-h5), :deep(.md-h6) { font-size: 13.5px; }

:deep(.md-list) {
  margin: 6px 0 6px 20px;
  padding: 0;
  list-style: disc;
}
:deep(.md-list li) { margin: 2px 0; }

:deep(.md-hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 10px 0;
}

:deep(.md-inline-code) {
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 0.9em;
  background: var(--panel-soft, #f8f9ff);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 0 4px;
  color: var(--indigo-deep);
}

:deep(.md-quote) {
  border-left: 3px solid var(--indigo);
  padding-left: 12px;
  margin: 8px 0;
  color: var(--muted);
  font-style: italic;
}

:deep(.code-block) {
  position: relative;
  margin: 8px 0;
  padding: 10px 12px;
  background: #1e2230;
  border: 1px solid #2c3142;
  border-radius: 8px;
  overflow-x: auto;
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 12.5px;
  line-height: 1.55;
  color: #d6dae6;
  white-space: pre;
}

:deep(.code-block code) {
  font-family: inherit;
  background: transparent;
}

:deep(.code-lang) {
  position: absolute;
  top: 0;
  right: 0;
  padding: 2px 8px;
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #8b93a8;
  background: #2c3142;
  border-radius: 0 8px 0 8px;
}

:deep(.tok.k) { color: #c792ea; }
:deep(.tok.s) { color: #a5d6a7; }
:deep(.tok.c) { color: #7d8597; font-style: italic; }
:deep(.tok.n) { color: #f78c6c; }
:deep(.tok.f) { color: #82aaff; }
:deep(.tok.v) { color: #ffcb6b; }
:deep(.tok.p) { color: #89ddff; }

/* ── Citations ─────────────────────────────────────────────── */
.cite-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 2px 0 8px;
}

.cite-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #e8d5ae;
  background: #faf3e2;
  color: #a86a12;
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 11px;
  font-weight: 700;
  border-radius: 999px;
  padding: 1px 9px;
  cursor: pointer;
  transition: transform 0.12s, box-shadow 0.12s;
}
.cite-chip:hover {
  transform: translateY(-1px);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.cite-detail {
  margin: 4px 0 6px;
  padding: 8px 10px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  font-size: 12px;
  color: var(--muted);
  background: var(--panel-soft, #f8f9ff);
  white-space: pre-wrap;
  line-height: 1.5;
}

.msg-stats {
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 11px;
  color: var(--muted);
  margin-top: 7px;
}

.error-line {
  color: #b23a41;
  font-size: 13px;
  margin-top: 6px;
  white-space: pre-wrap;
}

.warn-line {
  color: #97600a;
  font-size: 13px;
  margin-top: 6px;
  white-space: pre-wrap;
}

/* ── Composer ──────────────────────────────────────────────── */
.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid var(--line);
  background: #fff;
}

.composer textarea {
  flex: 1;
  min-height: 40px;
  max-height: 160px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.45;
  resize: none;
  background: #fff;
}
.composer textarea:focus {
  border-color: var(--indigo);
  box-shadow: 0 0 0 3px rgba(79,70,229,0.1);
  outline: 0;
}

.btn-primary-send {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  color: #fff;
  background: var(--indigo);
  border: none;
  cursor: pointer;
  transition: filter 0.15s;
  flex-shrink: 0;
}
.btn-primary-send:hover:not(:disabled) { filter: brightness(1.1); }
.btn-primary-send:disabled { opacity: 0.45; cursor: not-allowed; }

.btn-danger-send {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  color: #fff;
  background: #b23a41;
  border: none;
  cursor: pointer;
  transition: filter 0.15s;
  flex-shrink: 0;
}
.btn-danger-send:hover { filter: brightness(1.1); }

/* ── Right sidebar ─────────────────────────────────────────── */
.tutor-side {
  display: grid;
  gap: 15px;
}
.tutor-side .content-panel {
  padding: 21px;
}
.tutor-side h2 {
  margin: 5px 0;
  font: 700 22px/1.1 "Fraunces", Georgia, serif;
}

.tutor-status-panel .status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.status-dot.online { background: #17a572; }
.status-dot.offline { background: #b23a41; }
.status-label { color: var(--muted); font-size: 12px; }

/* ── Sources modal ─────────────────────────────────────────── */
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: grid;
  place-items: center;
  background: rgba(0,0,0,0.35);
  backdrop-filter: blur(4px);
}

.modal-panel {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 16px 48px rgba(0,0,0,0.15);
  max-width: 500px;
  width: 90vw;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line);
}
.modal-head h3 {
  margin: 0;
  font: 700 17px/1 "Fraunces", Georgia, serif;
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}
.icon-btn:hover { background: var(--indigo-soft); color: var(--ink); }

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 20px;
}

.book-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line-soft, #eaecf5);
  cursor: pointer;
  font-size: 13px;
}
.book-row input[type="checkbox"] { accent-color: var(--indigo); }
.book-title { font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.book-chapter { color: var(--muted); font-size: 12px; flex: none; }

.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--line);
}

/* ── Spinner animation ─────────────────────────────────────── */
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* ── Responsive ────────────────────────────────────────────── */
@media (max-width: 1120px) {
  .tutor-layout { grid-template-columns: 1fr; }
  .tutor-side { grid-template-columns: repeat(2, 1fr); }
}

@media (max-width: 800px) {
  .conv-layout { flex-direction: column; }
  .conv-side {
    width: 100%;
    max-height: 240px;
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
  .tutor-side { grid-template-columns: 1fr; }
}
</style>
