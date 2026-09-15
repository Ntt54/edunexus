<!-- EduNexus UI direction: Atelier de progression — la prochaine action est prioritaire, les métriques servent la décision. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ArrowRight, BookMarked, CheckCircle2, CircleAlert, Clock3, FileText } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi, invalidateSubjectCaches } from "@/services/api";

const { state, nextStep: legacyNextStep, masteredCount, weakestConcept, hydrate } = useLearningStore();
const { t, activeSubjectId, activeLearnerId } = usePreferences();
const data = computed(() => state.data);

// ── 011 · Dashboard filtré par couple (FR-001/006/007/009) ────────────
const filtered = ref<{ nextStep: { id: string; title: string; progress: number; notion?: string } | null; counts: { sources: number; notions: number }; paths: unknown[] } | null>(null);
const filteredLoading = ref(false);
const filteredError = ref<string | null>(null);
const filteredSubjectName = ref("");
let dashGen = 0;

const RING_C = 326.73;
// Progress from filtered if present, else legacy
const ringProgress = computed(() => {
  if (filtered.value?.nextStep?.progress != null) return Math.max(0, Math.min(100, filtered.value.nextStep.progress));
  return Math.max(0, Math.min(100, data.value?.path.progress ?? 0));
});
const nextRoute = computed(() => {
  const type = (legacyNextStep.value as unknown as { activityType?: string })?.activityType;
  return type === "quiz" ? "/quiz" : type === "exercise" ? "/exercices" : type === "flashcard_review" ? "/reviser" : "/parcours";
});
const activityLabel = computed(() => t(`activity.${(legacyNextStep.value as unknown as { activityType?: string })?.activityType ?? "concept"}`));

const isEmptyFiltered = computed(() => filtered.value !== null && filtered.value.nextStep === null);
const nextTitle = computed(() => filtered.value?.nextStep?.title ?? legacyNextStep.value?.title ?? t('dashboard.finished'));
const hasFiltered = computed(() => filtered.value !== null);

async function loadFiltered() {
  const sid = (activeSubjectId.value || localStorage.getItem("edunexus.space") || localStorage.getItem("edunexus:subject") || "").trim();
  const lid = (activeLearnerId.value || localStorage.getItem("edunexus.learner") || localStorage.getItem("edunexus:learner") || "").trim();
  const gen = ++dashGen;
  // garde anti-spam: si pas de matière, pas de fetch, état vide local immédiat (évite 400 loop + blink)
  if (!sid) {
    filteredLoading.value = false;
    filtered.value = { nextStep: null, counts: { sources: 0, notions: 0 }, paths: [] };
    filteredSubjectName.value = "";
    filteredError.value = null;
    return;
  }
  // No-flash: clear previous matter data immediately, show loader
  filteredLoading.value = true;
  filtered.value = null;
  filteredError.value = null;
  // helper tolerant: map 400/404 → vide sans boucle, single retry backoff sinon
  async function fetchDashWithGuard(id: string, learner: string | undefined, retried = false): Promise<{ nextStep: { id: string; title: string; progress: number; notion?: string } | null; counts: { sources: number; notions: number }; paths: unknown[] }> {
    try {
      return await tutorApi.getDashboardFiltered(id, learner) as unknown as { nextStep: { id: string; title: string; progress: number; notion?: string } | null; counts: { sources: number; notions: number }; paths: unknown[] };
    } catch (e) {
      const st = (e as { status?: number }).status;
      if (st === 400 || st === 404) return { nextStep: null, counts: { sources: 0, notions: 0 }, paths: [] };
      if (!retried) { await new Promise(r => setTimeout(r, 700)); return fetchDashWithGuard(id, learner, true); }
      throw e;
    }
  }
  try {
    const [dash, subjRes] = await Promise.all([
      fetchDashWithGuard(sid, lid || undefined).catch(() => ({ nextStep: null, counts: { sources: 0, notions: 0 }, paths: [] } as unknown as { nextStep: null; counts: { sources: number; notions: number }; paths: unknown[] })),
      tutorApi.getSubjects().catch(() => ({ subjects: [] as Array<{ id: string; name: string }>, active_id: null })),
    ]);
    if (gen !== dashGen) return;
    filtered.value = dash as unknown as typeof filtered.value;
    const found = (subjRes.subjects || []).find(s => s.id === sid);
    filteredSubjectName.value = found?.name || "";
  } catch (e) {
    if (gen !== dashGen) return;
    filteredError.value = e instanceof Error ? e.message : "Erreur";
    // évite écran bloqué: fallback vide
    filtered.value = { nextStep: null, counts: { sources: 0, notions: 0 }, paths: [] };
  } finally {
    if (gen === dashGen) filteredLoading.value = false;
  }
}

function onSubjectChange() {
  invalidateSubjectCaches();
  void loadFiltered();
}
function onLearnerChange() {
  invalidateSubjectCaches();
  void loadFiltered();
}

watch(() => activeSubjectId.value, () => { onSubjectChange(); });
watch(() => activeLearnerId.value, () => { onLearnerChange(); });

onMounted(async () => {
  await hydrate();
  await loadFiltered();
  window.addEventListener("edunexus:subjectChange", onSubjectChange as EventListener);
  window.addEventListener("subjectChange", onSubjectChange as EventListener);
  window.addEventListener("edunexus:learnerChange", onLearnerChange as EventListener);
  window.addEventListener("learnerChange", onLearnerChange as EventListener);
});
onUnmounted(() => {
  window.removeEventListener("edunexus:subjectChange", onSubjectChange as EventListener);
  window.removeEventListener("subjectChange", onSubjectChange as EventListener);
  window.removeEventListener("edunexus:learnerChange", onLearnerChange as EventListener);
  window.removeEventListener("learnerChange", onLearnerChange as EventListener);
});
</script>

<template>
  <!-- Loading sans flash : on masque l'ancien contenu dès le switch -->
  <section v-if="filteredLoading" class="page loading-state"><p>{{ t('app.loadingWorkshop') }}</p></section>

  <!-- État vide filtré (FR-007) : matière sans parcours -->
  <section v-else-if="isEmptyFiltered" class="page dashboard-page">
    <header class="page-intro dashboard-intro">
      <div>
        <p class="eyebrow">{{ t('dashboard.kicker') }}</p>
        <h1>{{ t('dashboard.title') }}</h1>
        <p>{{ t('dashboard.context', { subject: filteredSubjectName || t('subject.active') }) }}</p>
      </div>
      <div class="subject-token"><span>{{ t('subject.active') }}</span><strong>{{ filteredSubjectName }}</strong></div>
    </header>
    <div class="empty-panel" style="margin-top:18px">
      <CircleAlert :size="48" aria-hidden="true" />
      <h2>Aucun parcours pour cette matière</h2>
      <p>Matière active : {{ filteredSubjectName || '—' }} — {{ filtered?.counts.sources ?? 0 }} source(s), {{ filtered?.counts.notions ?? 0 }} notion(s). Créez un parcours pour commencer.</p>
      <div class="empty-actions">
        <RouterLink to="/parcours" class="primary-action">{{ t('dashboard.createPath') }} <ArrowRight :size="18" /></RouterLink>
        <RouterLink to="/sources" class="secondary-action">{{ t('dashboard.importFirst') }} <ArrowRight :size="18" /></RouterLink>
      </div>
    </div>
  </section>

  <section v-else-if="hasFiltered && filtered?.nextStep" class="page dashboard-page">
    <header class="page-intro dashboard-intro">
      <div>
        <p class="eyebrow">{{ t('dashboard.kicker') }}</p>
        <h1>{{ t('dashboard.title') }}</h1>
        <p>{{ t('dashboard.context', { subject: filteredSubjectName || data?.subject.name || '' }) }}</p>
      </div>
      <div class="subject-token"><span>{{ t('subject.active') }}</span><strong>{{ filteredSubjectName || data?.subject.name || '' }}</strong><small v-if="data?.subject.level">{{ data?.subject.level }}</small></div>
    </header>

    <article class="next-step-card">
      <svg class="next-step-deco" viewBox="0 0 360 220" aria-hidden="true" focusable="false">
        <path d="M-10,170 C60,90 120,200 190,120 S300,40 380,110" fill="none" style="stroke:var(--indigo)" stroke-width="3" stroke-linecap="round" opacity="0.45" />
        <path d="M-10,190 C70,130 150,210 220,150 S320,90 380,150" fill="none" style="stroke:var(--orange)" stroke-width="2" stroke-dasharray="2 7" stroke-linecap="round" opacity="0.5" />
        <circle cx="190" cy="120" r="7" style="fill:var(--indigo-soft);stroke:var(--indigo)" stroke-width="3" />
        <circle cx="285" cy="78" r="4.5" style="fill:var(--orange-soft);stroke:var(--orange)" stroke-width="2.5" />
        <circle cx="95" cy="148" r="4" style="fill:var(--green-soft);stroke:var(--green)" stroke-width="2.5" />
      </svg>
      <div class="next-step-main">
        <div class="next-step-label"><span></span> {{ t('dashboard.next') }}</div>
        <p v-if="filtered?.nextStep" class="next-step-path">{{ nextTitle }}</p>
        <p v-else class="next-step-path">{{ data?.path.title ?? '' }} · {{ data ? t('dashboard.stepNumber', { current: data.path.steps.findIndex((item) => item.id === legacyNextStep?.id) + 1, total: data.path.steps.length }) : '' }}</p>
        <h2>{{ nextTitle }}</h2>
        <p v-if="filtered?.nextStep">{{ t('dashboard.approx', { minutes: 12 }) }} · Matière active : {{ filteredSubjectName }}</p>
        <p v-else>{{ legacyNextStep ? `${activityLabel} · ${t('dashboard.approx', { minutes: legacyNextStep.duration })} · ${legacyNextStep.source}` : t('dashboard.finishedCopy') }}</p>
        <RouterLink :to="nextRoute" class="primary-action">
          {{ filtered?.nextStep ? t('dashboard.openStep') : (legacyNextStep ? t('dashboard.openStep') : t('dashboard.viewPath')) }} <ArrowRight :size="18" aria-hidden="true" />
        </RouterLink>
      </div>
      <div class="hero-ring" role="img" :aria-label="String(Math.round(ringProgress)) + ' %'">
        <svg viewBox="0 0 120 120" aria-hidden="true" focusable="false">
          <circle cx="60" cy="60" r="52" fill="none" style="stroke:var(--line)" stroke-width="12" />
          <circle cx="60" cy="60" r="52" fill="none" style="stroke:var(--indigo)" stroke-width="12" stroke-linecap="round" :stroke-dasharray="`${(ringProgress * RING_C) / 100} ${RING_C}`" transform="rotate(-90 60 60)" />
        </svg>
        <div class="hero-ring-label"><strong>{{ Math.round(ringProgress) }} %</strong></div>
      </div>
    </article>

    <section class="metric-grid" aria-label="Résumé de progression">
      <article class="metric-card"><div class="metric-icon indigo"><CompassIcon /></div><div><span>{{ t('dashboard.path') }}</span><strong>{{ Math.round(ringProgress) }}%</strong><small v-if="filtered">Matière active : {{ filteredSubjectName }}</small><small v-else>{{ t('dashboard.stepsValidated', { count: data?.path.steps.filter((item) => item.status === 'completed').length ?? 0 }) }}</small></div></article>
      <article class="metric-card"><div class="metric-icon orange"><Clock3 :size="19" aria-hidden="true" /></div><div><span>{{ t('dashboard.reviews') }}</span><strong>{{ data?.reviews.length ?? 0 }}</strong><small>{{ (data?.reviews.length ?? 0) ? t('dashboard.reviewReason') : t('dashboard.noReview') }}</small></div></article>
      <article class="metric-card"><div class="metric-icon green"><CheckCircle2 :size="19" aria-hidden="true" /></div><div><span>{{ t('dashboard.stable') }}</span><strong>{{ masteredCount }}/{{ data?.concepts.length ?? filtered?.counts.notions ?? 0 }}</strong><small>{{ t('dashboard.stableReason') }}</small></div></article>
      <article class="metric-card"><div class="metric-icon slate"><FileText :size="19" aria-hidden="true" /></div><div><span>{{ t('dashboard.sources') }}</span><strong>{{ filtered?.counts.sources ?? data?.books.length ?? 0 }}</strong><small>{{ t('dashboard.documentsUsable', { count: filtered?.counts.sources ?? data?.books.filter((item) => item.status === 'indexed').length ?? 0 }) }}</small></div></article>
    </section>

    <section class="dashboard-columns">
      <section class="content-panel reviews-panel">
        <div class="panel-heading"><div><p class="eyebrow">{{ t('dashboard.remember') }}</p><h2>{{ t('dashboard.priorityReviews') }}</h2></div><RouterLink to="/reviser">{{ t('dashboard.viewAll') }}</RouterLink></div>
        <div v-if="data?.reviews.length" class="review-list">
          <RouterLink v-for="review in data.reviews" :key="review.id" to="/reviser" class="review-row">
            <div class="review-mark"><BookMarked :size="18" aria-hidden="true" /></div>
            <div><strong>{{ review.concept }}</strong><span>{{ review.source }}</span></div>
            <StatusPill tone="orange">{{ review.due === 'Aujourd’hui' ? t('dashboard.today') : review.due === 'Demain' ? t('dashboard.tomorrow') : review.due }}</StatusPill>
            <ArrowRight :size="17" aria-hidden="true" />
          </RouterLink>
        </div>
        <p v-else class="empty-copy">{{ t('dashboard.emptyReview') }}</p>
      </section>

      <aside class="context-stack">
        <section class="content-panel weak-panel">
          <div class="panel-heading"><div><p class="eyebrow">{{ t('dashboard.consolidate') }}</p><h2>{{ t('dashboard.fragile') }}</h2></div><CircleAlert :size="20" aria-hidden="true" /></div>
          <template v-if="weakestConcept"><strong class="weak-name">{{ weakestConcept.name }}</strong><div class="score-line"><span><i :style="{ width: `${weakestConcept.score}%` }"></i></span><b>{{ weakestConcept.score }}%</b></div><p>{{ t('dashboard.difficulty', { count: weakestConcept.recentFailures }) }}</p><RouterLink to="/exercices" class="secondary-action">{{ t('dashboard.practiceConcept') }} <ArrowRight :size="16" /></RouterLink></template>
        </section>
        <section class="content-panel source-panel"><div><p class="eyebrow">{{ t('dashboard.activeSources') }}</p><h2>{{ t('dashboard.readyDocuments', { count: filtered?.counts.sources ?? data?.books.filter((item) => item.status === 'indexed').length ?? 0 }) }}</h2><p>{{ t('dashboard.sourceReason') }}</p></div><RouterLink to="/sources" class="text-link">{{ t('dashboard.manageSources') }}</RouterLink></section>
      </aside>
    </section>
  </section>

  <section v-else-if="data" class="page dashboard-page">
    <header class="page-intro dashboard-intro">
      <div>
        <p class="eyebrow">{{ t('dashboard.kicker') }}</p>
        <h1>{{ t('dashboard.title') }}</h1>
        <p>{{ t('dashboard.context', { subject: data.subject.name }) }}</p>
      </div>
      <div class="subject-token"><span>{{ t('subject.active') }}</span><strong>{{ data.subject.name }}</strong><small>{{ data.subject.level }}</small></div>
    </header>

    <article class="next-step-card">
      <svg class="next-step-deco" viewBox="0 0 360 220" aria-hidden="true" focusable="false">
        <path d="M-10,170 C60,90 120,200 190,120 S300,40 380,110" fill="none" style="stroke:var(--indigo)" stroke-width="3" stroke-linecap="round" opacity="0.45" />
        <path d="M-10,190 C70,130 150,210 220,150 S320,90 380,150" fill="none" style="stroke:var(--orange)" stroke-width="2" stroke-dasharray="2 7" stroke-linecap="round" opacity="0.5" />
        <circle cx="190" cy="120" r="7" style="fill:var(--indigo-soft);stroke:var(--indigo)" stroke-width="3" />
        <circle cx="285" cy="78" r="4.5" style="fill:var(--orange-soft);stroke:var(--orange)" stroke-width="2.5" />
        <circle cx="95" cy="148" r="4" style="fill:var(--green-soft);stroke:var(--green)" stroke-width="2.5" />
      </svg>
      <div class="next-step-main">
        <div class="next-step-label"><span></span> {{ t('dashboard.next') }}</div>
        <p class="next-step-path">{{ data.path.title }} · {{ t('dashboard.stepNumber', { current: data.path.steps.findIndex((item) => item.id === legacyNextStep?.id) + 1, total: data.path.steps.length }) }}</p>
        <h2>{{ legacyNextStep?.title ?? t('dashboard.finished') }}</h2>
        <p>{{ legacyNextStep ? `${activityLabel} · ${t('dashboard.approx', { minutes: legacyNextStep.duration })} · ${legacyNextStep.source}` : t('dashboard.finishedCopy') }}</p>
        <RouterLink :to="nextRoute" class="primary-action">
          {{ legacyNextStep ? t('dashboard.openStep') : t('dashboard.viewPath') }} <ArrowRight :size="18" aria-hidden="true" />
        </RouterLink>
      </div>
      <div class="hero-ring" role="img" :aria-label="t('dashboard.complete', { percent: Math.round(ringProgress) })">
        <svg viewBox="0 0 120 120" aria-hidden="true" focusable="false">
          <circle cx="60" cy="60" r="52" fill="none" style="stroke:var(--line)" stroke-width="12" />
          <circle cx="60" cy="60" r="52" fill="none" style="stroke:var(--indigo)" stroke-width="12" stroke-linecap="round" :stroke-dasharray="`${(ringProgress * RING_C) / 100} ${RING_C}`" transform="rotate(-90 60 60)" />
        </svg>
        <div class="hero-ring-label"><strong>{{ Math.round(ringProgress) }} %</strong></div>
      </div>
    </article>

    <section class="metric-grid" aria-label="Résumé de progression">
      <article class="metric-card"><div class="metric-icon indigo"><CompassIcon /></div><div><span>{{ t('dashboard.path') }}</span><strong>{{ data.path.progress }}%</strong><small>{{ t('dashboard.stepsValidated', { count: data.path.steps.filter((item) => item.status === 'completed').length }) }}</small></div></article>
      <article class="metric-card"><div class="metric-icon orange"><Clock3 :size="19" aria-hidden="true" /></div><div><span>{{ t('dashboard.reviews') }}</span><strong>{{ data.reviews.length }}</strong><small>{{ data.reviews.length ? t('dashboard.reviewReason') : t('dashboard.noReview') }}</small></div></article>
      <article class="metric-card"><div class="metric-icon green"><CheckCircle2 :size="19" aria-hidden="true" /></div><div><span>{{ t('dashboard.stable') }}</span><strong>{{ masteredCount }}/{{ data.concepts.length }}</strong><small>{{ t('dashboard.stableReason') }}</small></div></article>
      <article class="metric-card"><div class="metric-icon slate"><FileText :size="19" aria-hidden="true" /></div><div><span>{{ t('dashboard.sources') }}</span><strong>{{ data.books.length }}</strong><small>{{ t('dashboard.documentsUsable', { count: data.books.filter((item) => item.status === 'indexed').length }) }}</small></div></article>
    </section>

    <section class="dashboard-columns">
      <section class="content-panel reviews-panel">
        <div class="panel-heading"><div><p class="eyebrow">{{ t('dashboard.remember') }}</p><h2>{{ t('dashboard.priorityReviews') }}</h2></div><RouterLink to="/reviser">{{ t('dashboard.viewAll') }}</RouterLink></div>
        <div v-if="data.reviews.length" class="review-list">
          <RouterLink v-for="review in data.reviews" :key="review.id" to="/reviser" class="review-row">
            <div class="review-mark"><BookMarked :size="18" aria-hidden="true" /></div>
            <div><strong>{{ review.concept }}</strong><span>{{ review.source }}</span></div>
            <StatusPill tone="orange">{{ review.due === 'Aujourd’hui' ? t('dashboard.today') : review.due === 'Demain' ? t('dashboard.tomorrow') : review.due }}</StatusPill>
            <ArrowRight :size="17" aria-hidden="true" />
          </RouterLink>
        </div>
        <p v-else class="empty-copy">{{ t('dashboard.emptyReview') }}</p>
      </section>

      <aside class="context-stack">
        <section class="content-panel weak-panel">
          <div class="panel-heading"><div><p class="eyebrow">{{ t('dashboard.consolidate') }}</p><h2>{{ t('dashboard.fragile') }}</h2></div><CircleAlert :size="20" aria-hidden="true" /></div>
          <template v-if="weakestConcept"><strong class="weak-name">{{ weakestConcept.name }}</strong><div class="score-line"><span><i :style="{ width: `${weakestConcept.score}%` }"></i></span><b>{{ weakestConcept.score }}%</b></div><p>{{ t('dashboard.difficulty', { count: weakestConcept.recentFailures }) }}</p><RouterLink to="/exercices" class="secondary-action">{{ t('dashboard.practiceConcept') }} <ArrowRight :size="16" /></RouterLink></template>
        </section>
        <section class="content-panel source-panel"><div><p class="eyebrow">{{ t('dashboard.activeSources') }}</p><h2>{{ t('dashboard.readyDocuments', { count: data.books.filter((item) => item.status === 'indexed').length }) }}</h2><p>{{ t('dashboard.sourceReason') }}</p></div><RouterLink to="/sources" class="text-link">{{ t('dashboard.manageSources') }}</RouterLink></section>
      </aside>
    </section>
  </section>
  <section v-else-if="state.error || filteredError" class="page empty-state">
    <div class="empty-panel">
      <CircleAlert :size="48" aria-hidden="true" />
      <h2>{{ t('dashboard.welcome') }}</h2>
      <p>{{ state.error || filteredError }}</p>
      <div class="empty-actions">
        <RouterLink to="/sources" class="primary-action">{{ t('dashboard.importFirst') }} <ArrowRight :size="18" /></RouterLink>
        <RouterLink to="/parcours" class="secondary-action">{{ t('dashboard.createPath') }} <ArrowRight :size="18" /></RouterLink>
      </div>
    </div>
  </section>
  <section v-else class="page loading-state"><p>{{ t('app.loadingWorkshop') }}</p></section>
</template>

<script lang="ts">
import { Compass as CompassIcon } from "lucide-vue-next";
export default { components: { CompassIcon } };
</script>

<style scoped>
.next-step-deco { position: absolute; top: 0; right: 0; width: 68%; height: 100%; opacity: .5; pointer-events: none; }
.hero-ring { position: relative; z-index: 1; width: 104px; height: 104px; flex: none; }
.hero-ring svg { width: 100%; height: 100%; display: block; }
.hero-ring-label { position: absolute; inset: 0; display: grid; place-items: center; }
.hero-ring-label strong { color: var(--ink); font: 700 22px/1 "Fraunces", Georgia, serif; }
@media (max-width: 800px) {
  .next-step-deco { width: 100%; opacity: .18; }
  .hero-ring { position: absolute; top: 20px; right: 20px; width: 71px; height: 71px; }
  .hero-ring-label strong { font-size: 15px; }
}
</style>
