<!-- EduNexus UI direction: Carnet de sujet — notes, sources et outputs organisés pour chaque matière. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { BookMarked, FileText, Lightbulb, ListChecks, Loader2, PencilLine, Plus, Trash2, Wand2 } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state } = useLearningStore();
const { t } = usePreferences();

/** ID de la matière active */
const subjectId = computed(() => state.data?.subject.id ?? "");

/* ── Données du carnet ──────────────────────────────────────────── */
interface NotebookNote { id: string; content: string; created_at: string; }
interface NotebookSource { id: string; title: string; chapter: string; }
interface NotebookOutput { id: string; action: string; title: string; content: string; created_at: string; }
interface Notebook { notes: NotebookNote[]; sources: NotebookSource[]; outputs: NotebookOutput[]; }

const notebook = ref<Notebook | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);

/** Action sélectionnée dans le dropdown */
const selectedAction = ref("summarize_source");

/** Liste des actions disponibles */
const actions = [
  { value: "summarize_source", label: "notebook.actionSummarize" },
  { value: "compare_chapters", label: "notebook.actionCompare" },
  { value: "create_study_sheet", label: "notebook.actionStudySheet" },
  { value: "quiz_without_answer", label: "notebook.actionQuiz" },
  { value: "explain_with_example", label: "notebook.actionExplain" },
] as const;

/** Champ de nouvelle note */
const newNote = ref("");
const submittingNote = ref(false);
const actionLoading = ref(false);

/** Chargement du carnet */
async function fetchNotebook() {
  if (!subjectId.value) return;
  loading.value = true;
  error.value = null;
  try {
    const response = await fetch(`/api/tutor/subjects/${subjectId.value}/notebook`);
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    notebook.value = await response.json();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de charger le carnet.";
  } finally {
    loading.value = false;
  }
}

/** Ajouter une note */
async function addNote() {
  if (!newNote.value.trim() || !subjectId.value) return;
  submittingNote.value = true;
  try {
    const response = await fetch(`/api/tutor/subjects/${subjectId.value}/notebook/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: newNote.value.trim() }),
    });
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    const created: NotebookNote = await response.json();
    notebook.value?.notes.unshift(created);
    newNote.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible d'ajouter la note.";
  } finally {
    submittingNote.value = false;
  }
}

/** Lancer une action sur le carnet */
async function runAction() {
  if (!subjectId.value) return;
  actionLoading.value = true;
  try {
    const response = await fetch(`/api/tutor/subjects/${subjectId.value}/notebook/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: selectedAction.value, params: {} }),
    });
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    const output: NotebookOutput = await response.json();
    notebook.value?.outputs.unshift(output);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de lancer l'action.";
  } finally {
    actionLoading.value = false;
  }
}

/** Supprimer un output */
async function deleteOutput(outputId: string) {
  try {
    const response = await fetch(`/api/tutor/notebook-outputs/${outputId}`, { method: "DELETE" });
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    if (notebook.value) {
      notebook.value.outputs = notebook.value.outputs.filter((o) => o.id !== outputId);
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de supprimer l'output.";
  }
}

/** État vide du carnet */
const isEmpty = computed(() => {
  if (!notebook.value) return false;
  return notebook.value.notes.length === 0 && notebook.value.sources.length === 0 && notebook.value.outputs.length === 0;
});

onMounted(fetchNotebook);
</script>

<template>
  <!-- Chargement -->
  <section v-if="loading" class="page loading-state">
    <Loader2 :size="24" class="spin" aria-hidden="true" />
    <p>{{ t('notebook.loading') }}</p>
  </section>

  <!-- Erreur -->
  <section v-else-if="error" class="page loading-state">
    <p class="error-text">{{ error }}</p>
    <button type="button" class="secondary-action" @click="fetchNotebook">{{ t('notebook.retry') }}</button>
  </section>

  <!-- État vide -->
  <section v-else-if="isEmpty" class="page notebook-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('notebook.kicker') }}</p>
        <h1>{{ t('notebook.emptyTitle') }}</h1>
        <p>{{ t('notebook.emptyCopy') }}</p>
      </div>
    </header>
  </section>

  <!-- Carnet -->
  <section v-else class="page notebook-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('notebook.kicker') }}</p>
        <h1>{{ t('notebook.title') }}</h1>
        <p>{{ t('notebook.copy') }}</p>
      </div>
    </header>

    <!-- Grille principale : notes + actions -->
    <section class="notebook-layout">

      <!-- Colonne gauche : notes et sources -->
      <div class="notebook-columns">

        <!-- Section Notes -->
        <section class="content-panel notes-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ t('notebook.notesSection') }}</p>
              <h2>{{ t('notebook.notesTitle', { count: notebook?.notes.length ?? 0 }) }}</h2>
            </div>
            <PencilLine :size="20" aria-hidden="true" />
          </div>

          <!-- Formulaire d'ajout de note -->
          <form class="note-form" @submit.prevent="addNote">
            <textarea
              v-model="newNote"
              rows="3"
              :placeholder="t('notebook.notePlaceholder')"
              :aria-label="t('notebook.notePlaceholder')"
            ></textarea>
            <button type="submit" class="primary-action" :disabled="!newNote.trim() || submittingNote">
              <Plus :size="16" aria-hidden="true" />
              {{ t('notebook.addNote') }}
            </button>
          </form>

          <!-- Liste des notes -->
          <ul v-if="notebook?.notes.length" class="note-list">
            <li v-for="note in notebook.notes" :key="note.id" class="note-item">
              <p>{{ note.content }}</p>
              <small>{{ note.created_at }}</small>
            </li>
          </ul>
          <p v-else class="empty-copy">{{ t('notebook.noNotes') }}</p>
        </section>

        <!-- Section Sources -->
        <section class="content-panel sources-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ t('notebook.sourcesSection') }}</p>
              <h2>{{ t('notebook.sourcesTitle', { count: notebook?.sources.length ?? 0 }) }}</h2>
            </div>
            <FileText :size="20" aria-hidden="true" />
          </div>
          <ul v-if="notebook?.sources.length" class="source-list">
            <li v-for="source in notebook.sources" :key="source.id" class="source-item">
              <BookMarked :size="16" aria-hidden="true" />
              <div>
                <strong>{{ source.title }}</strong>
                <span>{{ source.chapter }}</span>
              </div>
            </li>
          </ul>
          <p v-else class="empty-copy">{{ t('notebook.noSources') }}</p>
        </section>
      </div>

      <!-- Colonne droite : actions et outputs -->
      <aside class="notebook-side">

        <!-- Sélecteur d'action -->
        <section class="content-panel action-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ t('notebook.actionSection') }}</p>
              <h2>{{ t('notebook.actionTitle') }}</h2>
            </div>
            <Wand2 :size="20" aria-hidden="true" />
          </div>
          <p>{{ t('notebook.actionCopy') }}</p>
          <label class="field-label" for="notebook-action">{{ t('notebook.chooseAction') }}</label>
          <select id="notebook-action" v-model="selectedAction">
            <option v-for="action in actions" :key="action.value" :value="action.value">
              {{ t(action.label) }}
            </option>
          </select>
          <button type="button" class="primary-action" :disabled="actionLoading" @click="runAction">
            <Loader2 v-if="actionLoading" :size="16" class="spin" aria-hidden="true" />
            <Wand2 v-else :size="16" aria-hidden="true" />
            {{ t('notebook.runAction') }}
          </button>
        </section>

        <!-- Liste des outputs -->
        <section class="content-panel outputs-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ t('notebook.outputsSection') }}</p>
              <h2>{{ t('notebook.outputsTitle', { count: notebook?.outputs.length ?? 0 }) }}</h2>
            </div>
            <ListChecks :size="20" aria-hidden="true" />
          </div>
          <ul v-if="notebook?.outputs.length" class="output-list">
            <li v-for="output in notebook.outputs" :key="output.id" class="output-item">
              <div class="output-head">
                <StatusPill tone="indigo">{{ t(`notebook.action.${output.action}`) }}</StatusPill>
                <button type="button" class="icon-button" :aria-label="t('notebook.deleteOutput')" @click="deleteOutput(output.id)">
                  <Trash2 :size="15" />
                </button>
              </div>
              <strong>{{ output.title }}</strong>
              <p>{{ output.content }}</p>
              <small>{{ output.created_at }}</small>
            </li>
          </ul>
          <p v-else class="empty-copy">{{ t('notebook.noOutputs') }}</p>
        </section>
      </aside>
    </section>
  </section>
</template>
