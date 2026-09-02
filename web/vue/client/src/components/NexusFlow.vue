<!-- EduNexus UI direction: Atelier de progression — le motif Nexus relie les sources, les notions et la prochaine action sur chaque page. -->
<script setup lang="ts">
import { usePreferences } from "@/stores/preferences";
const { t } = usePreferences();
</script>

<template>
  <div class="nexus-flow" :aria-label="t('nexus.aria')">
    <span><i></i>{{ t('nexus.sources') }}</span><b aria-hidden="true"></b><span><i></i>{{ t('nexus.concepts') }}</span><b aria-hidden="true"></b><span class="is-active"><i></i>{{ t('nexus.activeStep') }}</span>
  </div>
</template>
