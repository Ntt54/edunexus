<!-- 012 US4 (FR-013) — Lisibilité WCAG 2.2 AA : presets branchés sur stores/preferences.ts, libellés simples en français. -->
<script setup lang="ts">
import { computed } from "vue";
import { RotateCcw } from "lucide-vue-next";
import { usePreferences, type ReadabilityTheme } from "@/stores/preferences";

const {
  locale, t, readability,
  setFontScale, setLineHeight, setLetterSpacing, setReadabilityTheme, setDyslexia, resetReadability,
} = usePreferences();

const msg = computed(() => locale.value === "fr" ? {
  hint: "Confort de lecture. Rien n'est activé par défaut.",
  dysHint: "Police Verdana, fond crème, interligne 1,5.",
  normal: "Normale",
  wide: "Large",
} : {
  hint: "Reading comfort. Nothing is enabled by default.",
  dysHint: "Verdana font, cream background, 1.5 line height.",
  normal: "Normal",
  wide: "Wide",
});

const themes: ReadabilityTheme[] = ["systeme", "clair", "nuit", "creme"];

function onScale(e: Event) {
  setFontScale(Number((e.target as HTMLInputElement).value));
}
function onSpacing(e: Event) {
  const v = (e.target as HTMLSelectElement).value;
  setLetterSpacing(v === "wide" ? "0.05em" : "normal");
}
function onDyslexia(e: Event) {
  setDyslexia((e.target as HTMLInputElement).checked);
}
</script>

<template>
  <div class="read-panel">
    <p class="eyebrow">{{ t("readability.title") }}</p>
    <h3>{{ t("readability.title") }}</h3>
    <p class="read-hint">{{ msg.hint }}</p>

    <label class="read-row">
      <span>{{ t("readability.fontScale") }} · <strong>{{ readability.fontScale }} %</strong></span>
      <input
        type="range" min="100" max="200" step="10"
        :value="readability.fontScale"
        :aria-label="t('readability.fontScale')"
        @input="onScale"
      />
    </label>

    <div class="read-row">
      <span id="read-lh">{{ t("readability.lineHeight") }}</span>
      <div class="read-seg" role="group" aria-labelledby="read-lh">
        <button type="button" :aria-pressed="readability.lineHeight === 1" :class="{ on: readability.lineHeight === 1 }" @click="setLineHeight(1)">1,0</button>
        <button type="button" :aria-pressed="readability.lineHeight === 1.5" :class="{ on: readability.lineHeight === 1.5 }" @click="setLineHeight(1.5)">1,5</button>
      </div>
    </div>

    <label class="read-row">
      <span>{{ t("readability.letterSpacing") }}</span>
      <select :value="readability.letterSpacing === 'normal' ? 'normal' : 'wide'" :aria-label="t('readability.letterSpacing')" @change="onSpacing">
        <option value="normal">{{ msg.normal }}</option>
        <option value="wide">{{ msg.wide }}</option>
      </select>
    </label>

    <div class="read-row">
      <span id="read-th">{{ t("readability.theme") }}</span>
      <div class="read-seg read-seg--wrap" role="group" aria-labelledby="read-th">
        <button
          v-for="th in themes" :key="th" type="button"
          :aria-pressed="readability.theme === th"
          :class="{ on: readability.theme === th }"
          @click="setReadabilityTheme(th)"
        >{{ t(`readability.${th === "systeme" ? "system" : th === "clair" ? "light" : th === "nuit" ? "night" : "cream"}`) }}</button>
      </div>
    </div>

    <label class="read-row read-check">
      <span><strong>{{ t("readability.dyslexia") }}</strong><small>{{ msg.dysHint }}</small></span>
      <input type="checkbox" :checked="readability.dyslexia" @change="onDyslexia" />
    </label>

    <button type="button" class="secondary-action read-reset" @click="resetReadability">
      <RotateCcw :size="14" aria-hidden="true" /> {{ t("readability.reset") }}
    </button>
  </div>
</template>

<style scoped>
.read-panel { display: grid; gap: 12px; }
.read-panel h3 { margin: 0; font: 700 20px/1.15 "Fraunces", Georgia, serif; }
.read-hint { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
.read-row { display: grid; gap: 6px; font-size: 12px; font-weight: 700; color: var(--ink-2); }
.read-row > span { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.read-row input[type="range"] { width: 100%; accent-color: var(--indigo); }
.read-seg { display: flex; gap: 6px; flex-wrap: wrap; }
.read-seg button { min-height: 34px; padding: 6px 12px; border: 1px solid var(--line); border-radius: 9px; background: transparent; color: var(--ink-2); font-size: 12px; font-weight: 700; cursor: pointer; }
.read-seg button.on { border-color: var(--indigo); background: var(--indigo-soft); color: var(--indigo-deep); }
.read-check { display: flex; align-items: center; justify-content: space-between; gap: 12px; cursor: pointer; }
.read-check span { display: grid; gap: 2px; }
.read-check small { color: var(--muted); font-weight: 600; }
.read-check input { width: 20px; height: 20px; accent-color: var(--indigo); }
.read-reset { justify-self: start; min-height: 36px; padding: 7px 12px; font-size: 12px; }
</style>
