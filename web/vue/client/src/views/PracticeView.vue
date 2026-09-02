<!-- EduNexus UI direction: Atelier de progression — l’exercice cible une notion et explique son niveau de difficulté. -->
<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowRight, Lightbulb, Target } from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state, weakestConcept } = useLearningStore();
const { t } = usePreferences();
const concept = ref("loops");
const difficulty = ref("adaptatif");
const activeConcept = computed(() => state.data?.concepts.find((item) => item.id === concept.value) ?? weakestConcept.value);
</script>

<template>
  <section v-if="state.data" class="page practice-page"><header class="page-intro"><div><p class="eyebrow">{{ t('practice.kicker') }}</p><h1>{{ t('practice.title') }}</h1><p>{{ t('practice.copy') }}</p></div></header><section class="practice-layout"><article class="content-panel exercise-config"><div class="panel-heading"><div><p class="eyebrow">{{ t('practice.prepare') }}</p><h2>{{ t('practice.adaptive') }}</h2></div><Target :size="21" aria-hidden="true" /></div><label class="field-label" for="practice-concept">{{ t('practice.concept') }}</label><select id="practice-concept" v-model="concept"><option v-for="item in state.data.concepts" :key="item.id" :value="item.id">{{ item.name }} · {{ item.score }} %</option></select><label class="field-label" for="difficulty">{{ t('practice.difficulty') }}</label><div id="difficulty" class="segmented" role="group" :aria-label="t('practice.difficulty')"><button v-for="level in [{ id: 'guided', label: t('practice.guided') }, { id: 'adaptatif', label: t('practice.adaptiveLevel') }, { id: 'challenge', label: t('practice.challenge') }]" :key="level.id" type="button" :class="{ selected: difficulty === level.id }" @click="difficulty = level.id">{{ level.label }}</button></div><div class="recommendation"><Lightbulb :size="19" aria-hidden="true" /><p><strong>{{ t('practice.advice') }}</strong> {{ activeConcept?.score && activeConcept.score < 40 ? t('practice.guidedAdvice') : t('practice.adaptiveAdvice') }}</p></div><button type="button" class="primary-action">{{ t('practice.generate') }} <ArrowRight :size="17" /></button></article><article class="exercise-preview"><span class="preview-label">{{ t('practice.preview') }}</span><p class="eyebrow">{{ activeConcept?.name }}</p><h2>{{ t('practice.previewTitle') }}</h2><p>{{ t('practice.previewCopy') }}</p><div class="preview-foot"><span>{{ t('practice.source') }} : {{ state.data.books[0]?.title }}</span><span>{{ t('practice.duration') }} : {{ t('practice.minutes') }}</span></div></article></section></section>
</template>
