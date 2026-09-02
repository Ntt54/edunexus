<!-- EduNexus UI direction: Atelier de progression — rendre le progrès lisible sans graphique décoratif. -->
<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(defineProps<{ value: number; size?: number }>(), { size: 88 });
const safeValue = computed(() => Math.max(0, Math.min(100, props.value)));
const style = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  background: `conic-gradient(#4f46e5 ${safeValue.value * 3.6}deg, #e9eafb 0deg)`,
}));
</script>

<template>
  <div class="progress-ring" :style="style" :aria-label="`${safeValue}% terminé`" role="img">
    <div><strong>{{ safeValue }}%</strong><span>terminé</span></div>
  </div>
</template>
