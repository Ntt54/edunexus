<!-- EduNexus UI direction: Atelier de progression — la prochaine action est prioritaire, les métriques servent la décision. -->
<script setup lang="ts">
import { computed } from "vue";
import { ArrowRight, BookMarked, CheckCircle2, CircleAlert, Clock3, FileText, ListChecks } from "lucide-vue-next";
import ProgressRing from "@/components/ProgressRing.vue";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state, nextStep, masteredCount, weakestConcept } = useLearningStore();
const { t } = usePreferences();
const data = computed(() => state.data);
const pathAtlas = "/manus-storage/edunexus-path-atlas_307e7394.png";
const nextRoute = computed(() => {
  const type = nextStep.value?.activityType;
  return type === "quiz" ? "/quiz" : type === "exercise" ? "/exercices" : type === "flashcard_review" ? "/reviser" : "/parcours";
});
const activityLabel = computed(() => t(`activity.${nextStep.value?.activityType ?? "concept"}`));
</script>

<template>
  <section v-if="data" class="page dashboard-page">
    <header class="page-intro dashboard-intro">
      <div>
        <p class="eyebrow">{{ t('dashboard.kicker') }}</p>
        <h1>{{ t('dashboard.title') }}</h1>
        <p>{{ t('dashboard.context', { subject: data.subject.name }) }}</p>
      </div>
      <div class="subject-token"><span>{{ t('subject.active') }}</span><strong>{{ data.subject.name }}</strong><small>{{ data.subject.level }}</small></div>
    </header>

    <article class="next-step-card">
      <img :src="pathAtlas" alt="" aria-hidden="true" @error="($event.currentTarget as HTMLImageElement).style.display = 'none'" />
      <div class="next-step-main">
        <div class="next-step-label"><span></span> {{ t('dashboard.next') }}</div>
        <p class="next-step-path">{{ data.path.title }} · {{ t('dashboard.stepNumber', { current: data.path.steps.findIndex((item) => item.id === nextStep?.id) + 1, total: data.path.steps.length }) }}</p>
        <h2>{{ nextStep?.title ?? t('dashboard.finished') }}</h2>
        <p>{{ nextStep ? `${activityLabel} · ${t('dashboard.approx', { minutes: nextStep.duration })} · ${nextStep.source}` : t('dashboard.finishedCopy') }}</p>
        <RouterLink :to="nextRoute" class="primary-action">
          {{ nextStep ? t('dashboard.openStep') : t('dashboard.viewPath') }} <ArrowRight :size="18" aria-hidden="true" />
        </RouterLink>
      </div>
      <ProgressRing :value="data.path.progress" :size="104" />
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
  <section v-else-if="state.error" class="page empty-state">
    <div class="empty-panel">
      <CircleAlert :size="48" aria-hidden="true" />
      <h2>{{ t('dashboard.welcome') }}</h2>
      <p>{{ state.error }}</p>
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
