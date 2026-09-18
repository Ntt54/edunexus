<!-- 012 US2 (FR-008) — Suivi parent : temps, maîtrise, erreurs, jalon (lecture seule, token). -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Clock3, Eye, Loader2, RotateCcw, TrendingUp, TriangleAlert, Users } from "lucide-vue-next";
import { usePreferences } from "@/stores/preferences";
import { tutorApi, invalidateSubjectCaches } from "@/services/api";

const { locale } = usePreferences();

// ── 012 · Messages inline fr/en ────────────────────────────────────
const msg = computed(() => locale.value === "fr" ? {
  kicker: "Suivi parent",
  title: "Espace parent",
  copy: "La progression réelle de votre enfant, en lecture seule. Le partage est accordé par l’apprenant et révocable à tout moment.",
  tokenLabel: "Jeton de partage",
  tokenPlaceholder: "Collez le jeton transmis par l’apprenant…",
  load: "Voir la progression",
  loading: "Chargement…",
  retry: "Réessayer",
  error: "Jeton invalide ou partage révoqué.",
  time: "Temps d’apprentissage (7 j)",
  minutes: "min",
  mastery: "Maîtrise par matière",
  noMastery: "Pas encore de données de maîtrise.",
  errors: "Erreurs fréquentes",
  noErrors: "Aucune erreur récurrente.",
  occurrences: "fois",
  milestone: "Prochain jalon",
  noMilestone: "Aucun jalon planifié pour l’instant.",
} : {
  kicker: "Parent follow-up",
  title: "Parent space",
  copy: "Your child's real progress, read-only. Sharing is granted by the learner and revocable at any time.",
  tokenLabel: "Sharing token",
  tokenPlaceholder: "Paste the token shared by the learner…",
  load: "View progress",
  loading: "Loading…",
  retry: "Retry",
  error: "Invalid token or revoked sharing.",
  time: "Learning time (7 d)",
  minutes: "min",
  mastery: "Mastery by subject",
  noMastery: "No mastery data yet.",
  errors: "Frequent mistakes",
  noErrors: "No recurring mistakes.",
  occurrences: "times",
  milestone: "Next milestone",
  noMilestone: "No milestone planned yet.",
});

interface ParentOverview {
  temps_semaine_min: number;
  maitrise: Record<string, number>;
  erreurs_frequentes: Array<{ concept: string; occurrences: number }>;
  prochain_jalon: string;
}

const TOKEN_KEY = "edunexus.parentToken";
const token = ref("");
const overview = ref<ParentOverview | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const masteryEntries = computed(() => Object.entries(overview.value?.maitrise ?? {}));

async function load() {
  const t = token.value.trim();
  if (!t) { error.value = msg.value.error; return; }
  loading.value = true; error.value = null; overview.value = null;
  try {
    try { localStorage.setItem(TOKEN_KEY, t); } catch { /* not persistent */ }
    const res = await tutorApi.getParentOverview(t);
    overview.value = res as unknown as ParentOverview;
    invalidateSubjectCaches();
  } catch {
    error.value = msg.value.error;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  try { token.value = localStorage.getItem(TOKEN_KEY) || ""; } catch { /* ignore */ }
  if (token.value.trim()) void load();
});
</script>

<template>
  <section class="page parent-page reading-surface">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ msg.kicker }}</p>
        <h1>{{ msg.title }}</h1>
        <p>{{ msg.copy }}</p>
      </div>
      <div class="subject-token"><Users :size="18" aria-hidden="true" /><span>{{ msg.title }}</span></div>
    </header>

    <article class="content-panel token-panel">
      <label class="token-field">
        <span>{{ msg.tokenLabel }}</span>
        <input v-model="token" type="password" :placeholder="msg.tokenPlaceholder" autocomplete="off" @keydown.enter="load" />
      </label>
      <button type="button" class="primary-action" :disabled="loading" @click="load">
        <Loader2 v-if="loading" :size="17" class="spin" aria-hidden="true" />
        <Eye v-else :size="17" aria-hidden="true" />
        {{ loading ? msg.loading : msg.load }}
      </button>
      <p v-if="error" class="field-error" role="alert">{{ error }}
        <button type="button" class="secondary-action" @click="load"><RotateCcw :size="14" aria-hidden="true" /> {{ msg.retry }}</button>
      </p>
    </article>

    <template v-if="overview">
      <div class="parent-grid">
        <article class="content-panel stat-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ msg.time }}</p>
              <h2>{{ overview.temps_semaine_min }} {{ msg.minutes }}</h2>
            </div>
            <Clock3 :size="21" aria-hidden="true" />
          </div>
        </article>

        <article class="content-panel stat-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ msg.milestone }}</p>
              <h2>{{ overview.prochain_jalon || msg.noMilestone }}</h2>
            </div>
            <TrendingUp :size="21" aria-hidden="true" />
          </div>
        </article>
      </div>

      <article class="content-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ msg.mastery }}</p>
            <h2>{{ masteryEntries.length }}</h2>
          </div>
        </div>
        <ul v-if="masteryEntries.length" class="mastery-list">
          <li v-for="[name, score] in masteryEntries" :key="name" class="mastery-row">
            <span>{{ name }}</span>
            <span class="mastery-bar"><i :style="{ width: Math.max(0, Math.min(100, score)) + '%' }"></i></span>
            <strong>{{ score }} %</strong>
          </li>
        </ul>
        <p v-else class="muted">{{ msg.noMastery }}</p>
      </article>

      <article class="content-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ msg.errors }}</p>
            <h2>{{ overview.erreurs_frequentes.length }}</h2>
          </div>
          <TriangleAlert :size="21" aria-hidden="true" />
        </div>
        <ul v-if="overview.erreurs_frequentes.length" class="errors-list">
          <li v-for="e in overview.erreurs_frequentes" :key="e.concept" class="error-row">
            <span>{{ e.concept }}</span>
            <span class="status-pill status-pill--orange">{{ e.occurrences }} {{ msg.occurrences }}</span>
          </li>
        </ul>
        <p v-else class="muted">{{ msg.noErrors }}</p>
      </article>
    </template>
  </section>
</template>

<style scoped>
.parent-page { display: grid; gap: 16px; }
.token-panel { display: grid; gap: 12px; }
.token-field { display: grid; gap: 4px; font-size: 12px; font-weight: 700; color: var(--muted); }
.parent-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.stat-card h2 { font-size: 20px; margin: 0; }
.mastery-list, .errors-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.mastery-row { display: grid; grid-template-columns: 1fr 2fr auto; gap: 10px; align-items: center; font-size: 13.5px; }
.mastery-bar { display: block; height: 8px; border-radius: 999px; background: var(--line); overflow: hidden; }
.mastery-bar i { display: block; height: 100%; background: var(--indigo, #4f46e5); }
.error-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 13.5px; padding: 8px 0; border-bottom: 1px solid var(--line); }
.muted { color: var(--muted); font-size: 13px; }
.field-error { color: #b42318; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
@media (max-width: 720px) { .parent-grid { grid-template-columns: 1fr; } }
</style>
