<!-- EduNexus UI direction: Atelier de progression — les états sont explicités par du texte, pas seulement par une couleur. -->
<script setup lang="ts">
withDefaults(defineProps<{ tone?: "indigo" | "orange" | "green" | "slate" }>(), { tone: "slate" });
</script>

<template>
  <span class="status-pill" :class="`status-pill--${tone}`"><slot /></span>
</template>
