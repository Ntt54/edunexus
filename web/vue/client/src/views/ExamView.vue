<!-- 012 US2 (FR-006) — Épreuve blanche BEPC/probatoire/bac : chrono, verrouillage, détail /20 par compétence. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ArrowRight, GraduationCap, Loader2, Lock, RotateCcw, Timer } from "lucide-vue-next";
import { usePreferences } from "@/stores/preferences";
import { tutorApi, invalidateSubjectCaches } from "@/services/api";

const { locale, activeSubjectId, activeLearnerId } = usePreferences();

// ── 012 · Messages inline fr/en ────────────────────────────────────
const msg = computed(() => locale.value === "fr" ? {
  kicker: "BEPC · Probatoire · Bac",
  title: "Examen blanc",
  copy: "Une épreuve chronométrée notée sur 20, avec le détail par compétence. Programmes camerounais (squelettes validés).",
  blueprint: "Programme",
  blueprints: [
    { key: "cm/bepc", label: "BEPC — Troisième" },
    { key: "cm/premiere-a", label: "Probatoire A — Première littéraire" },
    { key: "cm/premiere-c", label: "Probatoire C — Première scientifique" },
    { key: "cm/premiere-d", label: "Probatoire D — Première scientifique" },
    { key: "cm/terminale-a", label: "Bac A — Terminale littéraire" },
    { key: "cm/terminale-c", label: "Bac C — Terminale scientifique" },
    { key: "cm/terminale-d", label: "Bac D — Terminale scientifique" },
  ] as Array<{ key: string; label: string }>,
  duration: "Durée (minutes)",
  size: "Questions",
  start: "Démarrer l’épreuve",
  starting: "Préparation…",
  remaining: "Temps restant",
  locked: "Temps écoulé — épreuve verrouillée. Soumettez pour corriger.",
  interrupted: "Épreuve interrompue — reprenez avec le temps restant.",
  submit: "Soumettre et corriger",
  submitting: "Correction…",
  retry: "Réessayer",
  error: "Création impossible — réessayez.",
  noSpace: "Sélectionnez une matière pour passer une épreuve blanche.",
  score: "Note sur 20",
  detail: "Détail par compétence",
  competence: "Compétence",
  trueLabel: "Vrai",
  falseLabel: "Faux",
  yourAnswer: "Votre réponse…",
  lockedDone: "Épreuve corrigée",
} : {
  kicker: "BEPC · Probatoire · Bac",
  title: "Mock exam",
  copy: "A timed exam scored out of 20, with per-skill detail. Cameroonian syllabi (validated skeletons).",
  blueprint: "Syllabus",
  blueprints: [
    { key: "cm/bepc", label: "BEPC — Form 4" },
    { key: "cm/premiere-a", label: "Probatoire A — Lower sixth (arts)" },
    { key: "cm/premiere-c", label: "Probatoire C — Lower sixth (science)" },
    { key: "cm/premiere-d", label: "Probatoire D — Lower sixth (science)" },
    { key: "cm/terminale-a", label: "Bac A — Upper sixth (arts)" },
    { key: "cm/terminale-c", label: "Bac C — Upper sixth (science)" },
    { key: "cm/terminale-d", label: "Bac D — Upper sixth (science)" },
  ] as Array<{ key: string; label: string }>,
  duration: "Duration (minutes)",
  size: "Questions",
  start: "Start the exam",
  starting: "Preparing…",
  remaining: "Time left",
  locked: "Time over — exam locked. Submit to grade.",
  interrupted: "Exam interrupted — resume with the remaining time.",
  submit: "Submit and grade",
  submitting: "Grading…",
  retry: "Retry",
  error: "Could not create — please retry.",
  noSpace: "Select a subject to take a mock exam.",
  score: "Score out of 20",
  detail: "Per-skill detail",
  competence: "Skill",
  trueLabel: "True",
  falseLabel: "False",
  yourAnswer: "Your answer…",
  lockedDone: "Exam graded",
});

interface ExamQuestion { id: string; type: string; payload: Record<string, unknown>; points: number; answer?: unknown; concept_id?: string | null; }
interface CompetenceDetail { competence: string; score_20: number; mention: string; }
interface ExamData {
  id: string; subject_id: string; kind: string; status: string;
  blueprint: string | null; duree_min: number; time_limit_s?: number | null;
  score_20: number | null; detail_competences: CompetenceDetail[]; mention?: string | null;
  statut: string; locked: boolean; remaining_s: number;
  questions: ExamQuestion[];
  report?: { score_20?: number; detail_competences?: CompetenceDetail[]; mention?: string } | null;
}

const blueprint = ref("cm/bepc");
const dureeMin = ref(120);
const size = ref(5);
const exam = ref<ExamData | null>(null);
const loading = ref(false);
const submitting = ref(false);
const error = ref<string | null>(null);
const answers = ref<Record<string, unknown>>({});
const remaining = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;
let gen = 0;

function currentCouple(): { sid: string; lid: string } {
  const sid = (activeSubjectId.value || localStorage.getItem("edunexus.space") || localStorage.getItem("edunexus:subject") || "").trim();
  const lid = (activeLearnerId.value || localStorage.getItem("edunexus.learner") || localStorage.getItem("edunexus:learner") || "").trim();
  return { sid, lid };
}

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null; }
}

function startTimer() {
  stopTimer();
  if (!exam.value || exam.value.statut === "completed") return;
  remaining.value = exam.value.remaining_s;
  timer = setInterval(() => {
    if (remaining.value > 0) remaining.value -= 1;
    if (remaining.value <= 0 && exam.value && exam.value.statut !== "completed") {
      exam.value.locked = true;
      stopTimer();
    }
  }, 1000);
}

function fmtClock(totalS: number): string {
  const s = Math.max(0, totalS);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

async function createExam() {
  const { sid } = currentCouple();
  if (!sid) { error.value = msg.value.noSpace; return; }
  const myGen = ++gen;
  loading.value = true; error.value = null; exam.value = null; answers.value = {};
  stopTimer();
  try {
    const data = await tutorApi.createBlueprintExam({
      blueprint: blueprint.value,
      duree_min: Math.max(1, Math.floor(dureeMin.value || 0)),
      subject_id: sid,
      size: Math.max(1, Math.min(20, Math.floor(size.value || 0))),
    }) as unknown as ExamData;
    if (myGen !== gen) return;
    exam.value = data;
    for (const q of data.questions ?? []) {
      if (q.type === "matching") {
        const pairs = ((q.payload as Record<string, unknown>).pairs as Array<unknown>) ?? [];
        answers.value[q.id] = pairs.map((_, i) => i);
      } else answers.value[q.id] = "";
    }
    startTimer();
  } catch (e) {
    if (myGen !== gen) return;
    error.value = e instanceof Error ? e.message : msg.value.error;
  } finally {
    if (myGen === gen) loading.value = false;
  }
}

async function submitExam() {
  if (!exam.value || submitting.value) return;
  submitting.value = true; error.value = null;
  try {
    await tutorApi.submitQuiz(exam.value.id, answers.value, false);
    const updated = await tutorApi.getQuiz(exam.value.id) as unknown as ExamData;
    // Le report porte score_20 / détail / mention (012 US2, FR-006).
    const report = (updated.report ?? {}) as { score_20?: number; detail_competences?: CompetenceDetail[]; mention?: string };
    exam.value = {
      ...updated,
      score_20: report.score_20 ?? updated.score_20 ?? null,
      detail_competences: report.detail_competences ?? updated.detail_competences ?? [],
      mention: report.mention ?? updated.mention ?? null,
    };
    stopTimer();
  } catch (e) {
    error.value = e instanceof Error ? e.message : msg.value.error;
  } finally {
    submitting.value = false;
  }
}

function qTitle(q: ExamQuestion): string {
  const p = q.payload as Record<string, unknown>;
  return (p.question as string) ?? (p.statement as string) ?? `Question (${q.type})`;
}
function qOptions(q: ExamQuestion): string[] {
  const p = q.payload as Record<string, unknown>;
  const opts = p.options ?? p.choices;
  return Array.isArray(opts) ? (opts as string[]) : [];
}
function qPairs(q: ExamQuestion): Array<{ left: string; right: string }> {
  const p = q.payload as Record<string, unknown>;
  const pairs = p.pairs;
  return Array.isArray(pairs) ? (pairs as Array<{ left: string; right: string }>) : [];
}
function setMatching(qid: string, row: number, val: number) {
  const cur = Array.isArray(answers.value[qid]) ? [...(answers.value[qid] as number[])] : [];
  cur[row] = val;
  answers.value[qid] = cur;
}

const completed = computed(() => exam.value?.statut === "completed");
const detail = computed(() => exam.value?.detail_competences ?? []);

function onCoupleChange() {
  invalidateSubjectCaches();
  stopTimer();
  exam.value = null;
  answers.value = {};
  error.value = null;
}

watch(() => activeSubjectId.value, () => { onCoupleChange(); });
watch(() => activeLearnerId.value, () => { onCoupleChange(); });

onMounted(() => {
  window.addEventListener("edunexus:subjectChange", onCoupleChange as EventListener);
  window.addEventListener("subjectChange", onCoupleChange as EventListener);
  window.addEventListener("edunexus:learnerChange", onCoupleChange as EventListener);
  window.addEventListener("learnerChange", onCoupleChange as EventListener);
});
onUnmounted(() => {
  stopTimer();
  window.removeEventListener("edunexus:subjectChange", onCoupleChange as EventListener);
  window.removeEventListener("subjectChange", onCoupleChange as EventListener);
  window.removeEventListener("edunexus:learnerChange", onCoupleChange as EventListener);
  window.removeEventListener("learnerChange", onCoupleChange as EventListener);
});
</script>

<template>
  <section class="page exam-page reading-surface">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ msg.kicker }}</p>
        <h1>{{ msg.title }}</h1>
        <p>{{ msg.copy }}</p>
      </div>
      <div class="subject-token"><GraduationCap :size="18" aria-hidden="true" /><span>{{ msg.title }}</span></div>
    </header>

    <article class="content-panel exam-setup">
      <div class="setup-grid">
        <label class="setup-field">
          <span>{{ msg.blueprint }}</span>
          <select v-model="blueprint">
            <option v-for="b in msg.blueprints" :key="b.key" :value="b.key">{{ b.label }}</option>
          </select>
        </label>
        <label class="setup-field">
          <span>{{ msg.duration }}</span>
          <input v-model.number="dureeMin" type="number" min="1" max="480" />
        </label>
        <label class="setup-field">
          <span>{{ msg.size }}</span>
          <input v-model.number="size" type="number" min="1" max="20" />
        </label>
      </div>
      <button type="button" class="primary-action" :disabled="loading" @click="createExam">
        <Loader2 v-if="loading" :size="17" class="spin" aria-hidden="true" />
        <ArrowRight v-else :size="17" aria-hidden="true" />
        {{ loading ? msg.starting : msg.start }}
      </button>
      <p v-if="error" class="field-error" role="alert">{{ error }}
        <button type="button" class="secondary-action" @click="createExam"><RotateCcw :size="14" aria-hidden="true" /> {{ msg.retry }}</button>
      </p>
    </article>

    <article v-if="exam" class="content-panel exam-session">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ exam.blueprint }} · {{ exam.statut }}</p>
          <h2>{{ exam.questions.length }} questions</h2>
        </div>
        <span class="time-chip" :class="{ 'time-chip--over': exam.locked && !completed }" role="timer">
          <Timer :size="14" aria-hidden="true" /> {{ msg.remaining }} : {{ fmtClock(remaining) }}
        </span>
      </div>

      <div v-if="exam.locked && !completed" class="stale-banner" role="alert">
        <Lock :size="18" aria-hidden="true" />
        <p>{{ msg.locked }}</p>
      </div>
      <div v-if="exam.statut === 'interrompue' && !completed" class="stale-banner" role="status">
        <Timer :size="18" aria-hidden="true" />
        <p>{{ msg.interrupted }}</p>
      </div>

      <div v-if="completed" class="score-banner" role="status">
        <strong>{{ msg.score }} : {{ exam.score_20 }} / 20{{ exam.mention ? ` — ${exam.mention}` : "" }}</strong>
        <span class="status-pill status-pill--indigo">{{ msg.lockedDone }}</span>
      </div>

      <div class="questions-list">
        <div v-for="(q, idx) in exam.questions" :key="q.id" class="question-card">
          <div class="q-head">
            <span class="q-index">{{ idx + 1 }}</span>
            <div class="q-meta">
              <strong>{{ qTitle(q) }}</strong>
              <small>{{ q.type }} · {{ q.points }} pt</small>
            </div>
          </div>
          <div v-if="q.type === 'mcq' && qOptions(q).length" class="q-options">
            <label v-for="(opt, oi) in qOptions(q)" :key="oi" class="q-option">
              <input
                type="radio"
                :name="`ex-${q.id}`"
                :checked="(answers[q.id] as number) === oi"
                :disabled="completed"
                @change="answers[q.id] = oi"
              />
              <span>{{ opt }}</span>
            </label>
          </div>
          <div v-else-if="q.type === 'true_false'" class="q-options">
            <label class="q-option"><input type="radio" :name="`ex-${q.id}`" :checked="(answers[q.id] as boolean) === true" :disabled="completed" @change="answers[q.id] = true" /><span>{{ msg.trueLabel }}</span></label>
            <label class="q-option"><input type="radio" :name="`ex-${q.id}`" :checked="(answers[q.id] as boolean) === false" :disabled="completed" @change="answers[q.id] = false" /><span>{{ msg.falseLabel }}</span></label>
          </div>
          <div v-else-if="q.type === 'matching' && qPairs(q).length" class="q-matching">
            <label v-for="(pair, ri) in qPairs(q)" :key="ri" class="match-row">
              <span>{{ pair.left }}</span>
              <select :value="(answers[q.id] as number[])?.[ri] ?? ri" :disabled="completed" @change="setMatching(q.id, ri, Number(($event.target as HTMLSelectElement).value))">
                <option v-for="(r, rj) in qPairs(q)" :key="rj" :value="rj">{{ r.right }}</option>
              </select>
            </label>
          </div>
          <textarea
            v-else
            :value="(answers[q.id] as string) ?? ''"
            :disabled="completed"
            rows="2"
            :placeholder="msg.yourAnswer"
            @input="answers[q.id] = ($event.target as HTMLTextAreaElement).value"
          />
        </div>
      </div>

      <button v-if="!completed" type="button" class="primary-action" :disabled="submitting" @click="submitExam">
        <Loader2 v-if="submitting" :size="17" class="spin" aria-hidden="true" />
        {{ submitting ? msg.submitting : msg.submit }}
      </button>

      <div v-if="completed && detail.length" class="detail-table-wrap">
        <h3>{{ msg.detail }}</h3>
        <table class="detail-table">
          <thead>
            <tr><th>{{ msg.competence }}</th><th>/ 20</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="d in detail" :key="d.competence">
              <td>{{ d.competence }}</td>
              <td><strong>{{ d.score_20 }}</strong></td>
              <td>{{ d.mention }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>
  </section>
</template>

<style scoped>
.exam-page { display: grid; gap: 16px; }
.exam-setup { display: grid; gap: 12px; }
.setup-grid { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 10px; }
.setup-field { display: grid; gap: 4px; font-size: 12px; font-weight: 700; color: var(--muted); }
.exam-session { display: grid; gap: 12px; }
.time-chip { display: inline-flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; font-weight: 800; border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; }
.time-chip--over { border-color: #b42318; color: #b42318; }
.stale-banner { display: flex; gap: 10px; align-items: flex-start; padding: 14px 16px; border: 1px solid #f0c98a; border-radius: 12px; background: #fff8ec; }
.stale-banner p { margin: 0; font-size: 13.5px; line-height: 1.5; }
.score-banner { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: 12px; background: var(--indigo-soft); }
.questions-list { display: grid; gap: 10px; }
.question-card { border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; background: #fff; display: grid; gap: 10px; }
.q-head { display: flex; gap: 10px; align-items: flex-start; }
.q-index { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--indigo-soft); font-weight: 800; font-size: 13px; flex: 0 0 auto; }
.q-meta { display: grid; gap: 2px; }
.q-meta small { color: var(--muted); font-size: 12px; }
.q-options { display: grid; gap: 6px; }
.q-option { display: flex; gap: 8px; align-items: center; font-size: 13.5px; }
.q-matching { display: grid; gap: 6px; }
.match-row { display: flex; gap: 8px; align-items: center; justify-content: space-between; font-size: 13px; }
.detail-table-wrap { display: grid; gap: 8px; }
.detail-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.detail-table th, .detail-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.field-error { color: #b42318; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
@media (max-width: 720px) { .setup-grid { grid-template-columns: 1fr; } }
</style>
