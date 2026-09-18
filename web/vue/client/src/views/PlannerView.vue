<!-- 012 US3 (FR-011) — Planning semestre ECTS : charge par UE, créneaux hebdo révision/simulation, règle 150 %. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { CalendarDays, Loader2, Plus, RotateCcw, ShieldCheck, Trash2 } from "lucide-vue-next";
import { usePreferences } from "@/stores/preferences";
import { tutorApi, invalidateSubjectCaches } from "@/services/api";

const { locale, activeSubjectId, activeLearnerId } = usePreferences();

// ── 012 · Messages inline fr/en ────────────────────────────────────
const msg = computed(() => locale.value === "fr" ? {
  kicker: "Niveau universitaire",
  title: "Planning semestre",
  copy: "Votre charge par UE (1 ECTS = 25-30 h) répartie en créneaux hebdo. Aucune semaine ne dépasse 150 % de la moyenne.",
  ues: "Unités d’enseignement",
  subject: "Matière",
  hours: "Heures",
  ects: "ECTS",
  addUe: "Ajouter une UE",
  exams: "Épreuves",
  examDate: "Date",
  addExam: "Ajouter une épreuve",
  titleLabel: "Titre du plan",
  titlePh: "Semestre 1…",
  startLabel: "Début",
  capLabel: "Plafond hebdo (h, optionnel)",
  plan: "Planifier",
  planning: "Planification…",
  retry: "Réessayer",
  error: "Planification impossible — allégez la charge ou le plafond.",
  noSpace: "Sélectionnez une matière pour rattacher le plan.",
  totalHours: "Charge totale",
  hoursUnit: "h",
  weeklyMean: "Moyenne hebdo",
  weeklyCap: "Plafond 150 %",
  minUnit: "min",
  week: "Semaine",
  empty: "Renseignez vos UE puis lancez la planification.",
  emptyCopy: "Le plan apparaît ici en moins d’une minute, sans semaine en surcharge.",
  revision: "révision",
  simulation: "simulation",
  course: "cours",
  remove: "Retirer",
} : {
  kicker: "Higher education",
  title: "Semester plan",
  copy: "Your workload per course (1 ECTS = 25-30 h) spread over weekly slots. No week exceeds 150% of the average.",
  ues: "Course units",
  subject: "Subject",
  hours: "Hours",
  ects: "ECTS",
  addUe: "Add a unit",
  exams: "Exams",
  examDate: "Date",
  addExam: "Add an exam",
  titleLabel: "Plan title",
  titlePh: "Semester 1…",
  startLabel: "Start",
  capLabel: "Weekly cap (h, optional)",
  plan: "Plan",
  planning: "Planning…",
  retry: "Retry",
  error: "Planning impossible — reduce the load or the cap.",
  noSpace: "Select a subject to attach the plan.",
  totalHours: "Total load",
  hoursUnit: "h",
  weeklyMean: "Weekly average",
  weeklyCap: "150% ceiling",
  minUnit: "min",
  week: "Week",
  empty: "Fill in your units then run the planner.",
  emptyCopy: "The plan shows up here in under a minute, with no overloaded week.",
  revision: "review",
  simulation: "mock",
  course: "course",
  remove: "Remove",
});

interface UeRow { subject_id: string; heures: string; ects: string; }
interface ExamRow { subject_id: string; date: string; }
interface PlanSlot { subject_id: string; minutes: number; kind: string; }
interface PlanWeek { semaine: number; minutes_total: number; creneaux: PlanSlot[]; }
interface SemesterPlan {
  title: string; semaines: PlanWeek[]; heures_totales: number;
  moyenne_semaine_min: number; plafond_semaine_min: number;
}
interface SubjectOption { id: string; name: string; }

const subjects = ref<SubjectOption[]>([]);
const ues = ref<UeRow[]>([{ subject_id: "", heures: "120", ects: "" }]);
const exams = ref<ExamRow[]>([]);
const planTitle = ref("");
const startDate = ref("");
const capHours = ref("");
const plan = ref<SemesterPlan | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
let gen = 0;

function currentCouple(): { sid: string; lid: string } {
  const sid = (activeSubjectId.value || localStorage.getItem("edunexus.space") || localStorage.getItem("edunexus:subject") || "").trim();
  const lid = (activeLearnerId.value || localStorage.getItem("edunexus.learner") || localStorage.getItem("edunexus:learner") || "").trim();
  return { sid, lid };
}

async function loadSubjects() {
  try {
    const res = await tutorApi.getSubjects() as unknown as { subjects?: SubjectOption[] };
    subjects.value = res.subjects ?? [];
  } catch { subjects.value = []; }
}

function onCoupleChange() {
  invalidateSubjectCaches();
}

function addUe() { ues.value = [...ues.value, { subject_id: "", heures: "", ects: "" }]; }
function removeUe(i: number) { ues.value = ues.value.filter((_, k) => k !== i); }
function addExam() { exams.value = [...exams.value, { subject_id: "", date: "" }]; }
function removeExam(i: number) { exams.value = exams.value.filter((_, k) => k !== i); }

function kindLabel(kind: string): string {
  if (kind === "simulation") return msg.value.simulation;
  if (kind === "cours") return msg.value.course;
  return msg.value.revision;
}

async function submit() {
  const myGen = ++gen;
  loading.value = true; error.value = null; plan.value = null;
  try {
    const payloadUes = ues.value
      .map((u) => ({
        subject_id: u.subject_id.trim() || currentCouple().sid,
        heures: u.heures.trim() ? Number(u.heures) : undefined,
        ects: u.ects.trim() ? Number(u.ects) : undefined,
      }))
      .filter((u) => u.subject_id);
    const payloadExams = exams.value
      .filter((e) => e.date.trim() && (e.subject_id.trim() || currentCouple().sid))
      .map((e) => ({ date: e.date.trim(), subject_id: e.subject_id.trim() || currentCouple().sid }));
    const payload: Record<string, unknown> = { ues: payloadUes, epreuves: payloadExams };
    if (planTitle.value.trim()) payload.title = planTitle.value.trim();
    if (startDate.value) payload.start_date = startDate.value;
    if (capHours.value.trim()) payload.max_heures_semaine = Number(capHours.value);
    const res = await tutorApi.planSemester(payload as {
      ues: Array<{ subject_id: string; heures: number }>;
      epreuves?: Array<{ date: string; subject_id: string }>;
    }) as unknown as SemesterPlan;
    if (myGen !== gen) return;
    plan.value = res;
    invalidateSubjectCaches();
  } catch {
    if (myGen !== gen) return;
    error.value = msg.value.error;
  } finally {
    if (myGen === gen) loading.value = false;
  }
}

watch(() => activeSubjectId.value, () => { onCoupleChange(); });
watch(() => activeLearnerId.value, () => { onCoupleChange(); });

onMounted(async () => {
  await loadSubjects();
  window.addEventListener("edunexus:subjectChange", onCoupleChange as EventListener);
  window.addEventListener("subjectChange", onCoupleChange as EventListener);
  window.addEventListener("edunexus:learnerChange", onCoupleChange as EventListener);
  window.addEventListener("learnerChange", onCoupleChange as EventListener);
});
onUnmounted(() => {
  window.removeEventListener("edunexus:subjectChange", onCoupleChange as EventListener);
  window.removeEventListener("subjectChange", onCoupleChange as EventListener);
  window.removeEventListener("edunexus:learnerChange", onCoupleChange as EventListener);
  window.removeEventListener("learnerChange", onCoupleChange as EventListener);
});
</script>

<template>
  <section class="page planner-page reading-surface">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ msg.kicker }}</p>
        <h1>{{ msg.title }}</h1>
        <p>{{ msg.copy }}</p>
      </div>
      <div class="subject-token"><CalendarDays :size="18" aria-hidden="true" /><span>{{ msg.title }}</span></div>
    </header>

    <div class="planner-grid">
      <form class="content-panel planner-form" @submit.prevent="submit">
        <h2>{{ msg.ues }}</h2>
        <div v-for="(u, i) in ues" :key="i" class="ue-row">
          <label>
            <span>{{ msg.subject }}</span>
            <select v-model="u.subject_id">
              <option value="">{{ msg.subject }}…</option>
              <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </label>
          <label>
            <span>{{ msg.hours }}</span>
            <input v-model="u.heures" type="number" min="0" step="1" inputmode="numeric" />
          </label>
          <label>
            <span>{{ msg.ects }}</span>
            <input v-model="u.ects" type="number" min="0" step="0.5" inputmode="decimal" />
          </label>
          <button type="button" class="icon-action" :aria-label="msg.remove" :title="msg.remove" @click="removeUe(i)">
            <Trash2 :size="14" aria-hidden="true" />
          </button>
        </div>
        <button type="button" class="secondary-action" @click="addUe">
          <Plus :size="14" aria-hidden="true" /> {{ msg.addUe }}
        </button>

        <h2>{{ msg.exams }}</h2>
        <div v-for="(e, i) in exams" :key="'e' + i" class="ue-row">
          <label>
            <span>{{ msg.subject }}</span>
            <select v-model="e.subject_id">
              <option value="">{{ msg.subject }}…</option>
              <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </label>
          <label>
            <span>{{ msg.examDate }}</span>
            <input v-model="e.date" type="date" />
          </label>
          <button type="button" class="icon-action" :aria-label="msg.remove" :title="msg.remove" @click="removeExam(i)">
            <Trash2 :size="14" aria-hidden="true" />
          </button>
        </div>
        <button type="button" class="secondary-action" @click="addExam">
          <Plus :size="14" aria-hidden="true" /> {{ msg.addExam }}
        </button>

        <div class="ue-row">
          <label>
            <span>{{ msg.titleLabel }}</span>
            <input v-model="planTitle" type="text" :placeholder="msg.titlePh" />
          </label>
          <label>
            <span>{{ msg.startLabel }}</span>
            <input v-model="startDate" type="date" />
          </label>
          <label>
            <span>{{ msg.capLabel }}</span>
            <input v-model="capHours" type="number" min="0" step="1" inputmode="numeric" />
          </label>
        </div>

        <div class="form-actions">
          <button type="submit" class="primary-action" :disabled="loading">
            <Loader2 v-if="loading" :size="14" class="spin" aria-hidden="true" />
            {{ loading ? msg.planning : msg.plan }}
          </button>
        </div>
        <p v-if="error" class="field-error" role="alert">{{ error }}
          <button type="button" class="secondary-action" @click="submit"><RotateCcw :size="14" aria-hidden="true" /> {{ msg.retry }}</button>
        </p>
        <p v-if="!currentCouple().sid" class="field-hint">{{ msg.noSpace }}</p>
      </form>

      <section class="content-panel planner-result" aria-live="polite">
        <div v-if="plan" class="plan-summary">
          <div class="plan-stat"><strong>{{ plan.heures_totales }} {{ msg.hoursUnit }}</strong><small>{{ msg.totalHours }}</small></div>
          <div class="plan-stat"><strong>{{ plan.moyenne_semaine_min }} {{ msg.minUnit }}</strong><small>{{ msg.weeklyMean }}</small></div>
          <div class="plan-stat plan-ok"><ShieldCheck :size="16" aria-hidden="true" /><strong>{{ plan.plafond_semaine_min }} {{ msg.minUnit }}</strong><small>{{ msg.weeklyCap }}</small></div>
        </div>
        <div v-if="plan" class="plan-weeks">
          <article v-for="w in plan.semaines" :key="w.semaine" class="plan-week">
            <header><strong>{{ msg.week }} {{ w.semaine }}</strong><span>{{ w.minutes_total }} {{ msg.minUnit }}</span></header>
            <ul>
              <li v-for="(c, k) in w.creneaux" :key="k">
                <span class="slot-subject">{{ c.subject_id }}</span>
                <span class="status-pill" :class="c.kind === 'simulation' ? 'status-pill--orange' : 'status-pill--indigo'">{{ kindLabel(c.kind) }}</span>
                <span class="slot-min">{{ c.minutes }} {{ msg.minUnit }}</span>
              </li>
            </ul>
          </article>
        </div>
        <div v-else class="empty-panel">
          <CalendarDays :size="48" aria-hidden="true" />
          <h2>{{ msg.empty }}</h2>
          <p>{{ msg.emptyCopy }}</p>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.planner-page { display: grid; gap: 16px; }
.planner-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); align-items: start; }
@media (max-width: 1080px) { .planner-grid { grid-template-columns: 1fr; } }
.planner-form { display: grid; gap: 12px; padding: 18px; }
.planner-form h2 { margin: 6px 0 0; font-size: 14px; }
.ue-row { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.ue-row label { display: grid; gap: 4px; font-size: 12px; flex: 1; min-width: 120px; }
.ue-row input, .ue-row select { min-height: 36px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; background: #fff; color: inherit; }
.icon-action { display: inline-grid; place-items: center; min-width: 36px; min-height: 36px; border: 1px solid var(--line); border-radius: 8px; background: transparent; cursor: pointer; color: var(--muted); }
.icon-action:hover { color: #b42318; border-color: #b42318; }
.form-actions { display: flex; gap: 8px; }
.field-error { color: #b42318; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.field-hint { color: var(--muted); font-size: 12.5px; }
.planner-result { padding: 18px; display: grid; gap: 12px; }
.plan-summary { display: flex; gap: 12px; flex-wrap: wrap; }
.plan-stat { display: grid; gap: 2px; padding: 10px 14px; border: 1px solid var(--line); border-radius: 12px; background: #fff; min-width: 130px; }
.plan-stat small { color: var(--muted); font-size: 11.5px; }
.plan-ok { border-color: var(--green, #16a34a); }
.plan-weeks { display: grid; gap: 10px; }
.plan-week { border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; background: #fff; }
.plan-week header { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 6px; }
.plan-week ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.plan-week li { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.slot-subject { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; font-size: 11.5px; }
.slot-min { color: var(--muted); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
</style>
