<!-- EduNexus UI direction: Atelier de progression — les scores et lacunes deviennent des actions, jamais un jugement opaque. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowRight, CheckCircle2, CircleAlert, TrendingUp, Loader2, AlertTriangle, History, BookOpenCheck, Trophy } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";

const { state } = useLearningStore();
const { t } = usePreferences();

const loading = ref(false);
const error = ref<string | null>(null);

interface ProgressRow { concept: string; concept_id: string; score: number; label: string; }
interface GapRow { concept: string; concept_id: string; score: number; recent_failures: number; }
interface ErrorRow { concept_name: string; question_text: string; given_answer: string; correct_answer: string; error_type: string; created_at: string; }
interface DueRow { id: string; concept_id: string; question: string; answer: string; }

const progress = ref<ProgressRow[]>([]);
const gaps = ref<GapRow[]>([]);
const errors = ref<ErrorRow[]>([]);
const dueReviews = ref<DueRow[]>([]);

const subjectId = computed(() => state.data?.subject.id ?? "");

const data = computed(() => state.data);
const stateFor = (score: number) => score >= 75 ? [t('status.mastered'), "green"] : score >= 40 ? [t('status.current'), "indigo"] : [t('status.review'), "orange"];

const masteredCount = computed(() => progress.value.filter(p => p.score >= 75).length);
const toReviewCount = computed(() => progress.value.filter(p => p.score < 40).length);
const avgScore = computed(() => {
  if (!progress.value.length) return 0;
  return Math.round(progress.value.reduce((s, r) => s + r.score, 0) / progress.value.length);
});

async function resolveSubjectId(): Promise<string> {
  if (subjectId.value) return subjectId.value;
  const resp = await tutorApi.getSubjects();
  return resp.active_id || resp.subjects[0]?.id || "";
}

async function fetchAll() {
  loading.value = true; error.value = null;
  try {
    const sid = await resolveSubjectId();
    if (!sid) { error.value = "Aucun espace actif."; loading.value = false; return; }
    const [p, g, e, d] = await Promise.allSettled([
      tutorApi.getProgress(sid),
      tutorApi.getGaps(sid),
      tutorApi.getErrors(sid, "", 20),
      tutorApi.getReviewsDue(sid),
    ]);
    if (p.status === 'fulfilled') progress.value = (p.value as unknown as { progress: ProgressRow[] }).progress ?? [];
    else progress.value = (data.value?.concepts.map(c => ({ concept: c.name, concept_id: c.id, score: c.score, label: c.score >= 75 ? 'maîtrisé' : c.score >= 40 ? 'en cours' : 'à revoir' })) ?? []) as ProgressRow[];
    if (g.status === 'fulfilled') gaps.value = (g.value as unknown as { gaps: GapRow[] }).gaps ?? [];
    if (e.status === 'fulfilled') errors.value = (e.value as unknown as { errors: ErrorRow[] }).errors ?? [];
    if (d.status === 'fulfilled') dueReviews.value = ((d.value as unknown as { due: DueRow[] }).due ?? []) as DueRow[];
  } catch (e2) {
    error.value = e2 instanceof Error ? e2.message : "Chargement impossible";
  } finally { loading.value = false; }
}

async function gradeReview(id: string, success: boolean) {
  try {
    await tutorApi.gradeReview(id, success);
    dueReviews.value = dueReviews.value.filter(r => r.id !== id);
    // refresh gaps/progress lightly
    await fetchAll();
  } catch (e2) {
    error.value = e2 instanceof Error ? e2.message : "Correction impossible";
  }
}

onMounted(fetchAll);

const sortedProgress = computed(() => [...progress.value].sort((a,b) => b.score - a.score));

// Révision due pour une notion (timing affiché sur sa ligne), sinon rien.
function dueFor(conceptId: string): boolean {
  return dueReviews.value.some((r) => r.concept_id === conceptId);
}
</script>

<template>
  <section v-if="loading" class="page loading-state">
    <Loader2 :size="24" class="spin" aria-hidden="true" />
    <p>Chargement de la progression…</p>
  </section>

  <section v-else-if="error" class="page loading-state">
    <div class="empty-panel" style="display:grid;place-items:center;gap:12px;padding:32px;text-align:center;">
      <AlertTriangle :size="36" aria-hidden="true" />
      <p>{{ error }}</p>
      <button type="button" class="secondary-action" @click="fetchAll">Réessayer</button>
    </div>
  </section>

  <section v-else-if="data" class="page progress-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('progress.kicker') }}</p>
        <h1>{{ t('progress.title') }}</h1>
        <p>{{ t('progress.copy') }}</p>
      </div>
    </header>

    <section class="progress-summary">
      <article>
        <TrendingUp :size="22" aria-hidden="true" />
        <div>
          <span>{{ t('progress.path') }}</span>
          <strong>{{ data.path.progress }} %</strong>
          <small>{{ t('progress.steps', { count: data.path.steps.filter((item) => item.status === 'completed').length }) }} · moy. {{ avgScore }} %</small>
        </div>
      </article>
      <article>
        <CheckCircle2 :size="22" aria-hidden="true" />
        <div>
          <span>{{ t('progress.stable') }}</span>
          <strong>{{ masteredCount }}</strong>
          <small>{{ t('progress.stableReason') }}</small>
        </div>
      </article>
      <article>
        <CircleAlert :size="22" aria-hidden="true" />
        <div>
          <span>{{ t('progress.consolidate') }}</span>
          <strong>{{ toReviewCount }}</strong>
          <small>{{ t('progress.consolidateReason') }}</small>
        </div>
      </article>
    </section>

    <section class="content-panel concept-table">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('progress.byConcept') }}</p>
          <h2>{{ t('progress.map') }}</h2>
        </div>
        <RouterLink to="/exercices" class="text-link">{{ t('progress.exercise') }}</RouterLink>
      </div>

      <div v-if="!sortedProgress.length" class="lib-empty" style="padding:16px 0;">Aucune notion suivie. Génère un parcours ou crée une notion en Exercices.</div>

      <RouterLink v-for="row in sortedProgress" :key="row.concept_id" to="/exercices" class="concept-row" :aria-label="`${row.concept} — ${t('progress.exercise')}`">
        <div class="concept-label">
          <strong>{{ row.concept }}</strong>
          <span>{{ row.label }}<template v-if="dueFor(row.concept_id)"> · {{ t('progress.reviewDue') }}</template></span>
        </div>
        <div class="concept-meter">
          <span><i :style="{ width: `${Math.max(0, Math.min(100, row.score))}%` }"></i></span>
          <b>{{ Math.round(row.score) }} %</b>
        </div>
        <StatusPill :tone="stateFor(row.score)[1] as never">{{ stateFor(row.score)[0] }}</StatusPill>
        <span class="row-action" aria-hidden="true"><ArrowRight :size="18" /></span>
      </RouterLink>
    </section>

    <!-- Gaps -->
    <section v-if="gaps.length" class="content-panel gaps-panel">
      <div class="panel-heading">
        <div><p class="eyebrow">Lacunes</p><h2>À consolider</h2></div>
        <AlertTriangle :size="18" aria-hidden="true" />
      </div>
      <div class="gaps-list">
        <div v-for="g in gaps" :key="g.concept_id" class="gap-row">
          <div class="gap-info">
            <strong>{{ g.concept }}</strong>
            <span>{{ g.recent_failures }} échecs récents · score {{ Math.round(g.score) }} %</span>
          </div>
          <RouterLink to="/exercices" class="secondary-action" style="min-height:34px;padding:6px 12px;font-size:12px;">S'exercer</RouterLink>
        </div>
      </div>
    </section>

    <!-- Due reviews quick grade -->
    <section v-if="dueReviews.length" class="content-panel reviews-quick">
      <div class="panel-heading">
        <div><p class="eyebrow">Révisions dues</p><h2>{{ dueReviews.length }} à traiter</h2></div>
        <BookOpenCheck :size="18" aria-hidden="true" />
      </div>
      <div class="due-list">
        <div v-for="card in dueReviews.slice(0,6)" :key="card.id" class="due-row">
          <div class="due-q">
            <strong>{{ card.question }}</strong>
            <small>{{ card.answer.slice(0, 80) }}{{ card.answer.length > 80 ? "…" : "" }}</small>
          </div>
          <div class="due-actions">
            <button type="button" class="secondary-action" style="min-height:32px;padding:6px 10px;font-size:12px;" @click="gradeReview(card.id, false)">Raté</button>
            <button type="button" class="primary-action" style="min-height:32px;padding:6px 10px;font-size:12px;" @click="gradeReview(card.id, true)">Réussi</button>
          </div>
        </div>
      </div>
    </section>

    <!-- Error history -->
    <section v-if="errors.length" class="content-panel errors-panel">
      <div class="panel-heading">
        <div><p class="eyebrow">Historique</p><h2>Erreurs récentes</h2></div>
        <History :size="18" aria-hidden="true" />
      </div>
      <div class="errors-list">
        <div v-for="(e, i) in errors.slice(0,8)" :key="i" class="error-row">
          <div class="error-top">
            <span class="status-pill status-pill--orange" style="font-size:10px;">{{ e.error_type }}</span>
            <strong>{{ e.concept_name }}</strong>
            <small>{{ e.created_at ? new Date(e.created_at).toLocaleDateString('fr-FR') : "" }}</small>
          </div>
          <p class="error-q">Q: {{ e.question_text }}</p>
          <p class="error-a"><span class="given">→ {{ e.given_answer }}</span> <span class="correct">✓ {{ e.correct_answer }}</span></p>
        </div>
      </div>
    </section>

    <section v-if="!gaps.length && !errors.length && !dueReviews.length" class="content-panel empty-state" style="padding:22px;display:flex;gap:12px;align-items:center;">
      <Trophy :size="20" aria-hidden="true" />
      <p style="margin:0;color:var(--muted);font-size:13px;">Peu d'activité pour l'instant — fais un quiz ou un exercice pour alimenter le suivi.</p>
    </section>
  </section>

  <section v-else class="page loading-state"><Loader2 :size="24" class="spin" /><p>{{ t('app.loadingWorkshop') }}</p></section>
</template>

<style scoped>
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
.gaps-panel, .reviews-quick, .errors-panel { padding: 25px; margin-top: 16px; }
.gaps-list, .due-list, .errors-list { display: grid; gap: 10px; }
.gap-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; }
.gap-info { display: grid; gap: 2px; }
.gap-info strong { font-size: 14px; }
.gap-info span { color: var(--muted); font-size: 12px; }
.due-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; }
.due-q { display: grid; gap: 4px; min-width: 0; }
.due-q strong { font-size: 13px; line-height: 1.4; white-space: pre-wrap; }
.due-q small { color: var(--muted); font-size: 12px; }
.due-actions { display: flex; gap: 8px; align-items: center; }
.error-row { padding: 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; display: grid; gap: 6px; }
.error-top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.error-top small { margin-left: auto; color: var(--muted); font-size: 11px; }
.error-q { margin: 0; font-size: 12.5px; font-weight: 600; line-height: 1.4; }
.error-a { margin: 0; font-size: 12px; color: var(--muted); display: flex; gap: 10px; flex-wrap: wrap; }
.error-a .given { color: #b42318; }
.error-a .correct { color: var(--green); }
.lib-empty { color: var(--muted); font-size: 13px; text-align: center; }
a.concept-row { cursor: pointer; border-radius: 12px; }
a.concept-row:hover { background: var(--panel-soft); }
@media (max-width: 800px) { .due-row { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
