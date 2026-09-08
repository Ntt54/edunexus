<!-- EduNexus UI direction: Atelier de progression — les sources sont des preuves visibles, pas un stockage opaque. -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ChevronRight,
  FileText,
  FolderPlus,
  LoaderCircle,
  Pause,
  Play,
  Search,
  Sparkles,
  Upload,
} from "lucide-vue-next";
import { tutorApi } from "@/services/api";
import type { IngestionJob } from "@/services/api";
import type { LibraryCategory, QueueStatus, SearchResult, SourceBook } from "@/types";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state, toggleQueue } = useLearningStore();
const { t } = usePreferences();

// ── State ──────────────────────────────────────────────────────
const categories = ref<LibraryCategory[]>([]);
const booksBySubject = ref<Map<string, SourceBook[]>>(new Map());
const allBooks = ref<SourceBook[]>([]);
const queue = ref<QueueStatus>({ running: false, pending_count: 0, completed_count: 0 });
const activeJobs = ref<IngestionJob[]>([]);
const etaText = ref("");
const searchQuery = ref("");
const showImport = ref(false);
const showSearchModal = ref(false);
const searchModalQuery = ref("");
const searchResults = ref<SearchResult[]>([]);
const searchLoading = ref(false);
const importSubject = ref("");
const importFile = ref<File | null>(null);
const importLoading = ref(false);
const importProgress = ref("");
const autoClassifying = ref(false);
const dragOver = ref(false);
const openDomains = ref<Set<string>>(new Set());
const openCategories = ref<Set<number>>(new Set());
const bookCategories = ref<Map<string, number[]>>(new Map());
const renamingCategoryId = ref<number | null>(null);
const renamingName = ref("");
const loading = ref(true);

// (pollTimer déclaré dans la section Polling ci-dessous)

// ── Helpers ────────────────────────────────────────────────────
const subject = computed(() => state.data?.subject);

const allBooksFlat = computed(() => {
  const out: SourceBook[] = [];
  for (const list of booksBySubject.value.values()) out.push(...list);
  // Deduplicate by id
  const seen = new Set<string>();
  return out.filter((b) => { if (seen.has(b.id)) return false; seen.add(b.id); return true; });
});

const filteredBooks = computed(() => {
  const q = searchQuery.value.toLowerCase().trim();
  if (!q) return allBooksFlat.value;
  return allBooksFlat.value.filter((b) => b.title.toLowerCase().includes(q));
});

const selectedBooksCount = computed(() => filteredBooks.value.length);

const statusLabel = (status: string): string =>
  ({ indexed: t("status.ready"), indexing: t("status.indexing"), pending: t("status.queued"), error: t("status.review") }[status] ?? status) as string;

const statusTone = (status: string) =>
  status === "indexed" ? "green" : status === "indexing" ? "indigo" : status === "error" ? "orange" : "slate";

const formatChip = (book: SourceBook): string => {
  if (book.format) return book.format.toUpperCase();
  if (book.sourceType) return book.sourceType;
  return "Note";
};

function catsOf(bookId: string): number[] {
  return bookCategories.value.get(bookId) ?? [];
}

// ── Data loading ───────────────────────────────────────────────
async function loadAll() {
  loading.value = true;
  try {
    const [catRes, queueRes] = await Promise.all([
      tutorApi.getCategories().catch(() => ({ categories: [] as LibraryCategory[] })),
      tutorApi.getQueueStatus().catch(() => ({ running: false, pending_count: 0, completed_count: 0 })),
    ]);
    categories.value = catRes.categories ?? [];
    queue.value = queueRes;
    await refreshJobs();

    // Load subjects and books
    try {
      const subjectsRes = await tutorApi.getSubjects();
      for (const sub of subjectsRes.subjects) {
        const booksRes = await tutorApi.getBooks(sub.name).catch(() => ({ books: [] as SourceBook[] }));
        booksBySubject.value.set(sub.id, booksRes.books ?? []);
      }
    } catch { /* no subjects */ }

    // Load book-category memberships
    await hydrateAllMemberships();
  } catch { /* best-effort */ }
  loading.value = false;
}

async function hydrateAllMemberships() {
  const books = allBooksFlat.value;
  await Promise.allSettled(
    books.map(async (b) => {
      if (!bookCategories.value.has(b.id)) {
        try {
          const res = await tutorApi.getBookCategories(b.id);
          bookCategories.value.set(
            b.id,
            (res.categories ?? []).map((c) => c.id),
          );
        } catch {
          bookCategories.value.set(b.id, []);
        }
      }
    }),
  );
}

async function refreshQueue() {
  try {
    queue.value = await tutorApi.getQueueStatus();
  } catch { /* best-effort */ }
}

// ── Progression réelle des jobs (US3) ────────────────────────────
// Multi-jobs : on affiche le job le plus avancé — le worker étant
// séquentiel, c'est lui qui détermine la fin visible du traitement.
const shownJob = computed<IngestionJob | null>(() => {
  let best: IngestionJob | null = null;
  for (const j of activeJobs.value) {
    if (!best || Number(j.progress_percent ?? 0) > Number(best.progress_percent ?? 0)) best = j;
  }
  return best;
});

const shownPercent = computed(() =>
  Math.max(0, Math.min(100, Math.round(Number(shownJob.value?.progress_percent ?? 0)))),
);

// ── Estimateur de temps restant (fenêtre glissante, lissée) ──────
// Vélocité = pente (%/s) entre le premier et le dernier échantillon du
// job affiché (≤ 6 échantillons, ≤ 60 s). ETA = (100 − p) / vélocité.
// Aucune vélocité mesurable (début, phase lente) ⇒ phase_label seul,
// jamais de chiffre inventé. Reset à chaque nouveau job affiché.
interface EtaSample { t: number; p: number; }
let etaJobId: string | null = null;
let etaSamples: EtaSample[] = [];

function resetEta(jobId: string | null) {
  etaJobId = jobId;
  etaSamples = [];
  etaText.value = "";
}

function trackProgress(job: IngestionJob) {
  if (etaJobId !== job.id) resetEta(job.id);
  const p = Math.max(0, Math.min(100, Number(job.progress_percent ?? 0)));
  const now = Date.now();
  etaSamples.push({ t: now, p });
  while (etaSamples.length > 6) etaSamples.shift();
  while (etaSamples.length > 1 && now - etaSamples[0].t > 60000) etaSamples.shift();
  const first = etaSamples[0];
  const last = etaSamples[etaSamples.length - 1];
  const dt = (last.t - first.t) / 1000;
  if (etaSamples.length >= 2 && dt >= 3) {
    const v = (last.p - first.p) / dt; // %/s
    if (v > 0.02 && p < 100) {
      const remain = Math.max(0, (100 - p) / v);
      etaText.value = remain < 60
        ? (t("library.etaSoon") as string)
        : (t("library.etaMinutes", { count: Math.ceil(remain / 60) }) as string);
      return;
    }
  }
  etaText.value = "";
}

async function refreshJobs() {
  try {
    const res = await tutorApi.getIngestionJobs(10);
    activeJobs.value = (res.jobs ?? []).filter((j) => j.status !== "completed" && j.status !== "failed");
  } catch {
    // Erreur transitoire : on garde la dernière liste connue (pas de reset
    // de l'estimateur sur un simple raté réseau).
    const job = shownJob.value;
    if (job) trackProgress(job);
    return;
  }
  const job = shownJob.value;
  if (job) trackProgress(job);
  else resetEta(null);
}

async function refreshBooks() {
  try {
    const subjectsRes = await tutorApi.getSubjects();
    // Reconstruction complète : un domaine supprimé disparaît de la liste.
    booksBySubject.value = new Map();
    for (const sub of subjectsRes.subjects) {
      const booksRes = await tutorApi.getBooks(sub.name).catch(() => ({ books: [] as SourceBook[] }));
      booksBySubject.value.set(sub.id, booksRes.books ?? []);
    }
    await hydrateAllMemberships();
  } catch { /* best-effort */ }
}

// ── Domain operations ──────────────────────────────────────────
async function deleteDomain(domain: TreeNode) {
  const n = domain.books.length;
  const base = t("library.deleteDomainConfirm", { name: domain.name });
  const msg = n > 0 ? `${base}\n\n${n} document(s).` : base;
  if (!confirm(msg)) return;
  try {
    await tutorApi.deleteSubject(domain.id);
    openDomains.value.delete(domain.id);
    importProgress.value = t("library.domainDeleted");
    await refreshBooks();
    setTimeout(() => { importProgress.value = ""; }, 2500);
  } catch { /* best-effort */ }
}

// ── Polling ────────────────────────────────────────────────────
// Boucle de suivi d'indexation : chaque passage rafraîchit la file PUIS
// les livres (statuts + nouveaux livres indexés), et ne s'arrête que
// quand tout est quiescent (file vide ET aucun livre pending/indexing).
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollInFlight = false;

function hasActiveWork(): boolean {
  if (queue.value.running || queue.value.pending_count > 0) return true;
  if (activeJobs.value.length > 0) return true;
  return allBooksFlat.value.some((b) => b.status === "indexing" || b.status === "pending");
}

async function pollTick(): Promise<void> {
  pollTimer = null;
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    await refreshQueue();
    // Livres : fait apparaître les documents dès leur indexation terminée
    // (completed) ou en échec (error), sans refresh manuel.
    await refreshBooks();
    // Jobs : progression réelle + ETA de la bannière.
    await refreshJobs();
  } finally {
    pollInFlight = false;
  }
  if (hasActiveWork()) pollTimer = setTimeout(pollTick, 2000);
}

function startPolling() {
  stopPolling();
  pollTimer = setTimeout(pollTick, 2000);
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

onMounted(() => {
  loadAll().then(() => {
    if (hasActiveWork()) startPolling();
  });
});
onUnmounted(stopPolling);

// Start polling when queue becomes active
watch(
  () => [queue.value.running, queue.value.pending_count],
  ([running, pending]) => {
    if (running || pending) startPolling();
  },
);

// ── Import ─────────────────────────────────────────────────────
function onDragOver(e: DragEvent) {
  e.preventDefault();
  dragOver.value = true;
}
function onDragLeave() {
  dragOver.value = false;
}
function onDrop(e: DragEvent) {
  e.preventDefault();
  dragOver.value = false;
  const files = e.dataTransfer?.files;
  if (files?.length) importFile.value = files[0];
}
function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) importFile.value = input.files[0];
}

async function doImport() {
  const file = importFile.value;
  // Domaine optionnel : vide ⇒ le backend infère depuis le nom du fichier.
  const domain = importSubject.value.trim();
  if (!file) {
    importProgress.value = t("library.importNeedFile");
    return;
  }
  importLoading.value = true;
  importProgress.value = t("library.importing");
  try {
    await tutorApi.importDocument(file, domain, undefined, true);
    importProgress.value = t("library.importDone");
    importFile.value = null;
    await refreshBooks();
    startPolling();
    setTimeout(() => { showImport.value = false; importProgress.value = ""; }, 1500);
  } catch (e) {
    importProgress.value = e instanceof Error ? e.message : "Erreur d'import";
  }
  importLoading.value = false;
}

// ── Auto-classify ──────────────────────────────────────────────
async function doAutoClassify() {
  if (autoClassifying.value) return;
  autoClassifying.value = true;
  try {
    const res = await tutorApi.autoClassify();
    if (res.assignments?.length) {
      // Update local membership cache
      for (const a of res.assignments) {
        const cats = bookCategories.value.get(a.book_id) ?? [];
        // Find category by name
        const cat = categories.value.find((c) => c.name === a.category);
        if (cat && !cats.includes(cat.id)) {
          cats.push(cat.id);
          bookCategories.value.set(a.book_id, cats);
        }
      }
    }
    await Promise.all([tutorApi.getCategories(), refreshBooks()]);
    categories.value = (await tutorApi.getCategories()).categories ?? [];
  } catch { /* best-effort */ }
  autoClassifying.value = false;
}

// ── Category operations ────────────────────────────────────────
function toggleDomain(id: string) {
  if (openDomains.value.has(id)) openDomains.value.delete(id);
  else openDomains.value.add(id);
}

function toggleCategory(id: number) {
  if (openCategories.value.has(id)) openCategories.value.delete(id);
  else openCategories.value.add(id);
}

async function createCategory() {
  const name = prompt(t("library.createCategory") + " :");
  if (!name?.trim()) return;
  try {
    await tutorApi.createCategory(name.trim());
    categories.value = (await tutorApi.getCategories()).categories ?? [];
  } catch { /* best-effort */ }
}

function startRenameCat(cat: CategoryNode) {
  if (cat.id == null) return;
  renamingCategoryId.value = cat.id;
  renamingName.value = cat.name;
  nextTick(() => {
    const input = document.querySelector<HTMLInputElement>(".lib-rename-input");
    input?.focus();
    input?.select();
  });
}

async function commitRename(cat: CategoryNode) {
  if (cat.id == null) { renamingCategoryId.value = null; return; }
  const name = renamingName.value.trim();
  if (!name || name === cat.name) { renamingCategoryId.value = null; return; }
  try {
    await tutorApi.renameCategory(cat.id, name);
    categories.value = (await tutorApi.getCategories()).categories ?? [];
  } catch { /* best-effort */ }
  renamingCategoryId.value = null;
}

async function deleteCategoryById(catId: number) {
  const cat = categories.value.find((c) => c.id === catId);
  if (!cat) return;
  await deleteCategory(cat);
}

async function deleteCategory(cat: LibraryCategory) {
  const n = cat.book_count ?? 0;
  const msg = n > 0
    ? `La catégorie « ${cat.name} » contient ${n} document(s).\n\nLes documents seront CONSERVÉS.\n\nSupprimer ?`
    : `Supprimer la catégorie vide « ${cat.name} » ?`;
  if (!confirm(msg)) return;
  try {
    await tutorApi.deleteCategory(cat.id);
    categories.value = (await tutorApi.getCategories()).categories ?? [];
    await refreshBooks();
  } catch { /* best-effort */ }
}

// ── Book operations ────────────────────────────────────────────
async function deleteBook(book: SourceBook) {
  if (!confirm(`Supprimer « ${book.title} » et ses fragments ?`)) return;
  try {
    await tutorApi.deleteBook(book.id);
    await refreshBooks();
  } catch { /* best-effort */ }
}

async function reindexBook(book: SourceBook) {
  try {
    await tutorApi.reindexBook(book.id);
    await refreshBooks();
    startPolling();
  } catch { /* best-effort */ }
}

// ── Queue operations ───────────────────────────────────────────
async function toggleQueueBtn() {
  try {
    if (queue.value.running) {
      queue.value = await tutorApi.stopQueue();
    } else {
      queue.value = await tutorApi.startQueue();
      startPolling();
    }
  } catch { /* best-effort */ }
}

// ── Semantic search ────────────────────────────────────────────
async function doSemanticSearch() {
  const q = searchModalQuery.value.trim();
  if (!q || !subject.value) return;
  searchLoading.value = true;
  searchResults.value = [];
  try {
    const res = await tutorApi.searchSemantic(subject.value.name, q, 6);
    searchResults.value = res.results ?? [];
  } catch {
    searchResults.value = [];
  }
  searchLoading.value = false;
}

// ── Build tree structure ───────────────────────────────────────
interface TreeNode {
  id: string;
  name: string;
  type: "domain";
  open: boolean;
  books: SourceBook[];
  categories: CategoryNode[];
}

interface CategoryNode {
  id: number | null;
  name: string;
  books: SourceBook[];
}

const tree = computed<TreeNode[]>(() => {
  const result: TreeNode[] = [];
  // Group books by subject
  const subjectBooks = new Map<string, SourceBook[]>();
  for (const [subId, books] of booksBySubject.value.entries()) {
    const filtered = searchQuery.value
      ? books.filter((b) => b.title.toLowerCase().includes(searchQuery.value.toLowerCase()))
      : books;
    subjectBooks.set(subId, filtered);
  }

  for (const [subId, books] of subjectBooks.entries()) {
    // Find subject name from state
    const subName = state.data?.subject?.id === subId ? state.data.subject.name : subId;

    // Group books by category
    const catMap = new Map<number | null, SourceBook[]>();
    for (const b of books) {
      const cats = catsOf(b.id);
      if (cats.length === 0) {
        const arr = catMap.get(null) ?? [];
        arr.push(b);
        catMap.set(null, arr);
      } else {
        for (const catId of cats) {
          const arr = catMap.get(catId) ?? [];
          arr.push(b);
          catMap.set(catId, arr);
        }
      }
    }

    const catNodes: CategoryNode[] = [];
    // Ordered categories
    for (const cat of categories.value) {
      const catBooks = catMap.get(cat.id);
      if (catBooks?.length) {
        catNodes.push({ id: cat.id, name: cat.name, books: catBooks });
      }
    }
    // Uncategorized
    const uncatBooks = catMap.get(null);
    if (uncatBooks?.length) {
      catNodes.push({ id: null, name: t("library.uncategorized"), books: uncatBooks });
    }

    // If no categories have books, but there are books, add them as uncategorized
    if (catNodes.length === 0 && books.length > 0) {
      catNodes.push({ id: null, name: t("library.uncategorized"), books });
    }

    result.push({
      id: subId,
      name: subName,
      type: "domain",
      open: openDomains.value.has(subId),
      books,
      categories: catNodes,
    });
  }

  return result;
});

// ── Template refs ──────────────────────────────────────────────
const fileInputRef = ref<HTMLInputElement | null>(null);
</script>

<template>
  <section class="page library-page">
    <!-- Header -->
    <header class="page-intro library-intro">
      <div>
        <p class="eyebrow">{{ t('library.kicker') }}</p>
        <h1>{{ t('library.title') }}</h1>
        <p>{{ t('library.copy') }}</p>
      </div>
    </header>

    <!-- Toolbar -->
    <section class="content-panel lib-toolbar">
      <div class="lib-head-tools">
        <span class="count-chip">{{ t('library.documents', { count: selectedBooksCount }) }}</span>
        <button type="button" class="primary-action" @click="showImport = !showImport">
          <FolderPlus :size="16" aria-hidden="true" />
          {{ t('library.add') }}
        </button>
        <button
          type="button"
          class="ghost-btn"
          :disabled="autoClassifying"
          @click="doAutoClassify"
        >
          <Sparkles v-if="!autoClassifying" :size="14" aria-hidden="true" />
          <LoaderCircle v-else :size="14" class="spin" aria-hidden="true" />
          {{ autoClassifying ? t('library.classifying') : t('library.autoClassify') }}
        </button>
      </div>

      <div class="lib-search-row">
        <input
          v-model="searchQuery"
          type="search"
          :placeholder="t('library.search')"
          :aria-label="t('library.search')"
        />
        <button type="button" class="ghost-btn" @click="showSearchModal = true">
          <Search :size="14" aria-hidden="true" />
          {{ t('library.semanticSearch') }}
        </button>
      </div>
    </section>

    <!-- Import Panel (collapsible) -->
    <section v-if="showImport" class="content-panel" style="margin-bottom: 13px; padding: 18px;">
      <h3 style="margin: 0 0 10px; font-size: 15px;">{{ t('library.importPanel') }}</h3>
      <label class="field-label" for="imp-subject" style="margin-top: 0;">{{ t('library.importDomain') }}</label>
      <input
        id="imp-subject"
        v-model="importSubject"
        type="text"
        :placeholder="t('library.importDomainPlaceholder')"
      />
      <div style="font-size: 11px; color: var(--faint); margin-top: 2px;">{{ t('library.importDomainHint') }}</div>
      <label class="field-label" for="imp-file">{{ t('library.importFile') }}</label>
      <div
        class="import-drop"
        :class="{ 'drag-over': dragOver }"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
        @click="fileInputRef?.click()"
      >
        <template v-if="importFile">
          <FileText :size="18" aria-hidden="true" style="vertical-align: -3px;" />
          {{ importFile.name }}
        </template>
        <template v-else>
          <Upload :size="20" aria-hidden="true" style="vertical-align: -4px; margin-bottom: 4px;" />
          <div>{{ t('library.dropHere') }}</div>
          <div style="font-size: 11px; color: var(--faint);">{{ t('library.orBrowse') }}</div>
        </template>
        <input
          ref="fileInputRef"
          type="file"
          accept=".txt,.md,.pdf,.epub,.docx,.pptx"
          @change="onFileChange"
        />
      </div>
      <button
        type="button"
        class="primary-action"
        style="margin-top: 8px;"
        :disabled="importLoading || !importFile"
        @click="doImport"
      >
        <LoaderCircle v-if="importLoading" :size="16" class="spin" aria-hidden="true" />
        <Upload v-else :size="16" aria-hidden="true" />
        {{ importLoading ? t('library.importing') : t('library.importBtn') }}
      </button>
      <p v-if="importProgress" class="lib-progress-msg" style="margin: 6px 0 0;">{{ importProgress }}</p>
    </section>

    <!-- Queue Banner -->
    <section class="queue-banner" :class="{ running: queue.running }">
      <div>
        <div class="queue-state">
          <span></span>{{ queue.running ? t('library.queueWorking') : t('library.queueReady') }}
        </div>
        <strong>
          {{ queue.running
            ? (queue.current_title ?? t('library.current'))
            : t('library.waiting', { count: queue.pending_count })
          }}
        </strong>
        <p>{{ t('library.queueReason', { count: queue.completed_count }) }}</p>
        <div v-if="shownJob" class="lib-job-progress">
          <div
            class="lib-progress-track"
            role="progressbar"
            :aria-valuenow="shownPercent"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-label="t('library.queueWorking')"
          >
            <div class="lib-progress-fill" :style="{ width: shownPercent + '%' }" />
          </div>
          <p class="lib-progress-line">{{ t('library.jobProgress', { phase: shownJob.phase_label || shownJob.status, percent: shownPercent }) }}<template v-if="etaText"> · {{ etaText }}</template></p>
        </div>
      </div>
      <button type="button" class="secondary-action" @click="toggleQueueBtn">
        <Pause v-if="queue.running" :size="17" aria-hidden="true" />
        <Play v-else :size="17" aria-hidden="true" />
        {{ queue.running ? t('library.pause') : t('library.start') }}
      </button>
    </section>

    <!-- Document Tree -->
    <section class="content-panel" style="padding: 23px;">
      <div class="panel-heading" style="margin-bottom: 14px;">
        <div>
          <p class="eyebrow">{{ t('library.active') }}</p>
          <h2>{{ t('library.documents', { count: allBooksFlat.length }) }}</h2>
        </div>
        <button type="button" class="ghost-btn" @click="createCategory">
          <FolderPlus :size="14" aria-hidden="true" />
          {{ t('library.createCategory') }}
        </button>
      </div>

      <div v-if="loading" class="lib-empty">
        <LoaderCircle :size="20" class="spin" aria-hidden="true" />
      </div>
      <div v-else-if="tree.length === 0" class="lib-empty">{{ t('library.emptyLibrary') }}</div>
      <div v-else class="lib-tree lib-tree-scroll">
        <!-- Domain nodes -->
        <div v-for="domain in tree" :key="domain.id" class="lib-tnode" :class="{ open: domain.open }">
          <div class="lib-trow">
            <span class="lib-tcaret" @click="toggleDomain(domain.id)">
              <ChevronRight :size="14" aria-hidden="true" />
            </span>
            <strong class="lib-tlabel" @click="toggleDomain(domain.id)">{{ domain.name }}</strong>
            <span class="lib-tcount">{{ domain.books.length }} doc</span>
            <span class="lib-tacts">
              <button type="button" class="lib-tact del" :title="t('library.deleteDomain')" @click.stop="deleteDomain(domain)">🗑</button>
            </span>
          </div>
          <div class="lib-tkids">
            <div v-if="domain.open">
              <!-- Category nodes -->
              <div v-for="cat in domain.categories" :key="cat.id ?? 'uncat'" class="lib-tnode" :class="{ open: openCategories.has(cat.id ?? -1) }">
                <div class="lib-trow">
                  <span class="lib-tcaret" @click="toggleCategory(cat.id ?? -1)">
                    <ChevronRight :size="14" aria-hidden="true" />
                  </span>
                  <template v-if="renamingCategoryId === cat.id">
                    <input
                      v-model="renamingName"
                      class="lib-rename-input"
                      type="text"
                      style="width: 160px; min-height: 26px; font-size: 12px;"
                      @keydown.enter="cat.id != null && commitRename(cat)"
                      @keydown.escape="renamingCategoryId = null"
                      @blur="cat.id != null && commitRename(cat)"
                    />
                  </template>
                  <template v-else>
                    <span class="lib-tlabel" @click="toggleCategory(cat.id ?? -1)">{{ cat.name }}</span>
                  </template>
                  <span class="lib-tcount">{{ cat.books.length }} doc</span>
                    <span v-if="cat.id != null" class="lib-tacts">
                    <button type="button" class="lib-tact" :title="t('library.renameCategory')" @click.stop="startRenameCat(cat)">✎</button>
                    <button type="button" class="lib-tact del" :title="t('library.deleteCategory')" @click.stop="deleteCategoryById(cat.id)">🗑</button>
                  </span>
                </div>
                <div class="lib-tkids">
                  <div v-for="book in cat.books" :key="book.id" class="lib-trow lib-tdoc">
                    <div style="flex: 1; min-width: 0;">
                      <div class="lib-src-title">{{ book.title }}</div>
                      <div class="lib-src-meta">
                        <span class="lib-fmt-chip">{{ formatChip(book) }}</span>
                        <span class="lib-badge" :class="`lib-badge-${book.status}`">{{ statusLabel(book.status) }}</span>
                        <span v-if="book.pages">{{ book.pages }} pages</span>
                        <span v-else-if="book.chunks_total">{{ book.chunks_total }} fragments</span>
                      </div>
                      <!-- Indexing progress bar -->
                      <div v-if="book.status === 'indexing'" style="height: 4px; border-radius: 99px; background: #e7e8f7; overflow: hidden; margin-top: 6px;">
                        <div style="width: 40%; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--orange), #f5ad4e); animation: indeterminate 1.4s ease infinite;" />
                      </div>
                    </div>
                    <span class="lib-src-actions">
                      <button type="button" class="lib-tact" :title="t('library.reindex')" @click.stop="reindexBook(book)">↻</button>
                      <button type="button" class="lib-src-del" :title="t('library.deleteBook')" @click.stop="deleteBook(book)">×</button>
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Semantic Search Modal -->
    <Teleport to="body">
      <div v-if="showSearchModal" class="search-backdrop" @click.self="showSearchModal = false">
        <div class="search-dialog" role="dialog" aria-modal="true">
          <div class="search-head">
            <h3>{{ t('library.searchTitle') }}</h3>
            <button type="button" class="search-close" @click="showSearchModal = false">✕</button>
          </div>
          <div class="search-bar">
            <input
              v-model="searchModalQuery"
              type="text"
              :placeholder="t('library.searchPlaceholder')"
              :aria-label="t('library.semanticSearch')"
              @keydown.enter="doSemanticSearch"
            />
            <button type="button" class="primary-action" style="min-height: 40px; padding: 8px 14px;" @click="doSemanticSearch">
              <Search :size="14" aria-hidden="true" />
              {{ t('library.searchBtn') }}
            </button>
          </div>
          <div class="search-results">
            <div v-if="searchLoading" class="lib-empty">{{ t('library.searching') }}</div>
            <div v-else-if="searchResults.length === 0 && searchModalQuery.trim()" class="lib-empty">{{ t('library.noResults') }}</div>
            <div v-else-if="!searchModalQuery.trim()" class="lib-empty" style="padding-top: 30px;">{{ t('library.searchPlaceholder') }}</div>
            <div v-for="(res, i) in searchResults" :key="i" class="search-result-item">
              <div>
                <span class="search-result-score">[{{ Number(res.score).toFixed(3) }}]</span>
                <span class="search-result-source">
                  {{ res.book_title || res.book_id }}
                  <template v-if="res.chapter"> · chap. {{ res.chapter }}</template>
                  <template v-if="res.page != null"> · p. {{ res.page }}</template>
                </span>
              </div>
              <div class="search-result-text">{{ res.text }}</div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
@keyframes indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.lib-job-progress { margin-top: 10px; }
.lib-progress-track { height: 6px; border-radius: 99px; background: #e7e8f7; overflow: hidden; }
.lib-progress-fill { height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--orange), #f5ad4e); transition: width .4s ease; }
.lib-progress-line { margin: 6px 0 0; color: var(--muted); font-size: 12px; }
</style>
