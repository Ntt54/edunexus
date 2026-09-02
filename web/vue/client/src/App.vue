<!-- EduNexus UI direction: Atelier de progression — clarté, prochaine étape et preuves de maîtrise. -->
<script setup lang="ts">
import { onMounted } from "vue";
import AppShell from "@/components/AppShell.vue";
import { useLearningStore } from "@/stores/learning";

const { hydrate } = useLearningStore();
onMounted(hydrate);
</script>

<template>
  <AppShell />
</template>
