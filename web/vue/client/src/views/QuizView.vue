<!-- EduNexus UI direction: Atelier de progression — une évaluation courte, explicite et reliée à une intention pédagogique. -->
<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowRight, ClipboardCheck, TimerReset, Loader2, CheckCircle2, AlertCircle, FileSearch, Upload, Send, Eye } from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";

const { state } = useLearningStore();
const { t } = usePreferences();

const selected = ref("diagnostic");
const subjectId = computed(() => state.data?.subject.id ?? "");
const error = ref<string | null>(null);

// Quiz state
interface QuizQuestion { id: string; type: string; payload: Record<string, unknown>; points: number; answer?: unknown; }
interface QuizData { id: string; kind: string; status: string; score?: number | null; questions: QuizQuestion[]; report?: unknown; time_limit_s?: number | null; }
const quiz = ref<QuizData | null>(null);
const loadingQuiz = ref(false);
const answers = ref<Record<string, unknown>>({});
const submitting = ref(false);
const report = ref<Record<string, unknown> | null>(null);

// Exam import
const showImport = ref(false);
const examPathsInput = ref("");
const examText = ref("");
const importingExam = ref(false);
const analyzingExam = ref(false);
const analyzedQuestions = ref<unknown[]>([]);

async function ensureSubject(): Promise<string> {
  if (subjectId.value) return subjectId.value;
  const resp = await tutorApi.getSubjects();
  const id = resp.active_id || resp.subjects[0]?.id || "";
  if (!id) throw new Error("Aucun espace actif — importez un document.");
  return id;
}

async function createQuiz() {
  error.value = null; report.value = null; analyzedQuestions.value = [];
  loadingQuiz.value = true;
  try {
    const sid = await ensureSubject();
    if (selected.value === "challenge") {
      const data = await tutorApi.createExam(sid, 10, 600);
      quiz.value = data as unknown as QuizData;
    } else {
      const size = selected.value === "review" ? 5 : 5;
      const kinds = selected.value === "review" ? ["mcq", "true_false"] : ["mcq", "true_false", "open"];
      const data = await tutorApi.createQuiz(sid, size, kinds);
      quiz.value = data as unknown as QuizData;
    }
    answers.value = {};
    // init answers scaffold
    for (const q of quiz.value?.questions ?? []) answers.value[q.id] = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Création impossible";
  } finally { loadingQuiz.value = false; }
}

async function submitQuiz() {
  if (!quiz.value) return;
  submitting.value = true;
  error.value = null;
  try {
    const res = await tutorApi.submitQuiz(quiz.value.id, answers.value, false);
    report.value = res as unknown as { score: number };
    // fetch updated quiz to get score/report
    try {
      const updated = await tutorApi.getQuiz(quiz.value.id);
      quiz.value = updated as unknown as QuizData;
      if ((updated as unknown as { score: unknown }).score != null) report.value = { score: (updated as unknown as { score: number }).score };
      if ((updated as unknown as { report: unknown }).report) report.value = (updated as unknown as { report: { score?: number } }).report as never;
    } catch { /* keep former */ }
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Envoi impossible";
    if (msg.includes("409") || msg.toLowerCase().includes("aide interdite")) error.value = "L'aide est interdite pendant un examen.";
    else error.value = msg;
  } finally { submitting.value = false; }
}

async function doImportExam() {
  const paths = examPathsInput.value.split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
  if (!paths.length) { error.value = "Saisissez au moins un chemin de fichier."; return; }
  importingExam.value = true; error.value = null;
  try {
    const res = await tutorApi.importExam(paths);
    examText.value = (res as unknown as { exam_text: string }).exam_text ?? "";
  } catch (e) { error.value = e instanceof Error ? e.message : "Import impossible"; }
  finally { importingExam.value = false; }
}

async function doAnalyze() {
  if (!examText.value.trim()) { error.value = "Texte d'épreuve vide."; return; }
  analyzingExam.value = true; error.value = null;
  try {
    const res = await tutorApi.analyzeExam(examText.value);
    analyzedQuestions.value = (res as unknown as { questions: unknown[] }).questions ?? [];
  } catch (e) { error.value = e instanceof Error ? e.message : "Analyse impossible"; }
  finally { analyzingExam.value = false; }
}

function qTitle(q: QuizQuestion): string {
  const p = q.payload as Record<string, unknown>;
  return (p.question as string) ?? (p.statement as string) ?? (p.prompt as string) ?? `Question (${q.type})`;
}
function qOptions(q: QuizQuestion): string[] {
  const p = q.payload as Record<string, unknown>;
  const opts = p.options ?? p.choices;
  if (Array.isArray(opts)) return opts as string[];
  return [];
}

const quizProgress = computed(() => {
  if (!quiz.value) return 0;
  const answered = Object.values(answers.value).filter(v => String(v ?? "").trim() !== "").length;
  return Math.round((answered / Math.max(1, quiz.value.questions.length)) * 100);
});
</script>

<template>
  <section class="page quiz-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('quiz.kicker') }}</p>
        <h1>{{ t('quiz.title') }}</h1>
        <p>{{ t('quiz.copy') }}</p>
      </div>
    </header>

    <section class="quiz-layout">
      <!-- Left: choice + quiz -->
      <div class="quiz-main">
        <article class="content-panel quiz-choice">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ t('quiz.launch') }}</p>
              <h2>{{ t('quiz.goal') }}</h2>
            </div>
            <ClipboardCheck :size="21" aria-hidden="true" />
          </div>

          <button
            v-for="option in [{id:'diagnostic',title:t('quiz.diagnostic'),copy:t('quiz.diagnosticCopy')},{id:'review',title:t('quiz.review'),copy:t('quiz.reviewCopy')},{id:'challenge',title:t('quiz.challenge'),copy:t('quiz.challengeCopy')} ]"
            :key="option.id"
            type="button"
            class="choice-row"
            :class="{ selected: selected === option.id }"
            @click="selected = option.id"
          >
            <span><strong>{{ option.title }}</strong><small>{{ option.copy }}</small></span><i />
          </button>

          <div class="duration-row">
            <TimerReset :size="18" aria-hidden="true" />
            <span>{{ t('quiz.duration') }} : <strong>{{ selected === 'challenge' ? '18' : selected === 'review' ? '8' : '10' }} min</strong></span>
            <span v-if="quiz?.time_limit_s" class="time-chip">Limite : {{ Math.round((quiz.time_limit_s ?? 0)/60) }} min</span>
          </div>

          <button type="button" class="primary-action" :disabled="loadingQuiz" @click="createQuiz">
            <Loader2 v-if="loadingQuiz" :size="17" class="spin" aria-hidden="true" />
            <ArrowRight v-else :size="17" aria-hidden="true" />
            {{ loadingQuiz ? "Préparation…" : t('quiz.prepare') }}
          </button>

          <p v-if="error" class="field-error" role="alert">{{ error }}</p>
        </article>

        <!-- Quiz questions -->
        <article v-if="quiz" class="content-panel quiz-questions">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Quiz · {{ quiz.kind }} · {{ quiz.status }}</p>
              <h2>{{ quiz.questions.length }} questions</h2>
            </div>
            <span class="status-pill status-pill--indigo">{{ quizProgress }} % répondu</span>
          </div>

          <div class="progress-inline"><i :style="{ width: quizProgress + '%' }"></i></div>

          <div class="questions-list">
            <div v-for="(q, idx) in quiz.questions" :key="q.id" class="question-card">
              <div class="q-head">
                <span class="q-index">{{ idx + 1 }}</span>
                <div class="q-meta">
                  <strong>{{ qTitle(q) }}</strong>
                  <small>{{ q.type }} · {{ q.points }} pt</small>
                </div>
              </div>
              <!-- MCQ options -->
              <div v-if="qOptions(q).length" class="q-options">
                <label v-for="opt in qOptions(q)" :key="opt" class="q-option">
                  <input
                    type="radio"
                    :name="`q-${q.id}`"
                    :value="opt"
                    :checked="answers[q.id] === opt"
                    :disabled="quiz.status==='completed'"
                    @change="answers[q.id]=opt"
                  />
                  <span>{{ opt }}</span>
                </label>
              </div>
              <!-- true/false -->
              <div v-else-if="q.type==='true_false'" class="q-options">
                <label class="q-option"><input type="radio" :name="`q-${q.id}`" value="true" :checked="answers[q.id]==='true'" :disabled="quiz.status==='completed'" @change="answers[q.id]='true'" /><span>Vrai</span></label>
                <label class="q-option"><input type="radio" :name="`q-${q.id}`" value="false" :checked="answers[q.id]==='false'" :disabled="quiz.status==='completed'" @change="answers[q.id]='false'" /><span>Faux</span></label>
              </div>
              <!-- open -->
              <textarea
                v-else
                :value="(answers[q.id] as string) ?? ''"
                :disabled="quiz.status==='completed'"
                rows="2"
                placeholder="Votre réponse…"
                @input="answers[q.id]=($event.target as HTMLTextAreaElement).value"
              />
              <p v-if="quiz.status==='completed' && q.answer != null" class="q-answer"><Eye :size="12" aria-hidden="true" /> Correction : {{ String(q.answer) }}</p>
            </div>
          </div>

          <div v-if="quiz.status!=='completed'" class="quiz-actions">
            <button type="button" class="primary-action" :disabled="submitting" @click="submitQuiz">
              <Loader2 v-if="submitting" :size="16" class="spin" />
              <Send v-else :size="16" aria-hidden="true" />
              {{ submitting ? "Envoi…" : "Envoyer mes réponses" }}
            </button>
          </div>

          <div v-if="report != null || quiz.score != null" class="report-box">
            <CheckCircle2 :size="18" aria-hidden="true" />
            <div>
              <strong>Score : {{ (report as Record<string, unknown> | null)?.score ?? quiz.score ?? "—" }} / {{ quiz.questions.length }}</strong>
              <p v-if="(report as Record<string, unknown> | null)?.feedback" class="report-feedback">{{ (report as Record<string, unknown>).feedback as string }}</p>
              <pre v-else-if="report" class="report-json">{{ JSON.stringify(report, null, 2) }}</pre>
            </div>
          </div>
        </article>

        <!-- Exam import section -->
        <article class="content-panel import-panel-quiz">
          <div class="panel-heading" style="margin-bottom:12px;">
            <div><p class="eyebrow">Épreuve</p><h2 style="font-size:18px;">Importer une épreuve</h2></div>
            <button type="button" class="secondary-action" style="min-height:32px;padding:6px 10px;font-size:12px;" @click="showImport = !showImport">
              <Upload :size="14" aria-hidden="true" /> {{ showImport ? "Fermer" : "Importer" }}
            </button>
          </div>
          <div v-if="showImport" class="import-form">
            <label class="field-label" for="exam-paths">Chemins des fichiers (un par ligne ou séparés par une virgule)</label>
            <textarea id="exam-paths" v-model="examPathsInput" rows="2" placeholder="/chemin/vers/epreuve.pdf"></textarea>
            <div class="import-actions">
              <button type="button" class="secondary-action" :disabled="importingExam" @click="doImportExam">
                <Loader2 v-if="importingExam" :size="14" class="spin" />
                <FileSearch v-else :size="14" aria-hidden="true" />
                {{ importingExam ? "Import…" : "Importer le texte" }}
              </button>
            </div>
            <div v-if="examText" class="exam-text-box">
              <div class="exam-text-head">
                <strong>Texte extrait ({{ examText.length }} caractères)</strong>
                <button type="button" class="primary-action" style="min-height:32px;padding:6px 10px;font-size:12px;" :disabled="analyzingExam" @click="doAnalyze">
                  <Loader2 v-if="analyzingExam" :size="14" class="spin" />
                  <Send v-else :size="14" aria-hidden="true" />
                  {{ analyzingExam ? "Analyse…" : "Analyser" }}
                </button>
              </div>
              <textarea v-model="examText" rows="6" style="margin-top:8px;"></textarea>
            </div>
            <div v-if="analyzedQuestions.length" class="analyzed-list">
              <p class="eyebrow">{{ analyzedQuestions.length }} questions extraites</p>
              <pre class="analyzed-json">{{ JSON.stringify(analyzedQuestions, null, 2) }}</pre>
            </div>
          </div>
        </article>
      </div>

      <aside class="quiz-aside">
        <article class="content-panel">
          <p class="eyebrow">{{ t('quiz.trust') }}</p>
          <h2>{{ t('quiz.citation') }}</h2>
          <p>{{ t('quiz.citationCopy') }}</p>
        </article>
        <article v-if="quiz" class="content-panel ready-card">
          <p class="eyebrow">{{ t('quiz.ready') }}</p>
          <h2>{{ t('quiz.readyCopy') }}</h2>
          <p>{{ t('quiz.connect') }}</p>
          <div v-if="quiz" class="ready-meta">
            <span class="status-pill status-pill--slate">{{ quiz.kind }}</span>
            <span class="status-pill" :class="quiz.status==='completed' ? 'status-pill--green' : 'status-pill--orange'">{{ quiz.status }}</span>
          </div>
        </article>
        <article v-else class="content-panel ready-card muted">
          <p class="eyebrow">{{ t('quiz.ready') }}</p>
          <h2>En attente de génération</h2>
          <p>Choisissez une intention et lancez la préparation pour afficher le quiz ici.</p>
        </article>
        <article v-if="report" class="content-panel score-card">
          <p class="eyebrow">Résultat</p>
          <h2><CheckCircle2 :size="18" aria-hidden="true" /> Score enregistré</h2>
          <p>La progression a été mise à jour. Consultez l'onglet Progression pour voir l'impact sur votre maîtrise.</p>
        </article>
      </aside>
    </section>
  </section>
</template>

<style scoped>
.quiz-main { display: grid; gap: 16px; }
.field-error { color: #b42318; font-size: 12px; font-weight: 600; margin: 8px 0 0; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
.duration-row { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
.time-chip { margin-left: auto; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 999px; background: var(--indigo-soft); color: var(--indigo-deep); }
.quiz-questions { padding: 25px; display: grid; gap: 16px; }
.progress-inline { height: 6px; border-radius: 99px; background: #e7e8f7; overflow: hidden; }
.progress-inline i { display: block; height: 100%; background: linear-gradient(90deg, var(--indigo), #8b83ff); transition: width .3s var(--ease-out); }
.questions-list { display: grid; gap: 12px; }
.question-card { padding: 16px; border: 1px solid var(--line); border-radius: 14px; background: #fff; display: grid; gap: 12px; }
.q-head { display: flex; gap: 12px; align-items: flex-start; }
.q-index { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 50%; background: var(--indigo-soft); color: var(--indigo-deep); font-weight: 800; font-size: 13px; flex: 0 0 auto; }
.q-meta { display: grid; gap: 3px; min-width: 0; }
.q-meta strong { font-size: 14px; line-height: 1.4; white-space: pre-wrap; }
.q-meta small { color: var(--muted); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.q-options { display: grid; gap: 8px; }
.q-option { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--line-soft); border-radius: 10px; background: #fff; cursor: pointer; font-size: 13px; }
.q-option:hover { border-color: #cbd1ff; background: #f8f8ff; }
.q-option input { accent-color: var(--indigo); }
.q-answer { margin: 0; font-size: 12.5px; color: var(--green); font-weight: 600; line-height: 1.4; }
.quiz-actions { display: flex; gap: 8px; }
.report-box { display: flex; gap: 10px; padding: 14px; border: 1px solid #b9e9d0; border-radius: 12px; background: var(--green-soft); }
.report-box strong { font-size: 14px; }
.report-feedback { margin: 6px 0 0; font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
.report-json, .analyzed-json { margin: 6px 0 0; font-size: 11.5px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid var(--line-soft); border-radius: 8px; padding: 10px; max-height: 200px; overflow: auto; }
.import-panel-quiz { padding: 20px; }
.import-form { display: grid; gap: 10px; }
.import-actions { display: flex; gap: 8px; }
.exam-text-box { padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: #f8f9ff; }
.exam-text-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.analyzed-list { margin-top: 8px; }
.ready-card.muted { opacity: .85; }
.ready-meta { display: flex; gap: 6px; margin-top: 10px; }
.score-card { border-color: #b9e9d0; background: var(--green-soft); }
@media (max-width: 980px) { .quiz-layout { grid-template-columns: 1fr; } }
</style>
