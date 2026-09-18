<!-- EduNexus UI direction: Carnet de sujet — notes, sources et outputs organisés pour chaque matière. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { BookMarked, FileText, Lightbulb, Link2, ListChecks, Loader2, PencilLine, Plus, Trash2, Wand2 } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi, invalidateSubjectCaches } from "@/services/api";

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

/* ── 012 US3 (FR-010) · Notes atomiques liées ────────────────────────
   Une idée par note (titre = affirmation, corps en propres mots), liens
   typés, plan assemblé en lecture seule. Le carnet ci-dessus est inchangé. */
const { locale } = usePreferences();
const amsg = computed(() => locale.value === "fr" ? {
  kicker: "Niveau universitaire",
  title: "Notes atomiques",
  copy: "Une idée par note : le titre est une affirmation, le corps vos propres mots. Reliez-les, puis assemblez un plan.",
  affLabel: "Affirmation (titre)",
  affPh: "La photosynthèse produit du glucose…",
  bodyLabel: "En propres mots",
  bodyPh: "Reformulez l’idée avec vos mots…",
  add: "Ajouter la note",
  adding: "Ajout…",
  noNotes: "Aucune note atomique pour l’instant.",
  linkTitle: "Relier deux notes",
  from: "Depuis",
  to: "Vers",
  rel: "Relation",
  rels: [
    { key: "précise", label: "précise" },
    { key: "contredit", label: "contredit" },
    { key: "mécanisme-de", label: "mécanisme-de" },
    { key: "exemple-de", label: "exemple-de" },
  ] as Array<{ key: string; label: string }>,
  link: "Relier",
  linking: "Liaison…",
  planTitle: "Assembler un plan",
  planPh: "Votre question…",
  assemble: "Assembler",
  assembling: "Assemblage…",
  noPlan: "Le plan assemblé depuis vos liens apparaîtra ici.",
  deleteNote: "Supprimer cette note",
  error: "Opération impossible — réessayez.",
} : {
  kicker: "Higher education",
  title: "Atomic notes",
  copy: "One idea per note: the title is a claim, the body your own words. Link them, then assemble an outline.",
  affLabel: "Claim (title)",
  affPh: "Photosynthesis produces glucose…",
  bodyLabel: "In your own words",
  bodyPh: "Restate the idea in your own words…",
  add: "Add the note",
  adding: "Adding…",
  noNotes: "No atomic notes yet.",
  linkTitle: "Link two notes",
  from: "From",
  to: "To",
  rel: "Relation",
  rels: [
    { key: "précise", label: "specifies" },
    { key: "contredit", label: "contradicts" },
    { key: "mécanisme-de", label: "mechanism-of" },
    { key: "exemple-de", label: "example-of" },
  ] as Array<{ key: string; label: string }>,
  link: "Link",
  linking: "Linking…",
  planTitle: "Assemble an outline",
  planPh: "Your question…",
  assemble: "Assemble",
  assembling: "Assembling…",
  noPlan: "The outline assembled from your links will show up here.",
  deleteNote: "Delete this note",
  error: "Operation failed — please retry.",
});

interface AtomicLink { from_id: string; to_id: string; rel: string; }
interface AtomicNote { id: string; title: string; body: string; links: AtomicLink[]; created_at: string; }
interface PlanSection { id: string; title: string; body: string; rel: string | null; }

const atomicNotes = ref<AtomicNote[]>([]);
const atomicLoading = ref(false);
const atomicError = ref<string | null>(null);
const newAtomicTitle = ref("");
const newAtomicBody = ref("");
const submittingAtomic = ref(false);
const linkFrom = ref("");
const linkTo = ref("");
const linkRel = ref("précise");
const linkingAtomic = ref(false);
const planQuestion = ref("");
const assemblingPlan = ref(false);
const assembledPlan = ref<{ question: string; sections: PlanSection[]; plan: string[] } | null>(null);

async function loadAtomicNotes() {
  atomicLoading.value = true; atomicError.value = null;
  try {
    const res = await tutorApi.getAtomicNotes() as unknown as { notes?: AtomicNote[] };
    atomicNotes.value = res.notes ?? [];
  } catch {
    atomicError.value = amsg.value.error;
    atomicNotes.value = [];
  } finally {
    atomicLoading.value = false;
  }
}

async function createAtomicNote() {
  const title = newAtomicTitle.value.trim();
  const body = newAtomicBody.value.trim();
  if (!title || !body || submittingAtomic.value) return;
  submittingAtomic.value = true; atomicError.value = null;
  try {
    const res = await tutorApi.createAtomicNote({ title, body }) as unknown as { note: AtomicNote };
    atomicNotes.value = [...atomicNotes.value, { ...(res.note as AtomicNote), links: (res.note as AtomicNote).links ?? [] }];
    newAtomicTitle.value = "";
    newAtomicBody.value = "";
    invalidateSubjectCaches();
  } catch {
    atomicError.value = amsg.value.error;
  } finally {
    submittingAtomic.value = false;
  }
}

async function deleteAtomicNote(noteId: string) {
  try {
    await tutorApi.deleteAtomicNote(noteId);
    atomicNotes.value = atomicNotes.value.filter((n) => n.id !== noteId);
    invalidateSubjectCaches();
  } catch {
    atomicError.value = amsg.value.error;
  }
}

async function linkAtomicNotes() {
  if (!linkFrom.value || !linkTo.value || linkingAtomic.value) return;
  linkingAtomic.value = true; atomicError.value = null;
  try {
    await tutorApi.linkAtomicNote(linkFrom.value, { to_id: linkTo.value, rel: linkRel.value });
    await loadAtomicNotes();
    invalidateSubjectCaches();
  } catch {
    atomicError.value = amsg.value.error;
  } finally {
    linkingAtomic.value = false;
  }
}

async function assembleAtomicPlan() {
  const q = planQuestion.value.trim();
  if (!q || assemblingPlan.value) return;
  assemblingPlan.value = true; atomicError.value = null;
  try {
    const res = await tutorApi.assembleAtomicPlan(q) as unknown as { question: string; sections: PlanSection[]; plan: string[] };
    assembledPlan.value = res;
  } catch {
    atomicError.value = amsg.value.error;
  } finally {
    assemblingPlan.value = false;
  }
}

onMounted(() => {
  void fetchNotebook();
  void loadAtomicNotes();
});
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

  <!-- 012 US3 (FR-010) · Notes atomiques liées (toujours visible, carnet inchangé) -->
  <section v-if="!loading && !error" class="page notebook-page atomic-block">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ amsg.kicker }}</p>
        <h1>{{ amsg.title }}</h1>
        <p>{{ amsg.copy }}</p>
      </div>
    </header>

    <p v-if="atomicError" class="error-text" role="alert">{{ atomicError }}</p>

    <section class="notebook-layout atomic-layout">
      <div class="notebook-columns">
        <section class="content-panel notes-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ amsg.title }}</p>
              <h2>{{ atomicNotes.length }} note(s)</h2>
            </div>
            <PencilLine :size="20" aria-hidden="true" />
          </div>
          <form class="note-form" @submit.prevent="createAtomicNote">
            <label class="field-label" for="atomic-title">{{ amsg.affLabel }}</label>
            <input id="atomic-title" v-model="newAtomicTitle" type="text" maxlength="120" :placeholder="amsg.affPh" />
            <label class="field-label" for="atomic-body">{{ amsg.bodyLabel }}</label>
            <textarea id="atomic-body" v-model="newAtomicBody" rows="3" :placeholder="amsg.bodyPh" :aria-label="amsg.bodyLabel"></textarea>
            <button type="submit" class="primary-action" :disabled="!newAtomicTitle.trim() || !newAtomicBody.trim() || submittingAtomic">
              <Loader2 v-if="submittingAtomic" :size="16" class="spin" aria-hidden="true" />
              <Plus v-else :size="16" aria-hidden="true" />
              {{ submittingAtomic ? amsg.adding : amsg.add }}
            </button>
          </form>
          <p v-if="atomicLoading" class="empty-copy"><Loader2 :size="16" class="spin" aria-hidden="true" /></p>
          <ul v-else-if="atomicNotes.length" class="note-list">
            <li v-for="note in atomicNotes" :key="note.id" class="note-item">
              <div class="output-head">
                <strong>{{ note.title }}</strong>
                <button type="button" class="icon-button" :aria-label="amsg.deleteNote" @click="deleteAtomicNote(note.id)">
                  <Trash2 :size="15" />
                </button>
              </div>
              <p>{{ note.body }}</p>
              <small v-if="note.links?.length">{{ note.links.map((l) => l.rel + ' → ' + l.to_id.slice(0, 6)).join(' · ') }}</small>
            </li>
          </ul>
          <p v-else class="empty-copy">{{ amsg.noNotes }}</p>
        </section>
      </div>

      <aside class="notebook-side">
        <section class="content-panel action-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ amsg.linkTitle }}</p>
              <h2>{{ amsg.linkTitle }}</h2>
            </div>
            <Link2 :size="20" aria-hidden="true" />
          </div>
          <label class="field-label" for="atomic-from">{{ amsg.from }}</label>
          <select id="atomic-from" v-model="linkFrom">
            <option value="">{{ amsg.from }}…</option>
            <option v-for="n in atomicNotes" :key="'f' + n.id" :value="n.id">{{ n.title }}</option>
          </select>
          <label class="field-label" for="atomic-to">{{ amsg.to }}</label>
          <select id="atomic-to" v-model="linkTo">
            <option value="">{{ amsg.to }}…</option>
            <option v-for="n in atomicNotes" :key="'t' + n.id" :value="n.id">{{ n.title }}</option>
          </select>
          <label class="field-label" for="atomic-rel">{{ amsg.rel }}</label>
          <select id="atomic-rel" v-model="linkRel">
            <option v-for="r in amsg.rels" :key="r.key" :value="r.key">{{ r.label }}</option>
          </select>
          <button type="button" class="primary-action" :disabled="!linkFrom || !linkTo || linkingAtomic" @click="linkAtomicNotes">
            <Loader2 v-if="linkingAtomic" :size="16" class="spin" aria-hidden="true" />
            <Link2 v-else :size="16" aria-hidden="true" />
            {{ linkingAtomic ? amsg.linking : amsg.link }}
          </button>
        </section>

        <section class="content-panel outputs-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ amsg.planTitle }}</p>
              <h2>{{ amsg.planTitle }}</h2>
            </div>
            <ListChecks :size="20" aria-hidden="true" />
          </div>
          <form class="note-form" @submit.prevent="assembleAtomicPlan">
            <input v-model="planQuestion" type="text" :placeholder="amsg.planPh" :aria-label="amsg.planTitle" />
            <button type="submit" class="primary-action" :disabled="!planQuestion.trim() || assemblingPlan">
              <Loader2 v-if="assemblingPlan" :size="16" class="spin" aria-hidden="true" />
              {{ assemblingPlan ? amsg.assembling : amsg.assemble }}
            </button>
          </form>
          <ol v-if="assembledPlan?.sections.length" class="output-list">
            <li v-for="s in assembledPlan.sections" :key="s.id" class="output-item">
              <strong>{{ s.title }}</strong>
              <small v-if="s.rel">{{ s.rel }}</small>
              <p>{{ s.body }}</p>
            </li>
          </ol>
          <p v-else class="empty-copy">{{ amsg.noPlan }}</p>
        </section>
      </aside>
    </section>
  </section>
</template>
