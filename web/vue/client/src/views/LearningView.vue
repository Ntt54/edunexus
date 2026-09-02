<!-- EduNexus UI direction: Atelier de progression — une séance calme, structurée en compréhension, rappel et vérification. -->
<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowRight, BookOpenCheck, BrainCircuit, Headphones, RotateCcw } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state } = useLearningStore();
const { t } = usePreferences();
const selected = ref("loops");
const reviewMoment = "/manus-storage/edunexus-review-moment_492064e9.png";
const current = computed(() => state.data?.concepts.find((item) => item.id === selected.value) ?? state.data?.concepts[0]);
</script>

<template>
  <section v-if="state.data" class="page learning-page">
    <header class="page-intro"><div><p class="eyebrow">{{ t('learning.kicker') }}</p><h1>{{ t('learning.title') }}</h1><p>{{ t('learning.copy') }}</p></div></header>
    <section class="session-plan"><article><span>01</span><div><strong>{{ t('learning.choose') }}</strong><small>{{ t('learning.chooseCopy') }}</small></div></article><article><span>02</span><div><strong>{{ t('learning.recall') }}</strong><small>{{ t('learning.recallCopy') }}</small></div></article><article><span>03</span><div><strong>{{ t('learning.check') }}</strong><small>{{ t('learning.checkCopy') }}</small></div></article></section>
    <section class="learning-grid"><article class="content-panel concept-focus"><div class="panel-heading"><div><p class="eyebrow">{{ t('learning.current') }}</p><h2>{{ current?.name }}</h2></div><StatusPill :tone="(current?.score ?? 0) < 40 ? 'orange' : 'indigo'">{{ t('learning.mastery', { score: current?.score ?? 0 }) }}</StatusPill></div><label class="field-label" for="concept">{{ t('learning.change') }}</label><select id="concept" v-model="selected"><option v-for="concept in state.data.concepts" :key="concept.id" :value="concept.id">{{ concept.name }} · {{ concept.score }} %</option></select><div class="mastery-scale"><span><i :style="{ width: `${current?.score ?? 0}%` }"></i></span><small>{{ t('learning.scoreReason') }}</small></div><div class="recall-card"><BrainCircuit :size="22" aria-hidden="true" /><div><strong>{{ t('learning.recallQuestion') }}</strong><p>{{ t('learning.recallPrompt') }}</p></div></div><textarea rows="4" :placeholder="t('learning.answerPlaceholder')" :aria-label="t('learning.answerPlaceholder')"></textarea><button class="primary-action" type="button">{{ t('learning.checkReasoning') }} <ArrowRight :size="17" /></button></article>
      <aside class="learning-side"><article class="illustrated-card"><img :src="reviewMoment" :alt="t('learning.due')" @error="($event.currentTarget as HTMLImageElement).style.display = 'none'" /><div><p class="eyebrow">{{ t('learning.due') }}</p><h2>{{ t('learning.toDo', { count: state.data.reviews.length }) }}</h2><p>{{ t('learning.dueReason') }}</p><RouterLink to="/parcours" class="text-link">{{ t('learning.pathSteps') }}</RouterLink></div></article><article class="content-panel audio-card"><Headphones :size="21" aria-hidden="true" /><div><p class="eyebrow">{{ t('learning.audio') }}</p><h2>{{ t('learning.reviewDifferently') }}</h2><p>{{ t('learning.audioCopy') }}</p><button type="button" class="secondary-action">{{ t('learning.prepareSummary') }} <ArrowRight :size="16" /></button></div></article><article class="content-panel flashcard-card"><RotateCcw :size="21" aria-hidden="true" /><div><p class="eyebrow">{{ t('learning.cards') }}</p><h2>{{ t('learning.spaced') }}</h2><p>{{ t('learning.cardsReason', { count: current?.recentFailures ?? 0 }) }}</p></div></article></aside>
    </section>
  </section>
</template>
