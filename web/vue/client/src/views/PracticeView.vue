<!-- EduNexus UI direction: Atelier de progression — l’exercice cible une notion et explique son niveau de difficulté. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowRight, Lightbulb, Target, Loader2, Send, Eye, Sparkles, CheckCircle2, AlertCircle, ChevronDown } from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";

const { state, weakestConcept } = useLearningStore();
const { t } = usePreferences();

const conceptInput = ref("");
const selectedConceptId = ref("");
const difficulty = ref("adaptatif");
const generating = ref(false);
const error = ref<string | null>(null);
const showNewConcept = ref(false);
const newConceptName = ref("");
const creatingConcept = ref(false);

// Exercise state
interface Exercise {
  id: string;
  statement: string;
  difficulty: string;
  hints: string[];
  hint_level: number;
  status: string;
}
const exercise = ref<Exercise | null>(null);
const answer = ref("");
const submitting = ref(false);
const feedback = ref<string | null>(null);
const verdict = ref<string | null>(null);
const hintLevel = ref(0);
const revealedHint = ref<string | null>(null);
const solution = ref<string | null>(null);
const revealingSolution = ref(false);
const askingHint = ref(false);

const subjectId = computed(() => state.data?.subject.id ?? "");
const concepts = computed(() => state.data?.concepts ?? []);
const activeConcept = computed(() => {
  if (selectedConceptId.value) return concepts.value.find((c) => c.id === selectedConceptId.value) ?? weakestConcept.value;
  return weakestConcept.value;
});

const difficultyMap: Record<string, string> = { guided: "easy", adaptatif: "medium", challenge: "hard" };
const apiDifficulty = computed(() => difficultyMap[difficulty.value] ?? "medium");

onMounted(() => {
  // pre-select weakest concept when available
  if (!selectedConceptId.value && weakestConcept.value) selectedConceptId.value = weakestConcept.value.id;
});

async function ensureConceptId(): Promise<string> {
  if (selectedConceptId.value) return selectedConceptId.value;
  if (conceptInput.value.trim()) {
    // try to find by name
    const found = concepts.value.find((c) => c.name.toLowerCase() === conceptInput.value.trim().toLowerCase());
    if (found) { selectedConceptId.value = found.id; return found.id; }
  }
  throw new Error("Sélectionnez une notion d'abord.");
}

async function createConcept() {
  const name = newConceptName.value.trim();
  if (!name || !subjectId.value) return;
  creatingConcept.value = true;
  error.value = null;
  try {
    const created = await tutorApi.createConcept(subjectId.value, name);
    // refresh concepts via reload — fallback to injecting locally
    const newId = (created as unknown as { id: string }).id;
    // push into store if possible
    if (state.data) {
      state.data.concepts.push({ id: newId, name, score: 0, recentFailures: 0 } as never);
    }
    selectedConceptId.value = newId;
    showNewConcept.value = false;
    newConceptName.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Création impossible";
  } finally { creatingConcept.value = false; }
}

async function generate() {
  error.value = null;
  feedback.value = null;
  verdict.value = null;
  revealedHint.value = null;
  solution.value = null;
  answer.value = "";
  try {
    const cid = await ensureConceptId();
    generating.value = true;
    const ex = await tutorApi.generateExercise(cid, apiDifficulty.value);
    exercise.value = {
      id: (ex as unknown as { id: string }).id,
      statement: (ex as unknown as { statement: string }).statement,
      difficulty: (ex as unknown as { difficulty: string }).difficulty,
      hints: ((ex as unknown as { hints: string[] }).hints ?? []) as string[],
      hint_level: (ex as unknown as { hint_level: number }).hint_level ?? 0,
      status: (ex as unknown as { status: string }).status ?? "open",
    };
    hintLevel.value = exercise.value.hint_level;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Génération impossible";
  } finally { generating.value = false; }
}

async function submitAnswer() {
  if (!exercise.value || !answer.value.trim()) return;
  submitting.value = true;
  error.value = null;
  try {
    const res = await tutorApi.gradeExercise(exercise.value.id, answer.value.trim(), false);
    verdict.value = (res as unknown as { verdict: string | null }).verdict;
    feedback.value = (res as unknown as { feedback: string }).feedback;
    hintLevel.value = (res as unknown as { hint_level: number }).hint_level ?? hintLevel.value;
    if ((res as unknown as { hint: string | null }).hint) revealedHint.value = (res as unknown as { hint: string }).hint;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Correction impossible";
  } finally { submitting.value = false; }
}

async function requestHint() {
  if (!exercise.value) return;
  askingHint.value = true;
  try {
    const res = await tutorApi.gradeExercise(exercise.value.id, answer.value.trim(), true);
    hintLevel.value = (res as unknown as { hint_level: number }).hint_level ?? hintLevel.value + 1;
    if ((res as unknown as { hint: string | null }).hint) revealedHint.value = (res as unknown as { hint: string }).hint;
    else if (exercise.value.hints[hintLevel.value]) revealedHint.value = exercise.value.hints[hintLevel.value];
    if ((res as unknown as { feedback: string }).feedback) feedback.value = (res as unknown as { feedback: string }).feedback;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Indice indisponible";
  } finally { askingHint.value = false; }
}

async function revealSolution() {
  if (!exercise.value) return;
  revealingSolution.value = true;
  try {
    const res = await tutorApi.requestSolution(exercise.value.id);
    solution.value = (res as unknown as { solution: string }).solution;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Solution indisponible";
  } finally { revealingSolution.value = false; }
}

const verdictTone = computed(() => verdict.value === "correct" ? "green" : verdict.value === "partial" ? "indigo" : verdict.value === "incorrect" ? "orange" : "slate");
const verdictLabel = computed(() => verdict.value === "correct" ? "Correct" : verdict.value === "partial" ? "Partiel" : verdict.value === "incorrect" ? "À retravailler" : "");
</script>

<template>
  <section class="page practice-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('practice.kicker') }}</p>
        <h1>{{ t('practice.title') }}</h1>
        <p>{{ t('practice.copy') }}</p>
      </div>
    </header>

    <section class="practice-layout">
      <!-- Left: config + exercise -->
      <div class="practice-main">
        <article class="content-panel exercise-config">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ t('practice.prepare') }}</p>
              <h2>{{ t('practice.adaptive') }}</h2>
            </div>
            <Target :size="21" aria-hidden="true" />
          </div>

          <label class="field-label" for="practice-concept">{{ t('practice.concept') }}</label>
          <div class="concept-select-row">
            <select id="practice-concept" v-model="selectedConceptId">
              <option value="" disabled>— choisir —</option>
              <option v-for="item in concepts" :key="item.id" :value="item.id">{{ item.name }} · {{ item.score }} %</option>
            </select>
            <button type="button" class="secondary-action" style="min-height:36px;padding:7px 10px;white-space:nowrap;" @click="showNewConcept = !showNewConcept">
              <Sparkles :size="14" aria-hidden="true" /> Nouvelle notion
            </button>
          </div>

          <div v-if="showNewConcept" class="new-concept-row">
            <input v-model="newConceptName" type="text" placeholder="Nom de la notion…" @keydown.enter.prevent="createConcept" />
            <button type="button" class="primary-action" style="min-height:36px;padding:7px 12px;" :disabled="creatingConcept || !newConceptName.trim()" @click="createConcept">
              <Loader2 v-if="creatingConcept" :size="14" class="spin" /> <span v-else>Créer</span>
            </button>
            <button type="button" class="text-button" @click="showNewConcept=false">Annuler</button>
          </div>

          <!-- quick free text fallback -->
          <input v-if="!concepts.length" v-model="conceptInput" type="text" placeholder="Ex : boucles, conditions…" style="margin-top:8px;" />

          <label class="field-label" for="difficulty">{{ t('practice.difficulty') }}</label>
          <div id="difficulty" class="segmented" role="group" :aria-label="t('practice.difficulty')">
            <button
              v-for="level in [{ id: 'guided', label: t('practice.guided') }, { id: 'adaptatif', label: t('practice.adaptiveLevel') }, { id: 'challenge', label: t('practice.challenge') }]"
              :key="level.id"
              type="button"
              :class="{ selected: difficulty === level.id }"
              @click="difficulty = level.id"
            >{{ level.label }}</button>
          </div>

          <div class="recommendation">
            <Lightbulb :size="19" aria-hidden="true" />
            <p><strong>{{ t('practice.advice') }}</strong> {{ activeConcept?.score != null && activeConcept.score < 40 ? t('practice.guidedAdvice') : t('practice.adaptiveAdvice') }}</p>
          </div>

          <button type="button" class="primary-action" :disabled="generating" @click="generate">
            <Loader2 v-if="generating" :size="17" class="spin" aria-hidden="true" />
            <ArrowRight v-else :size="17" aria-hidden="true" />
            {{ generating ? "Génération…" : t('practice.generate') }}
          </button>

          <p v-if="error" class="field-error" role="alert" style="margin-top:10px;">{{ error }}</p>
        </article>

        <!-- Exercise card -->
        <article v-if="exercise" class="content-panel exercise-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Énoncé · {{ exercise.difficulty }}</p>
              <h2 style="font-size:18px;">Exercice</h2>
            </div>
            <span class="status-pill status-pill--indigo" style="font-size:11px;">{{ hintLevel }}/3 indices</span>
          </div>

          <div class="statement-box">
            <p class="statement-text">{{ exercise.statement }}</p>
          </div>

          <!-- hints ladder -->
          <div v-if="revealedHint" class="hint-box">
            <Lightbulb :size="16" aria-hidden="true" />
            <p><strong>Indice {{ hintLevel }} :</strong> {{ revealedHint }}</p>
          </div>
          <div v-if="exercise.hints.length && !revealedHint" class="hint-dots">
            <span v-for="(_, i) in exercise.hints" :key="i" class="hint-dot" :class="{ done: i < hintLevel, next: i === hintLevel }"></span>
          </div>

          <label class="field-label" for="practice-answer">Votre réponse</label>
          <textarea id="practice-answer" v-model="answer" rows="4" placeholder="Rédigez votre réponse…" />

          <div class="exercise-actions">
            <button type="button" class="primary-action" :disabled="submitting || !answer.trim()" @click="submitAnswer">
              <Loader2 v-if="submitting" :size="16" class="spin" />
              <Send v-else :size="16" aria-hidden="true" />
              Envoyer
            </button>
            <button type="button" class="secondary-action" :disabled="askingHint || hintLevel >= 3" @click="requestHint">
              <Lightbulb :size="16" aria-hidden="true" />
              {{ askingHint ? "…" : "Indice" }}
            </button>
            <button type="button" class="text-button" :disabled="revealingSolution || !!solution" @click="revealSolution">
              <Eye :size="16" aria-hidden="true" />
              {{ solution ? "Solution affichée" : revealingSolution ? "…" : "Voir la solution" }}
            </button>
          </div>

          <!-- feedback -->
          <div v-if="feedback || verdictLabel" class="feedback-box" :class="`tone-${verdictTone}`">
            <div class="feedback-head">
              <CheckCircle2 v-if="verdict==='correct'" :size="18" aria-hidden="true" />
              <AlertCircle v-else :size="18" aria-hidden="true" />
              <strong>{{ verdictLabel || "Retour" }}</strong>
            </div>
            <p v-if="feedback" class="feedback-text">{{ feedback }}</p>
          </div>

          <div v-if="solution" class="solution-box">
            <p class="eyebrow">Solution</p>
            <p class="solution-text">{{ solution }}</p>
          </div>
        </article>

        <article v-else-if="!generating" class="content-panel exercise-placeholder">
          <Target :size="28" aria-hidden="true" />
          <h3>Aucun exercice encore</h3>
          <p>Choisissez une notion et un niveau, puis générez votre premier exercice adaptatif.</p>
        </article>
      </div>

      <!-- Right: preview / context -->
      <aside class="practice-side">
        <article class="exercise-preview">
          <span class="preview-label">{{ t('practice.preview') }}</span>
          <p class="eyebrow">{{ activeConcept?.name ?? t('practice.concept') }}</p>
          <h2 v-if="exercise">{{ exercise.statement.slice(0, 80) }}{{ exercise.statement.length > 80 ? "…" : "" }}</h2>
          <h2 v-else>{{ t('practice.previewTitle') }}</h2>
          <p>{{ t('practice.previewCopy') }}</p>
          <div class="preview-foot">
            <span>{{ t('practice.source') }} : {{ state.data?.books[0]?.title ?? "—" }}</span>
            <span>{{ t('practice.duration') }} : {{ t('practice.minutes') }}</span>
          </div>
        </article>

        <article class="content-panel side-tip">
          <div class="panel-heading" style="margin-bottom:10px;">
            <div><p class="eyebrow">Astuce</p><h2 style="font-size:18px;">Indice gradué</h2></div>
            <ChevronDown :size="16" aria-hidden="true" />
          </div>
          <p>Chaque exercice propose 3 indices. Le premier cadre la méthode, le dernier resserre sur la solution sans la donner. La solution ne s'affiche que sur demande explicite.</p>
        </article>
      </aside>
    </section>
  </section>
</template>

<style scoped>
.practice-layout { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(300px, .75fr); gap: 16px; align-items: start; }
.practice-main { display: grid; gap: 16px; }
.concept-select-row { display: flex; gap: 8px; align-items: center; }
.concept-select-row select { flex: 1 1 0; min-width: 0; }
.new-concept-row { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.new-concept-row input { flex: 1 1 0; }
.field-error { color: #b42318; font-size: 12px; font-weight: 600; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
.exercise-placeholder { display: grid; place-items: center; gap: 8px; padding: 32px 22px; text-align: center; }
.exercise-placeholder h3 { margin: 6px 0 0; font: 700 18px/1.2 "Fraunces", Georgia, serif; }
.exercise-placeholder p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; max-width: 420px; }
.exercise-card { padding: 25px; display: grid; gap: 14px; }
.statement-box { padding: 16px 18px; border: 1px solid #d9defb; border-radius: 14px; background: #f7f8ff; }
.statement-text { margin: 0; color: var(--ink); font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.hint-box { display: flex; gap: 10px; padding: 12px 14px; border: 1px solid #cbd1ff; border-radius: 12px; background: var(--indigo-soft); }
.hint-box p { margin: 0; font-size: 13px; line-height: 1.5; }
.hint-dots { display: flex; gap: 6px; align-items: center; }
.hint-dot { width: 10px; height: 10px; border-radius: 50%; border: 1.5px solid #d9def0; background: #fff; }
.hint-dot.done { background: var(--indigo); border-color: var(--indigo); }
.hint-dot.next { border-color: var(--indigo); }
.exercise-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.feedback-box { padding: 14px; border-radius: 12px; border: 1px solid var(--line); }
.feedback-box.tone-green { border-color: #b9e9d0; background: var(--green-soft); }
.feedback-box.tone-orange { border-color: #f5bb9e; background: var(--orange-soft); }
.feedback-box.tone-indigo { border-color: #cbd1ff; background: var(--indigo-soft); }
.feedback-box.tone-slate { background: #fff; }
.feedback-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.feedback-text { margin: 8px 0 0; font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
.solution-box { padding: 14px; border: 1px dashed var(--line); border-radius: 12px; background: #fff; }
.solution-box .eyebrow { margin: 0 0 6px; }
.solution-text { margin: 0; font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
.side-tip { padding: 20px; }
.side-tip p { margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
@media (max-width: 980px) { .practice-layout { grid-template-columns: 1fr; } .concept-select-row { flex-direction: column; align-items: stretch; } }
</style>
