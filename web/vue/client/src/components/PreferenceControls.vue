<!-- EduNexus UI direction: Atelier de progression — les préférences sont compactes, explicites et toujours accessibles. -->
<script setup lang="ts">
import { Moon, Sun } from "lucide-vue-next";
import { usePreferences } from "@/stores/preferences";

const { locale, isNight, setLocale, t, toggleTheme } = usePreferences();
</script>

<template>
  <div class="preference-controls">
    <div class="locale-switch" role="group" :aria-label="t('preferences.language')">
      <button type="button" :aria-pressed="locale === 'fr'" @click="setLocale('fr')">FR</button>
      <button type="button" :aria-pressed="locale === 'en'" @click="setLocale('en')">EN</button>
    </div>
    <button type="button" class="theme-toggle" :aria-label="isNight ? t('preferences.light') : t('preferences.dark')" :title="isNight ? t('preferences.light') : t('preferences.dark')" @click="toggleTheme">
      <Sun v-if="isNight" :size="17" aria-hidden="true" />
      <Moon v-else :size="17" aria-hidden="true" />
      <span>{{ isNight ? t('preferences.day') : t('preferences.night') }}</span>
    </button>
  </div>
</template>
