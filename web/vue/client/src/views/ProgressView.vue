<!-- EduNexus UI direction: Atelier de progression — les scores et lacunes deviennent des actions, jamais un jugement opaque. -->
<script setup lang="ts">
import { computed } from "vue";
import { ArrowRight, CheckCircle2, CircleAlert, TrendingUp } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state } = useLearningStore();
const { t } = usePreferences();
const data = computed(() => state.data);
const stateFor = (score: number) => score >= 75 ? [t('status.mastered'), "green"] : score >= 40 ? [t('status.current'), "indigo"] : [t('status.review'), "orange"];
</script>

<template>
  <section v-if="data" class="page progress-page"><header class="page-intro"><div><p class="eyebrow">{{ t('progress.kicker') }}</p><h1>{{ t('progress.title') }}</h1><p>{{ t('progress.copy') }}</p></div></header><section class="progress-summary"><article><TrendingUp :size="22" aria-hidden="true" /><div><span>{{ t('progress.path') }}</span><strong>{{ data.path.progress }} %</strong><small>{{ t('progress.steps', { count: data.path.steps.filter((item) => item.status === 'completed').length }) }}</small></div></article><article><CheckCircle2 :size="22" aria-hidden="true" /><div><span>{{ t('progress.stable') }}</span><strong>{{ data.concepts.filter((item) => item.score >= 75).length }}</strong><small>{{ t('progress.stableReason') }}</small></div></article><article><CircleAlert :size="22" aria-hidden="true" /><div><span>{{ t('progress.consolidate') }}</span><strong>{{ data.concepts.filter((item) => item.score < 40).length }}</strong><small>{{ t('progress.consolidateReason') }}</small></div></article></section><section class="content-panel concept-table"><div class="panel-heading"><div><p class="eyebrow">{{ t('progress.byConcept') }}</p><h2>{{ t('progress.map') }}</h2></div><RouterLink to="/exercices" class="text-link">{{ t('progress.exercise') }}</RouterLink></div><article v-for="concept in data.concepts" :key="concept.id" class="concept-row"><div class="concept-label"><strong>{{ concept.name }}</strong><span>{{ concept.nextReview ? t('progress.nextReview', { date: concept.nextReview }) : t('progress.new') }}</span></div><div class="concept-meter"><span><i :style="{ width: `${concept.score}%` }"></i></span><b>{{ concept.score }} %</b></div><StatusPill :tone="stateFor(concept.score)[1] as any">{{ stateFor(concept.score)[0] }}</StatusPill><RouterLink to="/exercices" class="row-action" :aria-label="concept.name"><ArrowRight :size="18" aria-hidden="true" /></RouterLink></article></section></section>
</template>
