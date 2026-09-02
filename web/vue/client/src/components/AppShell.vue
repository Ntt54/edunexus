<!-- EduNexus UI direction: Atelier de progression — header complet avec badge moteur, sélecteurs modèles, espaces, apprenants et statut. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView } from "vue-router";
import {
  BarChart3, BookOpen, Bot, BrainCircuit, Camera, CheckSquare, Compass,
  FolderOpen, GraduationCap, House, LibraryBig, LineChart, NotebookPen,
  Plus, Settings2, Sparkles, Star, Users,
} from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import NexusFlow from "@/components/NexusFlow.vue";
import PreferenceControls from "@/components/PreferenceControls.vue";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";
import type { EngineInfo, ModelInfo, ModelSources, SubjectInfo, LearnerProfile } from "@/services/api";

const { state, dismissNotice } = useLearningStore();
const brandMark = "/manus-storage/edunexus-nexus-mark_e91945cc.png";
const markUnavailable = ref(false);
const { t } = usePreferences();

// ── Engine badge ──────────────────────────────────────────────────
const engineLabel = ref("détection…");
const engineOcr = ref(false);
const engineReady = ref(false);

// ── Models ────────────────────────────────────────────────────────
const embeddingModels = ref<string[]>([]);
const llmModels = ref<string[]>([]);
const currentEmbedding = ref("");
const currentLLM = ref("");
const modelSources = ref<ModelSources>({ ollama: [], cloud: [] });
const searchEmbedding = ref("");
const searchLLM = ref("");
const favModels = ref<string[]>([]);

// ── Subjects ──────────────────────────────────────────────────────
const subjects = ref<SubjectInfo[]>([]);
const activeSubjectId = ref("");

// ── Learners ──────────────────────────────────────────────────────
const learners = ref<LearnerProfile[]>([]);
const activeLearnerId = ref("");

// ── Status ────────────────────────────────────────────────────────
const statusText = ref("prêt");

function setStatus(text: string) {
  statusText.value = text;
}

// ── Favorites persistence ─────────────────────────────────────────
function loadFavModels(): string[] {
  try {
    const arr = JSON.parse(localStorage.getItem("edunexus.favModels") || "[]");
    return Array.isArray(arr) ? arr.filter((x: unknown) => typeof x === "string") : [];
  } catch { return []; }
}

function saveFavModels(favs: string[]) {
  try { localStorage.setItem("edunexus.favModels", JSON.stringify(favs)); } catch { /* not persistent */ }
}

function isFavModel(name: string): boolean {
  return favModels.value.includes(name);
}

function toggleFavModel(name: string) {
  if (!name) return;
  const i = favModels.value.indexOf(name);
  if (i >= 0) favModels.value.splice(i, 1);
  else favModels.value.push(name);
  saveFavModels(favModels.value);
}

// ── Model helpers ─────────────────────────────────────────────────
function providerGroup(name: string): string {
  const idx = name.indexOf("/");
  if (idx > 0) return name.slice(0, idx);
  return "Autres";
}

function buildModelGroups(
  list: string[],
  current: string,
  sources: ModelSources,
  filter: string,
): Map<string, string[]> {
  const names = Array.from(new Set([...(list || []), current].filter(Boolean)));
  if (!names.length) return new Map();

  const favSet = new Set(favModels.value);
  const q = filter.trim().toLowerCase();
  const groups = new Map<string, string[]>();
  const ollamaSet = new Set(sources.ollama || []);
  const cloudSet = new Set(sources.cloud || []);

  // Favoris group first
  const favItems = names.filter(n => favSet.has(n) && (!q || n.toLowerCase().includes(q)));
  if (favItems.length) groups.set("★ Favoris", favItems);

  // Remaining by provider group
  const remainder = names.filter(n => !favSet.has(n));
  for (const n of remainder) {
    if (q && !n.toLowerCase().includes(q)) continue;
    let g: string;
    if (ollamaSet.has(n)) g = "ollama";
    else if (cloudSet.has(n)) g = providerGroup(n);
    else g = providerGroup(n);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(n);
  }

  // Sort: ollama first, then Autres, then alphabetical
  const sorted = new Map<string, string[]>();
  const rank = (g: string) => (g === "ollama" ? 0 : g === "Autres" ? 1 : g === "★ Favoris" ? -1 : 2);
  const entries = Array.from(groups.entries()).sort((a, b) => {
    const ra = rank(a[0]), rb = rank(b[0]);
    if (ra !== rb) return ra - rb;
    return a[0].localeCompare(b[0]);
  });
  for (const [g, items] of entries) sorted.set(g, items);
  return sorted;
}

// ── Sidebar navigation ────────────────────────────────────────────
const subjectLabel = computed(() => {
  const sub = subjects.value.find(s => s.id === activeSubjectId.value);
  return sub?.name ?? t("app.loadingSubject");
});

const groups = [
  { label: "Commencer", items: [
    { labelKey: "nav.home", to: "/", icon: House },
    { labelKey: "nav.path", to: "/parcours", icon: Compass },
    { labelKey: "nav.revise", to: "/reviser", icon: BrainCircuit },
  ] },
  { label: "S'entraîner", items: [
    { labelKey: "nav.practice", to: "/exercices", icon: CheckSquare },
    { labelKey: "nav.quiz", to: "/quiz", icon: GraduationCap },
    { labelKey: "nav.progress", to: "/progression", icon: BarChart3 },
  ] },
  { label: "Explorer", items: [
    { labelKey: "nav.tutor", to: "/tuteur", icon: Bot },
    { labelKey: "nav.sources", to: "/sources", icon: LibraryBig },
    { labelKey: "nav.graph", to: "/graphe", icon: LineChart },
    { labelKey: "nav.subjectDash", to: "/tableau", icon: BarChart3 },
    { labelKey: "nav.notebook", to: "/carnet", icon: NotebookPen },
    { labelKey: "nav.capture", to: "/capture", icon: Camera },
    { labelKey: "nav.profile", to: "/profil", icon: Compass },
    { labelKey: "nav.learners", to: "/apprenants", icon: Users },
  ] },
  { label: "Espace", items: [
    { labelKey: "nav.settings", to: "/reglages", icon: Settings2 },
  ] },
];

// ── API calls ─────────────────────────────────────────────────────
async function loadEngine() {
  try {
    const eng = await tutorApi.getEngine();
    engineLabel.value = eng.embedding === "gguf-local" ? "GGUF local" : "Ollama";
    engineOcr.value = eng.ocr;
    engineReady.value = true;
  } catch {
    engineLabel.value = "moteur ?";
    engineReady.value = true;
  }
}

async function loadModels() {
  try {
    setStatus("chargement des modèles…");
    const data = await tutorApi.getModels();
    modelSources.value = data.sources || { ollama: [], cloud: [] };
    embeddingModels.value = data.embedding || [];
    llmModels.value = data.llm || [];
    currentEmbedding.value = data.current?.embedding || "";
    currentLLM.value = data.current?.llm || "";
    setStatus("prêt");
  } catch {
    embeddingModels.value = [];
    llmModels.value = [];
    currentEmbedding.value = "";
    currentLLM.value = "";
  }
}

async function onEmbeddingChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value;
  if (!value || value === currentEmbedding.value) return;
  try {
    setStatus("changement de modèle…");
    const data = await tutorApi.setModel({ embedding: value });
    modelSources.value = data.sources || { ollama: [], cloud: [] };
    embeddingModels.value = data.embedding || [];
    llmModels.value = data.llm || [];
    currentEmbedding.value = data.current?.embedding || "";
    currentLLM.value = data.current?.llm || "";
    setStatus("prêt");
  } catch {
    loadModels();
  }
}

async function onLLMChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value;
  if (!value || value === currentLLM.value) return;
  try {
    setStatus("changement de modèle…");
    const data = await tutorApi.setModel({ llm: value });
    modelSources.value = data.sources || { ollama: [], cloud: [] };
    embeddingModels.value = data.embedding || [];
    llmModels.value = data.llm || [];
    currentEmbedding.value = data.current?.embedding || "";
    currentLLM.value = data.current?.llm || "";
    setStatus("prêt");
  } catch {
    loadModels();
  }
}

async function loadSubjects() {
  try {
    const data = await tutorApi.getSubjects();
    subjects.value = data.subjects || [];
    const remembered = localStorage.getItem("edunexus.space");
    if (subjects.value.some(s => s.id === remembered)) {
      activeSubjectId.value = remembered!;
    } else if (data.active_id && subjects.value.some(s => s.id === data.active_id)) {
      activeSubjectId.value = data.active_id;
    } else if (subjects.value.length) {
      activeSubjectId.value = subjects.value[0].id;
    }
    if (activeSubjectId.value) localStorage.setItem("edunexus.space", activeSubjectId.value);
  } catch {
    subjects.value = [];
  }
}

function onSubjectChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value;
  activeSubjectId.value = value;
  localStorage.setItem("edunexus.space", value);
}

async function loadLearners() {
  try {
    const data = await tutorApi.getLearners();
    learners.value = data.learners || [];
    const remembered = localStorage.getItem("edunexus.learner");
    if (learners.value.some(l => l.id === remembered)) {
      activeLearnerId.value = remembered!;
    } else if (learners.value.length) {
      activeLearnerId.value = learners.value[0].id;
    }
    if (activeLearnerId.value) localStorage.setItem("edunexus.learner", activeLearnerId.value);
  } catch {
    learners.value = [];
  }
}

async function onLearnerChange(event: Event) {
  const lid = (event.target as HTMLSelectElement).value;
  if (!lid) return;
  activeLearnerId.value = lid;
  localStorage.setItem("edunexus.learner", lid);
  try {
    await tutorApi.activateLearner(lid);
    const name = learners.value.find(l => l.id === lid)?.name || "";
    setStatus("Apprenant actif : " + name);
  } catch (e) {
    setStatus("Erreur : " + (e instanceof Error ? e.message : String(e)));
  }
}

async function addLearner() {
  const name = prompt("Nom du nouvel apprenant :");
  if (!name || !name.trim()) return;
  try {
    await tutorApi.createLearner(name.trim());
    await loadLearners();
    setStatus("Apprenant créé.");
  } catch (e) {
    setStatus("Erreur : " + (e instanceof Error ? e.message : String(e)));
  }
}

// ── Initialization ────────────────────────────────────────────────
onMounted(() => {
  favModels.value = loadFavModels();
  loadEngine();
  loadModels();
  loadSubjects();
  loadLearners();
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" aria-label="Navigation principale">
      <RouterLink class="brand" to="/" aria-label="EduNexus">
        <img v-if="!markUnavailable" :src="brandMark" alt="" aria-hidden="true" @error="markUnavailable = true" />
        <span v-else class="brand-mark-fallback" aria-hidden="true"><i></i><i></i><i></i></span>
        <span>Edu<span>Nexus</span></span>
      </RouterLink>

      <div class="matter-switcher" aria-label="Matière active">
        <span class="eyebrow">{{ t('app.workshop') }}</span>
        <strong>{{ subjectLabel }}</strong>
        <small>{{ t('app.localData') }}</small>
      </div>

      <nav class="main-nav">
        <section v-for="group in groups" :key="group.label" class="nav-group" :aria-label="t(`nav.${group.label === 'Commencer' ? 'start' : group.label === 'S'+'entraîner' ? 'train' : group.label === 'Explorer' ? 'explore' : 'space'}`)">
          <p>{{ t(`nav.${group.label === 'Commencer' ? 'start' : group.label === 'S'+'entraîner' ? 'train' : group.label === 'Explorer' ? 'explore' : 'space'}`) }}</p>
          <RouterLink v-for="item in group.items" :key="item.to" :to="item.to" class="nav-item">
            <component :is="item.icon" :size="18" stroke-width="2" aria-hidden="true" />
            <span>{{ t(item.labelKey) }}</span>
          </RouterLink>
        </section>
      </nav>

      <div class="sidebar-foot">
        <BookOpen :size="17" aria-hidden="true" />
        <span>{{ t('app.localData') }}</span>
      </div>
    </aside>

    <section class="workspace">
      <!-- ── Topbar: header complet ──────────────────────────────── -->
      <header class="topbar">
        <!-- Space selector -->
        <div class="topbar-chip" title="Espace d'apprentissage actif">
          <FolderOpen :size="14" aria-hidden="true" />
          <select
            :value="activeSubjectId"
            aria-label="Espace d'apprentissage actif"
            @change="onSubjectChange"
          >
            <option v-if="!subjects.length" value="" disabled>— importez un document —</option>
            <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>

        <!-- Learner selector -->
        <div class="topbar-chip" title="Apprenant actif">
          <Users :size="14" aria-hidden="true" />
          <select
            :value="activeLearnerId"
            aria-label="Apprenant actif"
            :disabled="!learners.length"
            @change="onLearnerChange"
          >
            <option v-if="!learners.length" value="" disabled>— aucun apprenant —</option>
            <option v-for="l in learners" :key="l.id" :value="l.id">{{ l.name }}</option>
          </select>
          <button
            type="button"
            class="topbar-icon-btn"
            title="Ajouter un apprenant"
            aria-label="Ajouter un apprenant"
            @click="addLearner"
          >
            <Plus :size="14" aria-hidden="true" />
          </button>
        </div>

        <div class="topbar-spacer" />

        <!-- Engine badge -->
        <div
          v-if="engineReady"
          class="engine-badge"
          :title="engineOcr
            ? 'Moteur : embeddings GGUF locaux · OCR Docling actif'
            : 'Moteur : embeddings via Ollama'"
        >
          <span class="engine-dot" />
          <Sparkles :size="13" aria-hidden="true" />
          <span>{{ engineLabel }}</span>
        </div>

        <!-- Model selectors -->
        <div class="model-group">
          <label class="model-field">
            <span class="model-label">Emb.</span>
            <input
              v-model="searchEmbedding"
              type="search"
              class="model-search"
              placeholder="Filtrer…"
              aria-label="Filtrer les modèles d'embeddings"
            />
            <select
              :value="currentEmbedding"
              :disabled="!embeddingModels.length && !currentEmbedding"
              aria-label="Modèle d'embeddings"
              @change="onEmbeddingChange"
            >
              <option v-if="!embeddingModels.length && !currentEmbedding" value="" disabled>(hors ligne)</option>
              <template v-for="[group, items] in buildModelGroups(embeddingModels, currentEmbedding, modelSources, searchEmbedding)" :key="group">
                <optgroup :label="group">
                  <option v-for="name in items" :key="name" :value="name">{{ name }}</option>
                </optgroup>
              </template>
            </select>
            <button
              type="button"
              class="fav-btn"
              :class="{ on: isFavModel(currentEmbedding) }"
              title="Favori du modèle d'embeddings"
              aria-label="Favori du modèle d'embeddings"
              @click="toggleFavModel(currentEmbedding)"
            >
              {{ isFavModel(currentEmbedding) ? '★' : '☆' }}
            </button>
          </label>

          <label class="model-field">
            <span class="model-label">LLM</span>
            <input
              v-model="searchLLM"
              type="search"
              class="model-search"
              placeholder="Filtrer…"
              aria-label="Filtrer les modèles de langage"
            />
            <select
              :value="currentLLM"
              :disabled="!llmModels.length && !currentLLM"
              aria-label="Modèle de langage"
              @change="onLLMChange"
            >
              <option v-if="!llmModels.length && !currentLLM" value="" disabled>(hors ligne)</option>
              <template v-for="[group, items] in buildModelGroups(llmModels, currentLLM, modelSources, searchLLM)" :key="group">
                <optgroup :label="group">
                  <option v-for="name in items" :key="name" :value="name">{{ name }}</option>
                </optgroup>
              </template>
            </select>
            <button
              type="button"
              class="fav-btn"
              :class="{ on: isFavModel(currentLLM) }"
              title="Favori du modèle de langage"
              aria-label="Favori du modèle de langage"
              @click="toggleFavModel(currentLLM)"
            >
              {{ isFavModel(currentLLM) ? '★' : '☆' }}
            </button>
          </label>
        </div>

        <!-- Status pill -->
        <span class="topbar-status" role="status" aria-live="polite">
          <i /> {{ statusText }}
        </span>

        <!-- Preference controls -->
        <PreferenceControls />
      </header>

      <NexusFlow />
      <main class="page-frame" tabindex="-1">
        <RouterView />
      </main>
    </section>

    <Transition name="toast">
      <div v-if="state.notice" class="toast" role="status" aria-live="polite">
        <span>{{ state.notice }}</span>
        <button type="button" aria-label="Fermer le message" @click="dismissNotice">Fermer</button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* ── Topbar chips (space, learner selectors) ───────────────────── */
.topbar-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  max-width: 240px;
  border: 1px solid var(--indigo-soft, #eef0ff);
  background: rgba(79, 70, 229, 0.06);
  color: var(--indigo-deep, #3730a3);
  border-radius: 999px;
  padding: 3px 10px 3px 10px;
  font-size: 12px;
  font-weight: 600;
  flex: 0 0 auto;
}

.topbar-chip select {
  border: none;
  background: transparent;
  padding: 0;
  width: auto;
  max-width: 150px;
  font-weight: 700;
  color: inherit;
  font-size: 12px;
  text-overflow: ellipsis;
  min-height: auto;
}

.topbar-chip select:focus {
  box-shadow: none;
  outline: 0;
}

.topbar-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 1px solid var(--line, #d9def0);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.8);
  color: var(--indigo-deep, #3730a3);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
  padding: 0;
  flex: 0 0 auto;
}

.topbar-icon-btn:hover {
  border-color: var(--indigo, #4f46e5);
  color: var(--indigo);
  background: var(--indigo-soft, #eef0ff);
}

/* ── Spacer ────────────────────────────────────────────────────── */
.topbar-spacer {
  flex: 1 1 0;
  min-width: 8px;
}

/* ── Engine badge ──────────────────────────────────────────────── */
.engine-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  color: var(--muted, #667085);
  border: 1px solid var(--line, #d9def0);
  border-radius: 999px;
  padding: 3px 10px;
  background: rgba(255, 255, 255, 0.78);
  flex: 0 0 auto;
}

.engine-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green, #137a52);
  flex: 0 0 auto;
}

/* ── Model group ───────────────────────────────────────────────── */
.model-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.model-field {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--muted, #667085);
  min-width: 0;
  flex: 0 0 auto;
}

.model-label {
  font-weight: 700;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

.model-search {
  border: 1px solid var(--line, #d9def0);
  background: rgba(255, 255, 255, 0.85);
  border-radius: 8px;
  padding: 3px 6px;
  font-size: 11px;
  width: 65px;
  color: var(--ink, #1e1b4b);
  min-height: auto;
}

.model-search:focus {
  outline: none;
  border-color: var(--indigo, #4f46e5);
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.12);
}

.model-search::placeholder {
  color: var(--faint, #98a2b3);
  opacity: 0.7;
}

.model-field select {
  border: 1px solid var(--line, #d9def0);
  background: rgba(255, 255, 255, 0.85);
  border-radius: 8px;
  padding: 4px 6px;
  font-size: 11px;
  max-width: 140px;
  color: var(--ink, #1e1b4b);
  text-overflow: ellipsis;
  min-height: auto;
}

.model-field select:focus {
  border-color: var(--indigo, #4f46e5);
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.12);
  outline: 0;
}

/* ── Favorite button ───────────────────────────────────────────── */
.fav-btn {
  border: 1px solid var(--line, #d9def0);
  background: rgba(255, 255, 255, 0.85);
  border-radius: 6px;
  width: 24px;
  height: 24px;
  line-height: 1;
  font-size: 14px;
  cursor: pointer;
  color: var(--muted, #667085);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex: 0 0 auto;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.fav-btn:hover {
  border-color: var(--indigo, #4f46e5);
  color: var(--indigo);
}

.fav-btn.on {
  color: #f5b301;
  border-color: #f5b301;
  background: rgba(245, 179, 1, 0.12);
}

/* ── Status pill ───────────────────────────────────────────────── */
.topbar-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted, #667085);
  font-size: 12px;
  font-weight: 600;
  min-width: 50px;
  justify-content: flex-end;
  flex: 0 0 auto;
}

.topbar-status i,
:deep(.topbar-status i) {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 99px;
  background: var(--green, #137a52);
  box-shadow: 0 0 0 3px rgba(19, 122, 82, 0.12);
}

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 1360px) {
  .model-field select {
    max-width: 110px;
  }
  .model-search {
    width: 55px;
  }
  .engine-badge {
    padding: 3px 8px;
  }
  .topbar-chip {
    max-width: 180px;
  }
}

@media (max-width: 1120px) {
  .model-group {
    display: none;
  }
  .engine-badge {
    display: none;
  }
}

@media (max-width: 800px) {
  .topbar-chip {
    display: none;
  }
  .topbar-status {
    display: none;
  }
}
</style>
