<!-- EduNexus UI direction: Atelier de progression — éditeur de parcours avec sidebar, création, drag-and-drop et gestion des étapes. -->
<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  FileText,
  GripVertical,
  HelpCircle,
  Lightbulb,
  Loader2,
  Plus,
  Play,
  RotateCcw,
  Trash2,
  X,
} from "lucide-vue-next";
import ProgressRing from "@/components/ProgressRing.vue";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { tutorApi } from "@/services/api";
import type { ActivityType } from "@/types";
import { usePreferences } from "@/stores/preferences";

const { state, hydrate } = useLearningStore();
const { t } = usePreferences();
const router = useRouter();

/* ── Local types ──────────────────────────────────────────────── */
interface PathSummary {
  id: string;
  title: string;
  description: string;
  status: string;
  progress?: number;
}

interface PathStep {
  id: string;
  title: string;
  activityType: ActivityType;
  duration?: number;
  status: "pending" | "completed";
  source: string;
}

interface PathDetail {
  id: string;
  title: string;
  description: string;
  status: string;
  progress: number;
  steps: PathStep[];
}

/* ── State ────────────────────────────────────────────────────── */
const paths = ref<PathSummary[]>([]);
const selectedPath = ref<PathDetail | null>(null);
const loadingPaths = ref(false);
const loadingPath = ref(false);
const error = ref<string | null>(null);

/* Create form */
const showCreateForm = ref(false);
const newTitle = ref("");
const newDescription = ref("");
const creating = ref(false);

/* Add step form */
const showAddStep = ref(false);
const newStepType = ref<ActivityType>("concept");
const newStepTitle = ref("");
const addingStep = ref(false);

/* Generate from books modal */
const showBookModal = ref(false);
const loadingBooks = ref(false);
const availableBooks = ref<Array<{ id: string; title: string; name: string; status: string; format: string; sourceType: string; pages: number | null; chunks_total: number | null }>>([]);
const selectedBookIds = ref<string[]>([]);
const generatingBooks = ref(false);
const bookGoal = ref("");

/* Drag state */
const draggedStepIndex = ref<number | null>(null);

/* Active subject — fall back to API if dashboard hasn't loaded */
const subjectId = ref("");
async function resolveSubjectId() {
  if (state.data?.subject.id) { subjectId.value = state.data.subject.id; return; }
  try {
    const resp = await tutorApi.getSubjects();
    subjectId.value = resp.active_id || resp.subjects[0]?.id || "";
  } catch { subjectId.value = ""; }
}

/* Icon mapping */
const iconFor: Record<ActivityType, typeof FileText> = {
  concept: Lightbulb,
  reading: FileText,
  exercise: Play,
  quiz: HelpCircle,
  flashcard_review: RotateCcw,
};

/* ── Defensive normalisation (API snake_case, no duration) ───────
   L'API renvoie `activity_type` (snake_case), aucun `duration` et des
   statuts `not_started|in_progress|completed`. Sans normalisation, le
   template affichait des clés brutes (`activity.undefined`) et
   « environ undefined min ». Tout inconnu retombe sur `concept`,
   jamais sur une clé brute. */
const KNOWN_ACTIVITIES: ReadonlySet<string> = new Set([
  "concept",
  "reading",
  "exercise",
  "quiz",
  "flashcard_review",
]);
function normalizeActivity(raw: unknown): ActivityType {
  const text = String(raw ?? "").trim();
  const base = text.includes(".") ? text.slice(text.lastIndexOf(".") + 1) : text;
  const low = base.replace(/-/g, "_").toLowerCase();
  return (KNOWN_ACTIVITIES.has(low) ? low : "concept") as ActivityType;
}
function normalizeStatus(raw: unknown): "pending" | "completed" {
  return String(raw ?? "").trim().toLowerCase() === "completed" ? "completed" : "pending";
}
function normalizeDuration(raw: unknown): number | undefined {
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : undefined;
}
function normalizeStep(raw: Record<string, unknown>): PathStep {
  const r = raw as Record<string, unknown>;
  return {
    id: String(r.id ?? ""),
    title: String(r.title ?? r.name ?? ""),
    activityType: normalizeActivity(r.activityType ?? r.activity_type ?? r.type),
    duration: normalizeDuration(r.duration ?? r.estimated_minutes ?? r.minutes),
    status: normalizeStatus(r.status),
    source: String(r.source ?? r.activity_id ?? ""),
  };
}
const iconForStep = (activityType: unknown): typeof FileText =>
  iconFor[normalizeActivity(activityType)] ?? FileText;
const labelFor = (activityType: unknown) => t(`activity.${normalizeActivity(activityType)}`);
const hasDuration = (duration: unknown): duration is number =>
  typeof duration === "number" && Number.isFinite(duration);

/* Computed */
const completedCount = computed(
  () => selectedPath.value?.steps.filter((s) => s.status === "completed").length ?? 0,
);
const totalCount = computed(() => selectedPath.value?.steps.length ?? 0);
// Cible de remplissage : le parcours sélectionné s'il est vide.
const fillTarget = computed(() =>
  selectedPath.value && selectedPath.value.steps.length === 0 ? selectedPath.value : null,
);

/* ── Data loading ─────────────────────────────────────────────── */
async function loadPaths() {
  if (!subjectId.value) return;
  loadingPaths.value = true;
  error.value = null;
  try {
    const data = await tutorApi.getPaths(subjectId.value);
    paths.value = data.paths;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de chargement";
  } finally {
    loadingPaths.value = false;
  }
}

async function selectPath(pathId: string) {
  loadingPath.value = true;
  error.value = null;
  try {
    const data = (await tutorApi.getPath(pathId)) as unknown as Record<string, unknown>;
    const rawSteps = (data.steps ?? []) as unknown as Array<Record<string, unknown>>;
    selectedPath.value = {
      ...(data as unknown as PathDetail),
      steps: rawSteps.map(normalizeStep),
    };
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de chargement";
  } finally {
    loadingPath.value = false;
  }
}

/* ── Path CRUD ────────────────────────────────────────────────── */
async function createPath() {
  if (!newTitle.value.trim() || !subjectId.value) return;
  creating.value = true;
  try {
    await tutorApi.createPath(subjectId.value, newTitle.value.trim(), newDescription.value.trim());
    newTitle.value = "";
    newDescription.value = "";
    showCreateForm.value = false;
    await loadPaths();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de création";
  } finally {
    creating.value = false;
  }
}

async function deletePath(pathId: string) {
  if (!confirm(t("path.deleteConfirm"))) return;
  try {
    await tutorApi.deletePath(pathId);
    if (selectedPath.value?.id === pathId) selectedPath.value = null;
    await loadPaths();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de suppression";
  }
}

async function savePathTitle() {
  if (!selectedPath.value) return;
  try {
    await tutorApi.updatePath(selectedPath.value.id, { title: selectedPath.value.title });
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur d'enregistrement";
  }
}

/* ── Step management ──────────────────────────────────────────── */
async function addStep() {
  if (!selectedPath.value) return;
  addingStep.value = true;
  try {
    const step = (await tutorApi.addPathStep(
      selectedPath.value.id,
      newStepType.value,
      `manual-${Date.now()}`,
      newStepTitle.value.trim(),
    )) as unknown as Record<string, unknown>;
    selectedPath.value.steps.push(normalizeStep(step));
    newStepTitle.value = "";
    showAddStep.value = false;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur d'ajout";
  } finally {
    addingStep.value = false;
  }
}

async function removeStep(stepId: string) {
  if (!selectedPath.value) return;
  try {
    await tutorApi.deletePathStep(stepId);
    selectedPath.value.steps = selectedPath.value.steps.filter((s) => s.id !== stepId);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de suppression";
  }
}

async function markComplete(stepId: string) {
  if (!selectedPath.value) return;
  try {
    await tutorApi.completeStep(stepId);
    const step = selectedPath.value.steps.find((s) => s.id === stepId);
    if (step) step.status = "completed";
    const completed = selectedPath.value.steps.filter((s) => s.status === "completed").length;
    selectedPath.value.progress = Math.round((completed / selectedPath.value.steps.length) * 100);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur";
  }
}

/* ── Drag-and-drop ────────────────────────────────────────────── */
function onDragStart(index: number, event: DragEvent) {
  draggedStepIndex.value = index;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
  }
}

function onDragOver(event: DragEvent) {
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = "move";
  }
}

async function onDrop(targetIndex: number) {
  const sourceIndex = draggedStepIndex.value;
  if (sourceIndex === null || sourceIndex === targetIndex || !selectedPath.value) {
    draggedStepIndex.value = null;
    return;
  }
  const steps = [...selectedPath.value.steps];
  const [moved] = steps.splice(sourceIndex, 1);
  steps.splice(targetIndex, 0, moved);
  selectedPath.value.steps = steps;
  draggedStepIndex.value = null;
  try {
    await tutorApi.reorderPathSteps(
      selectedPath.value.id,
      steps.map((s) => s.id),
    );
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de réordonnancement";
  }
}

/* ── Open personal lesson discussion ──────────────────────────── */
const openingStepId = ref<string | null>(null);
function activeLearnerId(): string {
  try {
    const v =
      localStorage.getItem("edunexus.learner") ||
      localStorage.getItem("edunexus:learner") ||
      "";
    if (v) return v;
  } catch { /* storage unavailable */ }
  return "default";
}
async function openLesson(stepId: string) {
  if (!stepId || openingStepId.value) return;
  openingStepId.value = stepId;
  error.value = null;
  try {
    const created = (await tutorApi.createLessonDiscussion(
      stepId,
      activeLearnerId(),
    )) as unknown as { discussion: { id: string } };
    const discussionId = created?.discussion?.id;
    if (!discussionId) throw new Error(t("path.lessonError"));
    await router.push({ name: "lecon", params: { id: discussionId } });
  } catch (e) {
    error.value = e instanceof Error ? e.message : t("path.lessonError");
  } finally {
    openingStepId.value = null;
  }
}

/* ── Init ─────────────────────────────────────────────────────── */
onMounted(async () => {
  await hydrate();
  await resolveSubjectId();
  await loadPaths();
});

/* ── Generate from books ─────────────────────────────────────── */
// Libellés carte-livre repris de LibraryView (mêmes règles, mêmes i18n) :
// le dialogue utilise les classes globales lib-* aux couleurs explicites,
// lisibles dans les deux thèmes.
type ModalBook = { id: string; title: string; name: string; status: string; format: string; sourceType: string; pages: number | null; chunks_total: number | null };

function modalStatusLabel(status: string): string {
  return ({ indexed: t("status.ready"), indexing: t("status.indexing"), pending: t("status.queued"), error: t("status.review") }[status] ?? status) as string;
}

function modalFormatChip(book: ModalBook): string {
  if (book.format) return book.format.toUpperCase();
  if (book.sourceType) return book.sourceType;
  return "Note";
}

async function openBookModal() {
  showBookModal.value = true;
  loadingBooks.value = true;
  selectedBookIds.value = [];
  bookGoal.value = "";
  try {
    const data = await tutorApi.getBooks();
    // Normalisation défensive : forme inattendue (vieux bundle, payload
    // partiel) ⇒ champs garantis, entrées sans id écartées, titre manquant
    // signalé en console (diagnostic futur, pas d'alert). Les champs
    // d'affichage (format, pages, fragments) sont conservés pour la carte.
    const norm: Array<{ id: string; title: string; name: string; status: string; format: string; sourceType: string; pages: number | null; chunks_total: number | null }> = [];
    for (const raw of data.books as unknown as Array<Record<string, unknown>>) {
      const id = String(raw.id ?? raw.book_id ?? "");
      if (!id) continue;
      const title = String(raw.title ?? "");
      const name = String(raw.name ?? "");
      if (!title && !name) console.warn("[PathView] livre sans titre (payload brut) :", raw);
      const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
      norm.push({
        id,
        title,
        name,
        status: String(raw.status ?? ""),
        format: String(raw.format ?? ""),
        sourceType: String(raw.sourceType ?? ""),
        pages: numOrNull(raw.pages),
        chunks_total: numOrNull(raw.chunks_total),
      });
    }
    availableBooks.value = norm;
  } catch {
    availableBooks.value = [];
  } finally {
    loadingBooks.value = false;
  }
}

async function generateFromBooks() {
  if (!subjectId.value || selectedBookIds.value.length === 0) return;
  generatingBooks.value = true;
  error.value = null;
  // Remplissage : le parcours sélectionné ne reçoit les étapes que s'il
  // est vide (titre gardé) ; sinon un nouveau parcours est créé.
  const target = selectedPath.value && selectedPath.value.steps.length === 0
    ? selectedPath.value.id
    : undefined;
  const call = (pathId?: string) =>
    tutorApi.generateFromBooks(subjectId.value, selectedBookIds.value, bookGoal.value || undefined, pathId);
  try {
    let result: { id: string; title: string };
    try {
      result = await call(target) as { id: string; title: string };
    } catch (e) {
      // 404 sur un parcours supprimé entre-temps ⇒ repli création classique.
      if (target && (e as { status?: number }).status === 404) {
        result = await call(undefined) as { id: string; title: string };
      } else {
        throw e;
      }
    }
    showBookModal.value = false;
    await loadPaths();
    if (result?.id) await selectPath(result.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Erreur de génération";
  } finally {
    generatingBooks.value = false;
  }
}
</script>

<template>
  <section class="page path-editor">
    <!-- Page header -->
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t("path.kicker") }}</p>
        <h1>{{ t("nav.path") }}</h1>
      </div>
      <ProgressRing v-if="selectedPath" :value="selectedPath.progress" :size="104" />
    </header>

    <!-- Main layout: sidebar + content -->
    <div class="path-layout">
      <!-- Sidebar -->
      <aside class="path-summary content-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ t("path.steps") }}</p>
            <h2>{{ t("path.createTitle") }}</h2>
          </div>
          <button class="path-create-toggle" @click="showCreateForm = !showCreateForm">
            <Plus :size="18" aria-hidden="true" />
          </button>
        </div>

        <!-- Create form -->
        <div v-if="showCreateForm" class="path-create-form">
          <label class="field-label">{{ t("path.stepTitle") }}</label>
          <input
            v-model="newTitle"
            :placeholder="t('path.titlePlaceholder')"
            @keyup.enter="createPath"
          />
          <label class="field-label">{{ t("path.descriptionPlaceholder") }}</label>
          <input
            v-model="newDescription"
            :placeholder="t('path.descriptionPlaceholder')"
          />
          <button
            class="primary-action"
            :disabled="creating || !newTitle.trim()"
            @click="createPath"
          >
            {{ creating ? t("path.creating") : t("path.createBtn") }}
          </button>
        </div>

        <!-- Path list -->
        <nav class="path-list">
          <button
            v-for="p in paths"
            :key="p.id"
            class="path-item"
            :class="{ active: selectedPath?.id === p.id }"
            @click="selectPath(p.id)"
          >
            <div class="path-item-info">
              <span class="path-item-title">{{ p.title }}</span>
              <StatusPill
                :tone="
                  p.status === 'active'
                    ? 'green'
                    : p.status === 'completed'
                      ? 'indigo'
                      : 'slate'
                "
              >
                {{ p.status }}
              </StatusPill>
            </div>
          </button>
          <p v-if="!loadingPaths && paths.length === 0" class="lib-empty">
            {{ t("path.selectOrCreate") }}
          </p>
          <p v-if="loadingPaths" class="lib-empty">{{ t("app.loadingWorkshop") }}</p>
        </nav>
      </aside>

      <!-- Main content area -->
      <section class="path-editor-main content-panel">
        <!-- Loading state -->
        <div v-if="loadingPath" class="loading-state">
          <p>{{ t("app.loadingWorkshop") }}</p>
        </div>

        <!-- Error state -->
        <div v-else-if="error" class="path-editor-empty">
          <p>{{ error }}</p>
          <button class="text-button" @click="error = null">
            <X :size="14" aria-hidden="true" />
          </button>
        </div>

        <!-- Empty state: no path selected -->
        <div v-else-if="!selectedPath" class="path-editor-empty">
          <Circle :size="48" aria-hidden="true" />
          <p>{{ t("path.selectOrCreate") }}</p>
        </div>

        <!-- Selected path -->
        <div v-else class="path-editor-content">
          <!-- Path header -->
          <div class="path-editor-header">
            <div class="path-title-row">
              <input
                v-model="selectedPath.title"
                class="path-title-input"
                @blur="savePathTitle"
                @keyup.enter="($event.target as HTMLInputElement).blur()"
              />
              <button
                class="path-delete-btn"
                :aria-label="t('path.deleteConfirm')"
                @click="deletePath(selectedPath.id)"
              >
                <Trash2 :size="14" aria-hidden="true" />
              </button>
            </div>
            <p v-if="selectedPath.description" class="path-description">
              {{ selectedPath.description }}
            </p>
            <div class="path-progress-row">
              <span class="path-progress-text">
                {{ t("path.completedSteps", { count: completedCount }) }} /
                {{ totalCount }}
              </span>
              <ProgressRing :value="selectedPath.progress" :size="64" />
            </div>
          </div>

          <!-- Steps -->
          <div class="path-steps">
            <p class="path-drag-hint">{{ t("path.dragHint") }}</p>

            <div
              v-for="(step, index) in selectedPath.steps"
              :key="step.id"
              class="step-row"
              draggable="true"
              @dragstart="onDragStart(index, $event)"
              @dragover="onDragOver"
              @drop="onDrop(index)"
            >
              <div class="step-drag-handle">
                <GripVertical :size="16" aria-hidden="true" />
              </div>

              <div class="step-timeline-node">
                <Check v-if="step.status === 'completed'" :size="14" aria-hidden="true" />
                <Circle v-else :size="14" aria-hidden="true" />
              </div>

              <button
                type="button"
                class="step-icon-btn"
                :aria-label="t('path.openLesson')"
                :title="t('path.openLesson')"
                :disabled="openingStepId === step.id"
                @click="openLesson(step.id)"
              >
                <component :is="iconForStep(step.activityType)" :size="16" aria-hidden="true" />
              </button>

              <div class="step-info">
                <button
                  type="button"
                  class="step-title-btn"
                  :title="`${step.title} — ${t('path.openLesson')}`"
                  :disabled="openingStepId === step.id"
                  @click="openLesson(step.id)"
                >
                  <span class="step-title">{{ step.title }}</span>
                  <Loader2 v-if="openingStepId === step.id" :size="13" class="spin" aria-hidden="true" />
                </button>
                <div class="step-sub">
                  <span class="step-type-label">{{ labelFor(step.activityType) }}</span>
                  <span v-if="hasDuration(step.duration)" class="step-meta">
                    <Clock3 :size="13" aria-hidden="true" />
                    <span>{{ t("dashboard.approx", { minutes: step.duration }) }}</span>
                  </span>
                  <StatusPill
                    :tone="step.status === 'completed' ? 'green' : 'orange'"
                  >
                    {{
                      step.status === "completed"
                        ? t("status.completed")
                        : t("status.toDo")
                    }}
                  </StatusPill>
                  <button
                    v-if="step.status !== 'completed'"
                    class="text-button step-complete-btn"
                    @click="markComplete(step.id)"
                  >
                    <CheckCircle2 :size="14" aria-hidden="true" />
                    {{ t("path.markComplete") }}
                  </button>
                  <button
                    class="step-delete-btn"
                    :aria-label="t('path.deleteStep')"
                    @click="removeStep(step.id)"
                  >
                    <X :size="14" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>

            <p v-if="selectedPath.steps.length === 0" class="lib-empty">
              {{ t("path.noSteps") }}
            </p>
          </div>

          <!-- Actions bar -->
          <div class="path-actions">
            <button class="secondary-action" @click="showAddStep = !showAddStep">
              <Plus :size="16" aria-hidden="true" />
              {{ t("path.addStep") }}
            </button>
            <button class="secondary-action" @click="openBookModal">
              <FileText :size="16" aria-hidden="true" />
              {{ t("path.generateFromBooks") }}
            </button>
          </div>

          <!-- Generate from books modal -->
          <div v-if="showBookModal" class="modal-overlay" @click.self="showBookModal = false">
            <div class="modal-panel content-panel">
              <div class="panel-heading">
                <div>
                  <p class="eyebrow">{{ t("path.generateFromBooks") }}</p>
                  <h2>{{ t("path.selectBooks") }}</h2>
                </div>
                <button class="text-button" @click="showBookModal = false"><X :size="16" /></button>
              </div>
              <div v-if="loadingBooks" class="loading-state"><p>{{ t("app.loadingWorkshop") }}</p></div>
              <div v-else style="display: grid; gap: 6px; max-height: 320px; overflow-y: auto;">
                <label v-for="book in availableBooks" :key="book.id" class="lib-trow lib-tdoc" style="cursor: pointer;">
                  <input type="checkbox" class="lib-tcheck" :value="book.id" v-model="selectedBookIds" />
                  <div style="flex: 1; min-width: 0;">
                    <div class="lib-src-title" :title="book.id">{{ book.title || book.name || 'Sans titre' }}</div>
                    <div class="lib-src-meta">
                      <span class="lib-fmt-chip">{{ modalFormatChip(book) }}</span>
                      <span class="lib-badge" :class="'lib-badge-' + (book.status || 'pending')">{{ book.status ? modalStatusLabel(book.status) : '…' }}</span>
                      <span v-if="book.pages">{{ book.pages }} pages</span>
                      <span v-else-if="book.chunks_total">{{ book.chunks_total }} fragments</span>
                    </div>
                  </div>
                </label>
                <p v-if="availableBooks.length === 0" class="lib-empty">{{ t("path.noBooks") }}</p>
              </div>
              <div style="margin-top: 12px;">
                <label class="field-label" for="book-goal">{{ t("path.bookGoalLabel") }}</label>
                <textarea
                  id="book-goal"
                  v-model="bookGoal"
                  rows="2"
                  maxlength="500"
                  :placeholder="t('path.bookGoalPlaceholder')"
                  style="width: 100%; resize: vertical;"
                ></textarea>
                <p style="margin: 4px 0 0; color: var(--muted); font-size: 12px;">{{ t("path.bookGoalHint") }}</p>
              </div>
              <div class="modal-actions">
                <button
                  class="primary-action"
                  :disabled="generatingBooks || selectedBookIds.length === 0"
                  @click="generateFromBooks"
                >
                  <Loader2 v-if="generatingBooks" :size="16" class="spin" aria-hidden="true" />
                  {{ generatingBooks ? t("path.generating") : t("path.generateFromBooks") }}
                </button>
              </div>
              <p style="margin: 8px 0 0; color: var(--muted); font-size: 12px;">{{ fillTarget ? t("path.fillEmpty", { name: fillTarget.title }) : t("path.createNew") }}</p>
            </div>
          </div>

          <!-- Add step form -->
          <div v-if="showAddStep" class="add-step-form">
            <label class="field-label">{{ t("path.stepType") }}</label>
            <select v-model="newStepType">
              <option value="concept">{{ t("activity.concept") }}</option>
              <option value="reading">{{ t("activity.reading") }}</option>
              <option value="exercise">{{ t("activity.exercise") }}</option>
              <option value="quiz">{{ t("activity.quiz") }}</option>
              <option value="flashcard_review">
                {{ t("activity.flashcard_review") }}
              </option>
            </select>
            <label class="field-label">{{ t("path.stepTitle") }}</label>
            <input
              v-model="newStepTitle"
              :placeholder="t('path.stepTitlePlaceholder')"
              @keyup.enter="addStep"
            />
            <div class="add-step-actions">
              <button
                class="primary-action"
                :disabled="addingStep || !newStepTitle.trim()"
                @click="addStep"
              >
                {{ addingStep ? "…" : t("path.addBtn") }}
              </button>
              <button class="text-button" @click="showAddStep = false">
                <X :size="14" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
/* ── Path editor layout ──────────────────────────────────────── */
.path-editor-main {
  min-height: 420px;
}
.path-editor-empty {
  display: grid;
  place-items: center;
  gap: 12px;
  padding: 48px 24px;
  color: var(--muted);
  text-align: center;
}
.path-editor-content {
  display: grid;
  gap: 20px;
  padding: 28px;
}

/* ── Sidebar ─────────────────────────────────────────────────── */
.path-create-toggle {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  color: var(--indigo-deep);
  background: var(--indigo-soft);
  cursor: pointer;
  transition: background 0.15s var(--ease-out);
}
.path-create-toggle:hover {
  background: #d8d6ff;
}
.path-create-form {
  display: grid;
  gap: 8px;
  padding: 14px;
  margin-bottom: 12px;
  border: 1px dashed var(--line);
  border-radius: 14px;
  background: var(--panel-soft);
}
.path-create-form .field-label {
  margin: 0;
}
.path-list {
  display: grid;
  gap: 4px;
}
.path-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s var(--ease-out), border-color 0.15s var(--ease-out),
    transform 0.12s var(--ease-out);
}
.path-item:hover {
  border-color: #dde1fc;
  background: #f4f5ff;
  transform: translateX(2px);
}
.path-item.active {
  border-color: #cbd1ff;
  background: var(--indigo-soft);
  box-shadow: inset 3px 0 0 var(--indigo);
}
.path-item-info {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.path-item-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--ink);
  font-size: 13px;
  font-weight: 700;
  line-height: 1.35;
}

/* ── Path header ─────────────────────────────────────────────── */
.path-editor-header {
  display: grid;
  gap: 10px;
}
.path-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.path-title-input {
  flex: 1;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--ink);
  font: 700 24px/1.15 "Fraunces", Georgia, serif;
  letter-spacing: -0.025em;
  background: transparent;
  transition: border-color 0.15s var(--ease-out), background 0.15s var(--ease-out);
}
.path-title-input:hover {
  border-color: var(--line);
  background: rgba(255, 255, 255, 0.6);
}
.path-title-input:focus {
  border-color: var(--indigo);
  background: #fff;
  outline: 0;
  box-shadow: 0 0 0 4px rgba(79, 70, 229, 0.1);
}
.path-delete-btn {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.path-delete-btn:hover {
  color: #e53935;
  background: #fdeaea;
}
.path-description {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.5;
}
.path-progress-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-top: 6px;
}
.path-progress-text {
  color: var(--ink-2);
  font-size: 13px;
  font-weight: 650;
}

/* ── Steps list ──────────────────────────────────────────────── */
.path-steps {
  display: grid;
  gap: 6px;
}
.path-drag-hint {
  margin: 0 0 4px;
  color: var(--faint);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.step-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 54px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 14px;
  background: #fff;
  cursor: grab;
  transition: border-color 0.15s var(--ease-out), background 0.15s var(--ease-out),
    box-shadow 0.15s var(--ease-out), transform 0.12s var(--ease-out);
}
.step-row:hover {
  border-color: #d0d5f0;
  background: #fbfcff;
}
.step-row:active {
  cursor: grabbing;
  transform: scale(1.01);
  box-shadow: var(--shadow-soft);
}
.step-drag-handle {
  flex: 0 0 auto;
  color: var(--faint);
  cursor: grab;
}
.step-timeline-node {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  border: 1px solid var(--line);
  border-radius: 50%;
  color: #9aa3bc;
  background: #fff;
}
.step-row:hover .step-timeline-node {
  border-color: #c8cdf1;
  color: var(--indigo);
}
.step-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border-radius: 8px;
  color: var(--indigo-deep);
  background: var(--indigo-soft);
}
.step-icon-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border-radius: 8px;
  color: var(--indigo-deep);
  background: var(--indigo-soft);
  cursor: pointer;
  transition: background 0.12s, transform 0.12s;
}
.step-icon-btn:hover:not(:disabled) {
  background: #d8d6ff;
  transform: scale(1.05);
}
.step-title-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.step-title-btn:hover:not(:disabled) .step-title {
  color: var(--indigo-deep);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.step-title-btn:disabled {
  cursor: wait;
}
.spin {
  flex: 0 0 auto;
  animation: path-spin 1s linear infinite;
}
@keyframes path-spin {
  from { transform: rotate(0); }
  to { transform: rotate(360deg); }
}
.step-info {
  display: grid;
  gap: 4px;
  min-width: 220px;
  flex: 1;
}
.step-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  margin: 0;
  padding-top: 2px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 700;
  line-height: 1.4;
  white-space: normal;
}
.step-sub {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
}
.step-type-label {
  color: var(--muted);
  font-size: 11px;
  font-weight: 650;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.step-meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.step-complete-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: 0 0 auto;
}
.step-delete-btn {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  color: var(--faint);
  background: transparent;
  cursor: pointer;
  transition: color 0.12s, background 0.12s;
}
.step-delete-btn:hover {
  color: #e53935;
  background: #fdeaea;
}

/* ── Actions & add-step form ─────────────────────────────────── */
.path-actions {
  display: flex;
  gap: 10px;
  padding-top: 4px;
}
.add-step-form {
  display: grid;
  gap: 8px;
  padding: 18px;
  border: 1px dashed var(--line);
  border-radius: 14px;
  background: var(--panel-soft);
}
.add-step-form .field-label {
  margin: 0;
}
.add-step-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* ── Responsive ──────────────────────────────────────────────── */
@media (max-width: 800px) {
  .step-row {
    flex-wrap: wrap;
    gap: 6px;
  }
  .step-drag-handle {
    display: none;
  }
  .step-meta {
    order: 5;
  }
}

/* ── Modal (books selection) ─────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: grid;
  place-items: center;
  background: rgba(15, 15, 25, 0.45);
  backdrop-filter: blur(4px);
}
.modal-panel {
  width: min(480px, 92vw);
  max-height: 80vh;
  overflow-y: auto;
  padding: 28px;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.18);
}
.modal-panel .panel-heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
</style>
