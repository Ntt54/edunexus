<!-- EduNexus UI direction: Atelier de progression — une évaluation courte, explicite et reliée à une intention pédagogique. -->
<script setup lang="ts">
import { ref } from "vue";
import { ArrowRight, ClipboardCheck, TimerReset } from "lucide-vue-next";
import { usePreferences } from "@/stores/preferences";

const selected = ref("diagnostic");
const started = ref(false);
const { t } = usePreferences();
</script>

<template>
  <section class="page quiz-page"><header class="page-intro"><div><p class="eyebrow">{{ t('quiz.kicker') }}</p><h1>{{ t('quiz.title') }}</h1><p>{{ t('quiz.copy') }}</p></div></header><section class="quiz-layout"><article class="content-panel quiz-choice"><div class="panel-heading"><div><p class="eyebrow">{{ t('quiz.launch') }}</p><h2>{{ t('quiz.goal') }}</h2></div><ClipboardCheck :size="21" aria-hidden="true" /></div><button v-for="option in [{id:'diagnostic',title:t('quiz.diagnostic'),copy:t('quiz.diagnosticCopy')},{id:'review',title:t('quiz.review'),copy:t('quiz.reviewCopy')},{id:'challenge',title:t('quiz.challenge'),copy:t('quiz.challengeCopy')} ]" :key="option.id" type="button" class="choice-row" :class="{ selected: selected === option.id }" @click="selected = option.id"><span><strong>{{ option.title }}</strong><small>{{ option.copy }}</small></span><i></i></button><div class="duration-row"><TimerReset :size="18" aria-hidden="true" /><span>{{ t('quiz.duration') }} : <strong>{{ selected === 'challenge' ? '18' : selected === 'review' ? '8' : '10' }} min</strong></span></div><button type="button" class="primary-action" @click="started = true">{{ t('quiz.prepare') }} <ArrowRight :size="17" /></button></article><aside class="quiz-aside"><article class="content-panel"><p class="eyebrow">{{ t('quiz.trust') }}</p><h2>{{ t('quiz.citation') }}</h2><p>{{ t('quiz.citationCopy') }}</p></article><article v-if="started" class="content-panel ready-card"><p class="eyebrow">{{ t('quiz.ready') }}</p><h2>{{ t('quiz.readyCopy') }}</h2><p>{{ t('quiz.connect') }}</p></article></aside></section></section>
</template>
