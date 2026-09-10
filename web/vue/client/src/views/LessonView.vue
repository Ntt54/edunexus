<!-- EduNexus UI direction: Atelier de progression — leçon centrée avec discussion RAG, génération cours/synthèse, exercices et validation. -->
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  BookOpen,
  FileText,
  GraduationCap,
  Loader2,
  Send,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Info,
  X,
} from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { feedSSE, formatElapsed, openEventStream } from "@/services/sse";
import { tutorApi } from "@/services/api";

const route = useRoute();
const router = useRouter();
const { state } = useLearningStore();
const { t } = usePreferences();

const routeId = computed(() => String(route.params.id ?? (route.params as Record<string, unknown>).discussionId ?? ""));
const notionLabel = computed(() => discussion.value?.notion_id ?? t("lesson.discussion"));

/* ── Learner identity ─────────────────────────────────────── */
function learnerId(): string {
  try {
    const v = localStorage.getItem("edunexus.learner") || localStorage.getItem("edunexus:learner") || "";
    if (v) return v;
    const sel = document.querySelector<HTMLSelectElement>("#learnerSelect");
    if (sel?.value) return sel.value;
  } catch { /* ignore */ }
  return "default";
}

/* ── Types ────────────────────────────────────────────────── */
interface LessonDiscussion { id: string; notion_id?: string; path_step_id?: string; learner_id?: string; subject_id?: string; }
interface LessonContent { id: string; kind: string; content: string; sources?: Array<{ book_id?: string; book?: string; chapter?: string; confidence?: number }>; confidence?: number; created_at?: string; model?: string | null; fallback?: boolean; }
interface LessonMsg { id: string; role: string; content: string; sources?: unknown[]; created_at?: string; }
interface ExerciseQuestion { id: string; type: string; statement: string; options?: string[]; answer?: string; }
interface ExerciseAttempt { id: string; questions: ExerciseQuestion[]; score?: number; passed?: boolean; per_question?: Array<{ statement?: string; question_id?: string; given?: string; expected?: string; correct?: boolean; explanation?: string }>; correct_count?: number; total?: number; feedback?: string; }

/* ── State ────────────────────────────────────────────────── */
const loading = ref(true);
const error = ref<string | null>(null);
const discussion = ref<LessonDiscussion | null>(null);
const messages = ref<LessonMsg[]>([]);
const contents = ref<LessonContent[]>([]);
const attempts = ref<ExerciseAttempt[]>([]);
const generating = ref(false);
const exerciseAttempt = ref<ExerciseAttempt | null>(null);
const feedback = ref<{ score: number; passed: boolean; per: ExerciseAttempt["per_question"]; correct: number; total: number } | null>(null);
const composerText = ref("");
const isDeleted = ref(false);

const discussionId = ref("");

/* ── Progressive course generation: honest timer + SSE text ────── */
const courseElapsed = ref(0);
const courseTimerActive = ref(false);
const courseStreaming = ref(false);
const courseStreamText = ref("");
let courseTimer: number | null = null;
let courseAbort: AbortController | null = null;
function startCourseTimer() {
  stopCourseTimer();
  courseElapsed.value = 0;
  courseTimerActive.value = true;
  courseTimer = window.setInterval(() => { courseElapsed.value += 1; }, 1000);
}
function stopCourseTimer() {
  if (courseTimer !== null) {
    clearInterval(courseTimer);
    courseTimer = null;
  }
  courseTimerActive.value = false;
}
onUnmounted(() => {
  stopCourseTimer();
  courseAbort?.abort();
});

/* ── Helpers ──────────────────────────────────────────────── */
function wordCount(s: string): number { return String(s||"").trim().split(/\s+/).filter(Boolean).length; }
function escHtml(s: unknown): string { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }
function lessonTitle(): string {
  if (discussion.value?.notion_id) return discussion.value.notion_id;
  const stepTitle = (discussion.value as unknown as { title?: string })?.title;
  if (stepTitle) return stepTitle;
  return t("lesson.discussion");
}
const statusInfo = computed(() => {
  if (attempts.value.some(a => a.passed)) return { label: t("lesson.statusDone"), tone: "green" as const, cls: "done" };
  if (feedback.value?.passed) return { label: t("lesson.statusDone"), tone: "green" as const, cls: "done" };
  if (isDeleted.value) return { label: t("lesson.statusReadonly"), tone: "slate" as const, cls: "draft" };
  if (generating.value) return { label: t("lesson.statusBusy"), tone: "orange" as const, cls: "active" };
  if (discussion.value) return { label: t("lesson.statusActive"), tone: "orange" as const, cls: "active" };
  return { label: t("lesson.statusDraft"), tone: "slate" as const, cls: "draft" };
});

/* ── Tabs: discussion / course / summary / exercises ──────────── */
type LessonTab = "discussion" | "course" | "summary" | "exercises";
const activeTab = ref<LessonTab>("discussion");
const courseContents = computed(() => contents.value.filter(c => c.kind === "lesson_course"));
const summaryContents = computed(() => contents.value.filter(c => c.kind === "lesson_summary"));

// ── Pastille modèle (contrat generated_contents[].model/fallback) ───
// Nom court : après le dernier « / », sans le tag après « : »
// (ex. « unsloth/Qwen3-8B-GGUF:Q8_0 » ⇒ « Qwen3-8B-GGUF »).
// fallback ou model null/absent (vieux contenus) ⇒ « hors-ligne ».
function contentModel(c: LessonContent): string | null {
  const m = (c as { model?: unknown }).model;
  return typeof m === "string" && m.trim() ? m : null;
}
function isOfflineContent(c: LessonContent): boolean {
  return (c as { fallback?: unknown }).fallback === true || contentModel(c) == null;
}
function shortModelName(model: string): string {
  const afterSlash = model.split("/").pop() ?? model;
  return afterSlash.split(":")[0] || model;
}
const hasNoSources = computed(() => {
  if (!contents.value.length && !messages.value.length) return false;
  if (contents.value.length>0 && contents.value.every(c => !c.sources || c.sources.length===0)) return true;
  if (contents.value.length>0 && contents.value.every(c => Number((c as unknown as { confidence?: number }).confidence || 0)===0)) return true;
  return false;
});

function renderMarkdown(text: string): string {
  const lines = String(text||"").split("\n");
  let out="";
  for(const ln of lines){
    const t=ln.trim();
    if(!t){ out+='<div style="height:6px"></div>'; continue; }
    const hm = /^(#{1,3})\s+(.*)$/.exec(t);
    if(hm){ out+=`<div class="md-h md-h${hm[1].length}">${escHtml(hm[2])}</div>`; continue; }
    if(/^[-*]\s+/.test(t)){ out+=`<div style="margin-left:16px">• ${escHtml(t.replace(/^[-*]\s+/,""))}</div>`; continue; }
    out+=`<div>${escHtml(ln)}</div>`;
  }
  return out;
}
function parseFeedback(raw: unknown): Array<{ statement?: string; question_id?: string; given?: string; expected?: string; correct?: boolean; explanation?: string }> {
  try {
    if (typeof raw === "string") {
      const j = JSON.parse(raw as string);
      return Array.isArray(j) ? j as never[] : [];
    }
    if (Array.isArray(raw)) return raw as never[];
    return [];
  } catch { return []; }
}

/* ── Fetch discussion ─────────────────────────────────────── */
async function loadDiscussion() {
  loading.value = true; error.value = null;
  const id = routeId.value;
  if (!id) { error.value = t("lesson.badId"); loading.value=false; return; }
  const lid = learnerId();
  // Try as discussionId first
  try {
    const payload = await tutorApi.getLessonDiscussion(id) as unknown as { discussion: LessonDiscussion; messages: LessonMsg[]; generated_contents: LessonContent[]; exercise_attempts: ExerciseAttempt[] };
    discussion.value = payload.discussion;
    messages.value = payload.messages ?? [];
    contents.value = payload.generated_contents ?? [];
    attempts.value = payload.exercise_attempts ?? [];
    discussionId.value = payload.discussion.id;
    isDeleted.value = false;
    loading.value = false;
    return;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    // If 404 / not found, treat as stepId -> create discussion
    const isNotFound = /404|introuvable|Discussion inconnue|Unknown/i.test(msg);
    if (!isNotFound) {
      // Try step fallback anyway
    }
  }
  // Fallback: treat routeId as stepId
  try {
    const created = await tutorApi.createLessonDiscussion(id, lid) as unknown as { discussion: LessonDiscussion };
    const disc = created.discussion as unknown as LessonDiscussion;
    discussionId.value = disc.id;
    const payload = await tutorApi.getLessonDiscussion(disc.id) as unknown as { discussion: LessonDiscussion; messages: LessonMsg[]; generated_contents: LessonContent[]; exercise_attempts: ExerciseAttempt[] };
    discussion.value = payload.discussion;
    messages.value = payload.messages ?? [];
    contents.value = payload.generated_contents ?? [];
    attempts.value = payload.exercise_attempts ?? [];
    isDeleted.value = false;
  } catch (e2) {
    // If creation fails, maybe step already deleted but discussion exists via notion_id -> try to fetch via notion? For now error
    error.value = e2 instanceof Error ? e2.message : t("lesson.askError");
    isDeleted.value = true;
  } finally {
    loading.value = false;
  }
}

onMounted(loadDiscussion);

/* ── Generation actions ───────────────────────────────────── */
async function generateCourse() {
  if (generating.value || !discussionId.value) return;
  generating.value = true;
  error.value = null;
  courseStreamText.value = "";
  courseStreaming.value = false;
  const coursesBefore = courseContents.value.length;
  startCourseTimer();
  try {
    if (await tryStreamCourse()) {
      await refreshCourseContents(coursesBefore);
    } else {
      // Repli classique : l'endpoint de stream n'existe pas (404) ou ne
      // parle pas SSE — attente honnête (minuteur) puis texte final.
      await classicGenerateCourse();
    }
    activeTab.value = "course";
    await nextTick();
  } catch (e) { error.value = e instanceof Error ? e.message : t("lesson.askError"); }
  finally {
    stopCourseTimer();
    generating.value = false;
  }
}
async function classicGenerateCourse() {
  const r = await tutorApi.generateCourse(discussionId.value, learnerId()) as { content: LessonContent };
  const c = r.content as unknown as LessonContent;
  contents.value = [c as LessonContent, ...contents.value];
}
/** Recharge les contenus persistés après un stream ; si le serveur n'a
 *  rien persisté, le texte accumulé devient une carte locale — le texte
 *  déjà affiché n'est jamais perdu. */
async function refreshCourseContents(coursesBefore: number) {
  try {
    const payload = await tutorApi.getLessonDiscussion(discussionId.value) as unknown as {
      generated_contents: LessonContent[]; messages: LessonMsg[]; exercise_attempts: ExerciseAttempt[];
    };
    if (Array.isArray(payload.generated_contents)) contents.value = payload.generated_contents;
    if (Array.isArray(payload.messages)) messages.value = payload.messages;
    if (Array.isArray(payload.exercise_attempts)) attempts.value = payload.exercise_attempts;
  } catch { /* repli : carte locale ci-dessous */ }
  const text = courseStreamText.value.trim();
  if (text && courseContents.value.length <= coursesBefore) {
    contents.value = [
      { id: "local-course-" + Date.now(), kind: "lesson_course", content: courseStreamText.value } as LessonContent,
      ...contents.value,
    ];
  }
  courseStreamText.value = "";
  courseStreaming.value = false;
}
/** Tente le stream SSE ; `false` ⇒ repli classique, jamais d'écran cassé. */
async function tryStreamCourse(): Promise<boolean> {
  courseAbort?.abort();
  courseAbort = new AbortController();
  const url = tutorApi.courseStreamUrl(discussionId.value, learnerId());
  const body = await openEventStream(url, courseAbort.signal);
  if (!body) return false; // 404 / non-SSE / réseau → comportement actuel
  courseStreaming.value = true;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finished = false;
  let streamError: string | null = null;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const fed = feedSSE(buf);
      buf = fed.rest;
      for (const ev of fed.events) {
        if (ev.type === "delta") courseStreamText.value += ev.text;
        else if (ev.type === "done") finished = true;
        else if (ev.type === "error") streamError = ev.message;
      }
      if (finished || streamError) break;
    }
  } catch { /* lecture interrompue : on garde le texte accumulé */ }
  finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }
  if (finished || courseStreamText.value.trim()) return true;
  if (streamError) {
    // Le serveur a parlé (erreur explicite) : on l'affiche, sans
    // régénération aveugle derrière.
    error.value = streamError;
    return true;
  }
  return false;
}
async function generateSummary() {
  if (generating.value || !discussionId.value) return;
  generating.value = true;
  try {
    const r = await tutorApi.generateSummary(discussionId.value, learnerId()) as { content: LessonContent };
    const c = r.content as unknown as LessonContent;
    contents.value = [c as LessonContent, ...contents.value];
    activeTab.value = "summary";
    await nextTick();
  } catch (e) { error.value = e instanceof Error ? e.message : t("lesson.askError"); }
  generating.value = false;
}
async function launchExercises() {
  if (generating.value || !discussionId.value) return;
  generating.value = true;
  feedback.value = null;
  try {
    const r = await tutorApi.generateLessonExercises(discussionId.value, learnerId()) as { attempt: ExerciseAttempt };
    const att = r.attempt as ExerciseAttempt;
    exerciseAttempt.value = att;
    // Also push to attempts list optimistically
    attempts.value = [...attempts.value, att];
    activeTab.value = "exercises";
    await nextTick();
    document.getElementById("exercisesArea")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { error.value = e instanceof Error ? e.message : t("lesson.askError"); }
  generating.value = false;
}
async function submitExercises() {
  const att = exerciseAttempt.value;
  if (!att || !discussionId.value) return;
  generating.value = true;
  try {
    const answers: Record<string, string> = {};
    for (const q of att.questions ?? []) {
      const qid = q.id;
      const radios = document.querySelectorAll<HTMLInputElement>(`input[name="q_${CSS.escape(qid)}"]`);
      if (radios.length) {
        const checked = document.querySelector<HTMLInputElement>(`input[name="q_${CSS.escape(qid)}"]:checked`);
        answers[qid] = checked ? checked.value : "";
      } else {
        const inp = document.getElementById(`q_${qid}`) as HTMLInputElement | null;
        answers[qid] = inp ? inp.value : "";
      }
    }
    const r = await tutorApi.submitLessonExercises(discussionId.value, att.id, answers, learnerId()) as { attempt: ExerciseAttempt };
    const updated = r.attempt as unknown as ExerciseAttempt;
    exerciseAttempt.value = updated;
    // Update attempts list
    const idx = attempts.value.findIndex(a => a.id === att.id);
    if (idx>=0) { const copy=[...attempts.value]; copy[idx]=updated; attempts.value=copy; }
    feedback.value = {
      score: Math.round((updated.score ?? 0)*100),
      passed: !!updated.passed,
      per: (updated.per_question ?? []) as ExerciseAttempt["per_question"],
      correct: Number((updated as unknown as { correct_count?: number }).correct_count ?? 0),
      total: Number((updated as unknown as { total?: number }).total ?? att.questions.length),
    };
    if (updated.passed) {
      // refresh status
    }
    // reload full discussion after delay to show persisted state
    setTimeout(()=>{ loadDiscussion(); }, 900);
  } catch (e) { error.value = e instanceof Error ? e.message : t("lesson.askError"); }
  generating.value = false;
}
async function completeManual() {
  if (!discussionId.value) return;
  generating.value = true;
  try {
    await tutorApi.completeLessonManual(discussionId.value, learnerId());
    // mark as completed locally
    isDeleted.value = false;
    // force status to completed by adding a fake passed attempt
    await loadDiscussion();
  } catch (e) { error.value = e instanceof Error ? e.message : t("lesson.askError"); }
  generating.value = false;
}

/* ── Delete a generated content (course/summary card) ────────── */
const deletingContentId = ref<string | null>(null);
async function deleteContent(contentId: string) {
  if (!contentId || !discussionId.value || deletingContentId.value) return;
  if (!window.confirm(t("lesson.deleteContentConfirm"))) return;
  // Provisional local card (unpersisted stream text): instant removal.
  if (contentId.startsWith("local-")) {
    contents.value = contents.value.filter((c) => c.id !== contentId);
    state.notice = t("lesson.contentDeleted");
    return;
  }
  deletingContentId.value = contentId;
  error.value = null;
  try {
    await tutorApi.deleteLessonContent(discussionId.value, contentId);
    contents.value = contents.value.filter((c) => c.id !== contentId);
    state.notice = t("lesson.contentDeleted");
  } catch (e) {
    const msg = e instanceof Error ? e.message : "";
    if (/404/.test(msg)) {
      // Already gone server-side: sync the list, no crash.
      contents.value = contents.value.filter((c) => c.id !== contentId);
      error.value = t("lesson.contentNotFound");
    } else {
      error.value = msg || t("lesson.deleteError");
    }
  } finally {
    deletingContentId.value = null;
  }
}

/* ── Composer (real RAG round-trip via POST …/ask) ────────────── */const answering = ref(false);
function scrollThreadToBottom() {
  nextTick(() => {
    const el = document.querySelector(".lecon-thread .thread-list");
    el?.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
  });
}
async function sendLessonMessage() {
  const text = composerText.value.trim();
  if (!text || !discussionId.value || answering.value || isDeleted.value) return;
  composerText.value = "";
  error.value = null;
  const lid = learnerId();
  messages.value = [...messages.value, { id: "local-" + Date.now(), role: "user", content: text }];
  scrollThreadToBottom();
  answering.value = true;
  try {
    const r = await tutorApi.askLessonQuestion(discussionId.value, text, lid);
    const answer = String((r as { answer?: unknown }).answer ?? "");
    const sources = Array.isArray((r as { sources?: unknown }).sources)
      ? (r as { sources?: unknown[] }).sources ?? []
      : [];
    if (answer) {
      messages.value = [
        ...messages.value,
        { id: "assistant-" + Date.now(), role: "assistant", content: answer, sources },
      ];
    } else {
      // Empty answer: re-sync from server (messages are persisted there)
      const payload = await tutorApi.getLessonDiscussion(discussionId.value) as unknown as { messages: LessonMsg[] };
      if (Array.isArray(payload.messages)) messages.value = payload.messages;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : t("lesson.askError");
    try {
      const payload = await tutorApi.getLessonDiscussion(discussionId.value) as unknown as { messages: LessonMsg[] };
      if (Array.isArray(payload.messages) && payload.messages.length) messages.value = payload.messages;
    } catch { /* keep optimistic user message */ }
  } finally {
    answering.value = false;
    scrollThreadToBottom();
  }
}

function goBack() { router.push("/parcours"); }
</script>

<template>
  <section class="page lecon-page">
    <!-- Header -->
    <header class="page-intro lecon-intro">
      <div class="lecon-head-left">
        <button type="button" class="secondary-action back-btn" @click="goBack">
          <ArrowLeft :size="16" aria-hidden="true" /> {{ t("lesson.back") }}
        </button>
        <div class="lecon-title-row">
          <h1>{{ lessonTitle() }}</h1>
          <StatusPill :tone="statusInfo.tone">{{ statusInfo.label }}</StatusPill>
        </div>
        <p class="lecon-meta">
          <span v-if="discussion">{{ t("lesson.discussion") }} <code>{{ discussion.id }}</code> · <strong>{{ notionLabel }}</strong></span>
          <span v-else>{{ t("lesson.discussion") }} — {{ t("nav.path") }}</span>
        </p>
      </div>
    </header>

    <!-- Loading -->
    <section v-if="loading" class="content-panel loading-state" style="padding:32px; text-align:center;">
      <Loader2 :size="22" class="spin" aria-hidden="true" />
      <p style="margin-top:8px; color:var(--muted);">{{ t("lesson.loading") }}</p>
    </section>

    <!-- Error -->
    <section v-else-if="error && !discussion" class="content-panel" style="padding:28px; text-align:center;">
      <AlertTriangle :size="36" aria-hidden="true" style="color:var(--orange);" />
      <p style="margin:12px 0 0; color:var(--muted);">{{ error }}</p>
      <button type="button" class="secondary-action" style="margin-top:14px;" @click="loadDiscussion">
        <RotateCcw :size="14" aria-hidden="true" /> {{ t("lesson.retry") }}
      </button>
    </section>

    <!-- Content -->
    <template v-else>
      <section class="content-panel lecon-main">
        <!-- Banners -->
        <div v-if="isDeleted" class="banner banner-gold">
          <Info :size="14" aria-hidden="true" />
          <span>{{ t("lesson.deletedNotice") }} <strong>{{ notionLabel }}</strong>.</span>
        </div>
        <div v-if="hasNoSources" class="banner banner-dashed">
          <BookOpen :size="14" aria-hidden="true" />
          <span>{{ t("lesson.noSources") }}
            <RouterLink to="/sources" class="text-button" style="margin-left:8px;">{{ t("lesson.importSources") }}</RouterLink>
          </span>
        </div>
        <div v-if="generating" class="gen-indicator" role="status" aria-live="polite">
          <Loader2 :size="13" class="spin" aria-hidden="true" />
          <span v-if="courseTimerActive">{{ t("lesson.coursePhase") }} · {{ formatElapsed(courseElapsed) }}</span>
          <span v-else>{{ t("lesson.generating") }}</span>
        </div>

        <!-- Action bar -->
        <div class="lecon-actions">
          <button type="button" class="primary-action" :disabled="generating || isDeleted" @click="generateCourse">
            <Sparkles :size="14" aria-hidden="true" /> {{ t("lesson.generateCourse") }}
          </button>
          <button type="button" class="secondary-action" :disabled="generating || isDeleted" @click="generateSummary">
            <FileText :size="14" aria-hidden="true" /> {{ t("lesson.makeSummary") }}
          </button>
          <button type="button" class="secondary-action" :disabled="generating || isDeleted" @click="launchExercises">
            <GraduationCap :size="14" aria-hidden="true" /> {{ t("lesson.makeExercises") }}
          </button>
        </div>

        <!-- Tabs -->
        <div class="lecon-tabs" role="tablist">
          <button type="button" role="tab" :aria-selected="activeTab === 'discussion'" :class="{ active: activeTab === 'discussion' }" @click="activeTab = 'discussion'">
            {{ t("lesson.discussion") }}
            <span v-if="messages.length" class="tab-count">{{ messages.length }}</span>
          </button>
          <button type="button" role="tab" :aria-selected="activeTab === 'course'" :class="{ active: activeTab === 'course' }" @click="activeTab = 'course'">
            {{ t("lesson.course") }}
            <span v-if="courseContents.length" class="tab-count">{{ courseContents.length }}</span>
          </button>
          <button type="button" role="tab" :aria-selected="activeTab === 'summary'" :class="{ active: activeTab === 'summary' }" @click="activeTab = 'summary'">
            {{ t("lesson.summary") }}
            <span v-if="summaryContents.length" class="tab-count">{{ summaryContents.length }}</span>
          </button>
          <button type="button" role="tab" :aria-selected="activeTab === 'exercises'" :class="{ active: activeTab === 'exercises' }" @click="activeTab = 'exercises'">
            {{ t("lesson.exercises") }}
            <span v-if="attempts.length" class="tab-count">{{ attempts.length }}</span>
          </button>
        </div>

        <!-- Tab: generated course -->
        <div v-show="activeTab === 'course'" role="tabpanel" class="lecon-contents">
          <article
            v-if="generating && courseStreamText"
            class="notebook-output streaming-card"
            style="border-left:3px solid var(--indigo)"
            aria-live="polite"
          >
            <div class="notebook-output-head">
              <span class="capture-kind">{{ t("lesson.course") }}</span>
              <StatusPill tone="indigo">{{ t("lesson.live") }} · {{ formatElapsed(courseElapsed) }}</StatusPill>
            </div>
            <div class="notebook-output-body" v-html="renderMarkdown(courseStreamText)"></div>
          </article>
          <article
            v-for="c in courseContents"
            :key="c.id"
            class="notebook-output"
            style="border-left:3px solid var(--indigo)"
          >
            <div class="notebook-output-head">
              <span class="capture-kind">{{ t("lesson.course") }}</span>
              <span class="head-actions">
                <StatusPill :tone="isOfflineContent(c) ? 'slate' : 'green'" :title="contentModel(c) ?? t('lesson.offline')">{{ isOfflineContent(c) ? t("lesson.offline") : shortModelName(contentModel(c) as string) }}</StatusPill>
                <StatusPill tone="indigo">{{ t("lesson.words", { count: wordCount(c.content) }) }}</StatusPill>
                <button
                  type="button"
                  class="content-delete-btn"
                  :aria-label="t('lesson.deleteContent')"
                  :title="t('lesson.deleteContent')"
                  :disabled="deletingContentId === c.id"
                  @click="deleteContent(c.id)"
                >
                  <Loader2 v-if="deletingContentId === c.id" :size="13" class="spin" aria-hidden="true" />
                  <X v-else :size="14" aria-hidden="true" />
                </button>
              </span>
            </div>
            <div class="notebook-output-body" v-html="renderMarkdown(c.content)"></div>
            <div v-if="c.sources && c.sources.length" class="notebook-output-src">Sources : {{ c.sources.map(s => (s.book_id||s.book||'?') + (s.chapter ? ' · ' + s.chapter : '')).join(', ') }}</div>
            <div v-if="c.confidence!=null" class="notebook-output-src">Confiance : {{ Number(c.confidence).toFixed(2) }}</div>
          </article>
          <div v-if="!courseContents.length" class="tab-empty">
            <p class="empty-copy">{{ t("lesson.noCourse") }}</p>
            <button type="button" class="primary-action" :disabled="generating || isDeleted" @click="generateCourse">
              <Sparkles :size="14" aria-hidden="true" /> {{ t("lesson.generateCourse") }}
            </button>
          </div>
        </div>

        <!-- Tab: generated summary -->
        <div v-show="activeTab === 'summary'" role="tabpanel" class="lecon-contents">
          <article
            v-for="c in summaryContents"
            :key="c.id"
            class="notebook-output"
            style="border-left:3px solid var(--orange)"
          >
            <div class="notebook-output-head">
              <span class="capture-kind">{{ t("lesson.summary") }}</span>
              <span class="head-actions">
                <StatusPill :tone="isOfflineContent(c) ? 'slate' : 'green'" :title="contentModel(c) ?? t('lesson.offline')">{{ isOfflineContent(c) ? t("lesson.offline") : shortModelName(contentModel(c) as string) }}</StatusPill>
                <StatusPill tone="orange">{{ t("lesson.words", { count: wordCount(c.content) }) }}</StatusPill>
                <button
                  type="button"
                  class="content-delete-btn"
                  :aria-label="t('lesson.deleteContent')"
                  :title="t('lesson.deleteContent')"
                  :disabled="deletingContentId === c.id"
                  @click="deleteContent(c.id)"
                >
                  <Loader2 v-if="deletingContentId === c.id" :size="13" class="spin" aria-hidden="true" />
                  <X v-else :size="14" aria-hidden="true" />
                </button>
              </span>
            </div>
            <div class="notebook-output-body" v-html="renderMarkdown(c.content)"></div>
            <div v-if="c.sources && c.sources.length" class="notebook-output-src">Sources : {{ c.sources.map(s => (s.book_id||s.book||'?') + (s.chapter ? ' · ' + s.chapter : '')).join(', ') }}</div>
            <div v-if="c.confidence!=null" class="notebook-output-src">Confiance : {{ Number(c.confidence).toFixed(2) }}</div>
          </article>
          <div v-if="!summaryContents.length" class="tab-empty">
            <p class="empty-copy">{{ t("lesson.noSummary") }}</p>
            <button type="button" class="secondary-action" :disabled="generating || isDeleted" @click="generateSummary">
              <FileText :size="14" aria-hidden="true" /> {{ t("lesson.makeSummary") }}
            </button>
          </div>
        </div>

        <!-- Tab: exercises -->
        <div v-show="activeTab === 'exercises'" role="tabpanel">
        <!-- Exercises area -->
        <div id="exercisesArea" class="exercises-area">
          <!-- Current attempt form -->
          <div v-if="exerciseAttempt" class="exercise-card">
            <div class="exercise-card-head">
              <h3>{{ t("lesson.exercises") }} — {{ exerciseAttempt.questions.length }} questions</h3>
              <StatusPill tone="indigo">tentative</StatusPill>
            </div>
            <div v-for="(q, i) in exerciseAttempt.questions" :key="q.id" class="question-card">
              <div class="q-head">
                <span class="q-index">{{ i+1 }}.</span>
                <span class="q-statement">{{ q.statement }}</span>
                <StatusPill tone="slate">{{ q.type }}</StatusPill>
              </div>
              <div v-if="q.type==='mcq' && q.options" class="q-options">
                <label v-for="opt in q.options" :key="opt" class="q-opt">
                  <input type="radio" :name="`q_${q.id}`" :value="opt" /> <span>{{ opt }}</span>
                </label>
              </div>
              <div v-else-if="q.type==='true_false'" class="q-options">
                <label class="q-opt"><input type="radio" :name="`q_${q.id}`" value="true" /> {{ t("lesson.trueLabel") }}</label>
                <label class="q-opt"><input type="radio" :name="`q_${q.id}`" value="false" /> {{ t("lesson.falseLabel") }}</label>
              </div>
              <div v-else>
                <input type="text" :id="`q_${q.id}`" :placeholder="t('lesson.yourAnswer')" />
              </div>
            </div>
            <div class="exercise-actions">
              <button type="button" class="primary-action" :disabled="generating" @click="submitExercises">
                <CheckCircle2 :size="14" aria-hidden="true" /> {{ t("lesson.submit") }}
              </button>
              <button type="button" class="secondary-action" :disabled="generating" @click="launchExercises">
                <RotateCcw :size="14" aria-hidden="true" /> {{ t("lesson.redo") }}
              </button>
            </div>
            <!-- Feedback after submit -->
            <div v-if="feedback" class="exercise-feedback">
              <div class="feedback-head">
                <strong>Score : {{ feedback.score }} % — {{ feedback.passed ? '✅ ' + t("lesson.scorePassed") : '❌ ' + t("lesson.scoreFailed") }} ({{ feedback.correct }}/{{ feedback.total }})</strong>
              </div>
              <div v-for="(f, idx) in feedback.per" :key="idx" class="feedback-item" :style="f?.correct ? 'border-left-color: var(--green)' : 'border-left-color: var(--orange)'">
                <div class="fb-statement">{{ f?.statement ?? f?.question_id ?? '' }}</div>
                <div class="fb-detail">{{ t("lesson.given") }} : <em>{{ f?.given ?? '' }}</em> — {{ t("lesson.expected") }} : <strong>{{ f?.expected ?? '' }}</strong> {{ f?.correct ? '✅' : '❌' }}</div>
                <div class="fb-explain">{{ f?.explanation ?? '' }}</div>
              </div>
              <div v-if="!feedback.passed" class="feedback-actions">
                <button type="button" class="secondary-action" @click="launchExercises">{{ t("lesson.redo") }}</button>
                <button type="button" class="primary-action" @click="completeManual">{{ t("lesson.markDoneAnyway") }}</button>
              </div>
              <div v-else class="feedback-success">{{ t("lesson.validated") }}</div>
            </div>
          </div>

          <!-- Attempts history summary -->
          <div v-if="attempts.length" class="attempts-history">
            <h4>{{ t("lesson.exercises") }} — {{ attempts.length }} · {{ attempts[attempts.length-1].score!=null ? Math.round((attempts[attempts.length-1].score ?? 0)*100) : 0 }} % ({{ attempts[attempts.length-1].passed ? t("lesson.passed") : t("lesson.redo") }})</h4>
            <div v-if="attempts[attempts.length-1].feedback" class="history-feedback">
              <template v-for="(f, idx) in parseFeedback(attempts[attempts.length-1].feedback)" :key="idx">
                <div class="feedback-item" :style="f.correct ? 'border-left-color: var(--green)' : 'border-left-color: var(--orange)'">
                  <div class="fb-statement">{{ f.statement ?? f.question_id ?? '' }}</div>
                  <div class="fb-detail">{{ t("lesson.given") }} : <em>{{ f.given ?? '' }}</em> — {{ t("lesson.expected") }} : <strong>{{ f.expected ?? '' }}</strong> {{ f.correct ? '✅' : '❌' }}</div>
                  <div class="fb-explain">{{ f.explanation ?? '' }}</div>
                </div>
              </template>
            </div>
            <div class="attempts-actions">
              <button type="button" class="secondary-action" @click="launchExercises">{{ t("lesson.redo") }}</button>
              <button type="button" class="primary-action" @click="completeManual">{{ t("lesson.markDoneAnyway") }}</button>
            </div>
          </div>
          <div v-if="!exerciseAttempt && !attempts.length" class="tab-empty">
            <p class="empty-copy">{{ t("lesson.noExercises") }}</p>
            <button type="button" class="secondary-action" :disabled="generating || isDeleted" @click="launchExercises">
              <GraduationCap :size="14" aria-hidden="true" /> {{ t("lesson.makeExercises") }}
            </button>
          </div>
        </div>
        </div><!-- /tab exercises -->

        <!-- Tab: discussion -->
        <div v-show="activeTab === 'discussion'" role="tabpanel" class="discussion-tab">
        <!-- Messages thread -->
        <div class="lecon-thread" role="log" aria-live="polite" :aria-label="t('lesson.discussion')">
          <div v-if="messages.length || answering" class="thread-list">
            <div v-for="m in messages" :key="m.id" class="msg-row" :class="m.role==='user' ? 'from-student' : 'from-tutor'">
              <div :class="m.role==='user' ? 'bubble-student' : 'bubble-tutor'">{{ m.content }}</div>
            </div>
            <div v-if="answering" class="msg-row from-tutor">
              <div class="bubble-tutor answering"><Loader2 :size="13" class="spin" aria-hidden="true" /> {{ t("lesson.answering") }}</div>
            </div>
          </div>
          <p v-else class="empty-copy">{{ t("lesson.noMessages") }}</p>
        </div>

        <!-- Composer -->
        <form class="lecon-composer" @submit.prevent="sendLessonMessage">
          <textarea
            v-model="composerText"
            rows="1"
            :placeholder="isDeleted ? t('lesson.readonly') : t('lesson.askPlaceholder')"
            :disabled="generating || answering || isDeleted"
            @keydown.enter.exact.prevent="sendLessonMessage"
          ></textarea>
          <button type="submit" class="primary-action send-btn" :disabled="generating || answering || isDeleted || !composerText.trim()">
            <Send :size="14" aria-hidden="true" /> {{ t("lesson.send") }}
          </button>
        </form>
        </div><!-- /tab discussion -->
        <p v-if="error" class="field-error" style="margin-top:8px;">{{ error }}</p>
      </section>
    </template>
  </section>
</template>

<script lang="ts">
export default { name: "LessonView" };
</script>

<style scoped>
.lecon-page { animation: enter .28s var(--ease-out) both; }
.lecon-intro { margin-bottom: 18px; }
.lecon-head-left { display: grid; gap: 10px; }
.back-btn { justify-self: start; min-height: 36px; padding: 7px 12px; font-size: 13px; }
.lecon-title-row { display: inline-flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.lecon-title-row h1 { margin: 0; font: 700 clamp(26px, 3.2vw, 38px)/1.05 "Fraunces", Georgia, serif; letter-spacing: -.03em; }
.lecon-meta { margin: 0; color: var(--muted); font-size: 12px; }
.lecon-meta code { font-family: ui-monospace, monospace; font-size: 11px; background: var(--panel-soft); border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px; }
.loading-state { display: grid; place-items: center; }

.lecon-main { padding: 22px; display: grid; gap: 16px; }
.banner { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 10px; font-size: 13px; line-height: 1.5; }
.banner-gold { border: 1px solid #e8d5a3; background: #fff7e6; color: #6b4a10; }
.banner-dashed { border: 1px dashed #e8d5a3; background: #fffef6; color: #6b4a10; }
.gen-indicator { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--indigo); font-weight: 700; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

.lecon-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.lecon-tabs { display: flex; gap: 6px; flex-wrap: wrap; padding: 4px; border: 1px solid var(--line-soft); border-radius: 12px; background: var(--panel-soft); }
.lecon-tabs button { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 7px 12px; border-radius: 9px; color: var(--muted); font-size: 13px; font-weight: 750; background: transparent; cursor: pointer; transition: background .15s, color .15s; }
.lecon-tabs button:hover { background: rgba(79,70,229,.07); color: var(--indigo-deep); }
.lecon-tabs button.active { background: #fff; color: var(--indigo-deep); box-shadow: var(--shadow-soft); }
.tab-count { display: inline-grid; place-items: center; min-width: 20px; height: 20px; padding: 0 6px; border-radius: 999px; background: var(--indigo-soft); color: var(--indigo-deep); font-size: 11px; font-weight: 800; }
.tab-empty { display: grid; gap: 10px; justify-items: center; padding: 20px 12px; text-align: center; }
.discussion-tab { display: grid; gap: 12px; }
.bubble-tutor.answering { display: inline-flex; align-items: center; gap: 8px; color: var(--indigo-deep); font-weight: 650; }
.streaming-card { animation: stream-glow 2.2s ease-in-out infinite; }
@keyframes stream-glow {
  0%, 100% { box-shadow: var(--shadow-soft); }
  50% { box-shadow: 0 0 0 4px rgba(79, 70, 229, 0.12), var(--shadow-soft); }
}
@media (prefers-reduced-motion: reduce) {
  .streaming-card { animation: none; }
}

.lecon-contents { display: grid; gap: 12px; }
.notebook-output { padding: 14px 16px; border: 1px solid var(--line); border-radius: 12px; background: #fff; box-shadow: var(--shadow-soft); }
.notebook-output-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.head-actions { display: inline-flex; align-items: center; gap: 6px; }
.content-delete-btn {
  display: inline-grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  color: var(--faint);
  background: transparent;
  cursor: pointer;
  transition: color 0.12s, background 0.12s;
}
.content-delete-btn:hover:not(:disabled) {
  color: #e53935;
  background: #fdeaea;
}
.content-delete-btn:disabled {
  cursor: wait;
  opacity: 0.7;
}
.capture-kind { font-size: 11px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: var(--indigo-deep); }
.notebook-output-body { font-size: 13.5px; line-height: 1.6; color: var(--ink-2); }
.notebook-output-body :deep(.md-h) { font-family: "Fraunces", Georgia, serif; font-weight: 700; margin: 8px 0 4px; }
.notebook-output-body :deep(.md-h1) { font-size: 18px; }
.notebook-output-body :deep(.md-h2) { font-size: 15px; }
.notebook-output-body :deep(.md-h3) { font-size: 13.5px; }
.notebook-output-src { margin-top: 8px; color: var(--muted); font-size: 11.5px; }

.exercises-area { display: grid; gap: 14px; }
.exercise-card { border: 1px solid #cbd1ff; background: #f8f8ff; border-radius: 14px; padding: 16px; display: grid; gap: 12px; }
.exercise-card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.exercise-card-head h3 { margin: 0; font: 700 16px/1.2 "Fraunces", Georgia, serif; }
.question-card { padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: #fff; display: grid; gap: 8px; }
.q-head { display: flex; align-items: center; gap: 8px; }
.q-index { font-weight: 800; color: var(--indigo-deep); }
.q-statement { flex: 1; min-width: 0; font-size: 13.5px; font-weight: 650; line-height: 1.5; }
.q-options { display: grid; gap: 6px; }
.q-opt { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
.q-opt input { accent-color: var(--indigo); }
.question-card input[type="text"] { min-height: 40px; }
.exercise-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.exercise-feedback { display: grid; gap: 8px; padding-top: 8px; border-top: 1px dashed #cbd1ff; }
.feedback-head { font-size: 13.5px; }
.feedback-item { padding: 10px 12px; border: 1px solid var(--line); border-left-width: 3px; border-radius: 10px; background: #fff; display: grid; gap: 4px; }
.fb-statement { font-weight: 700; font-size: 13px; }
.fb-detail { font-size: 12.5px; }
.fb-detail em { color: var(--muted); }
.fb-explain { font-size: 12px; color: var(--muted); }
.feedback-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
.feedback-success { color: var(--green); font-size: 13px; font-weight: 700; margin-top: 6px; }

.attempts-history { padding: 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: rgba(255,255,255,.9); display: grid; gap: 10px; }
.attempts-history h4 { margin: 0; font-size: 13px; font-weight: 800; }
.history-feedback { display: grid; gap: 8px; }
.attempts-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.lecon-thread { display: grid; gap: 10px; min-height: 80px; padding: 12px; border: 1px solid var(--line-soft); border-radius: 14px; background: #fdfdff; }
.thread-list { display: grid; gap: 10px; }
.msg-row { display: flex; max-width: 88%; }
.msg-row.from-student { justify-self: end; margin-left: auto; }
.msg-row.from-tutor { justify-self: start; }
.bubble-student { padding: 10px 14px; border-radius: 16px 16px 4px 16px; background: linear-gradient(135deg, #eef0ff, #e3e0f8); border: 1px solid #cfcaf1; font-size: 13.5px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.bubble-tutor { padding: 10px 14px; border-radius: 4px 16px 16px 16px; background: #fff; border: 1px solid var(--line); box-shadow: var(--shadow-soft); font-size: 13.5px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.empty-copy { text-align: center; color: var(--muted); font-size: 13px; padding: 12px; }

.lecon-composer { display: flex; gap: 8px; align-items: flex-end; padding: 12px; border: 1px solid var(--line-soft); border-radius: 14px; background: #fbfcff; }
.lecon-composer textarea { flex: 1; min-height: 44px; max-height: 120px; resize: vertical; }
.send-btn { min-height: 44px; padding: 10px 14px; white-space: nowrap; }
.field-error { color: #b42318; font-size: 12px; font-weight: 600; }

@media (max-width: 640px) {
  .lecon-main { padding: 16px; }
  .lecon-title-row { flex-direction: column; align-items: flex-start; }
  .lecon-actions { flex-direction: column; }
  .lecon-actions button { width: 100%; justify-content: center; }
  .lecon-composer { flex-direction: column; align-items: stretch; }
  .send-btn { width: 100%; justify-content: center; }
}
</style>
