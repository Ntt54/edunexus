<!-- EduNexus UI direction: Atelier de progression — le tableau de bord matière synthétise l'état du graphe de compétences. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  CheckCircle2,
  CircleAlert,
  CircleDot,
  HelpCircle,
  Network,
  RefreshCw,
} from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";
import type { GraphDashboard, GraphDashboardItem, ContradictoryEdge } from "@/types";

const { state } = useLearningStore();
const { t } = usePreferences();

const dashboard = ref<GraphDashboard | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);

/** ID de la matière courante issu du store */
const subjectId = computed(() => state.data?.subject.id ?? null);

/** Nombre max d'éléments affichés avant « + N autres » */
const MAX_VISIBLE = 12;

/** Charger le tableau de bord */
async function fetchDashboard() {
  if (!subjectId.value) return;
  loading.value = true;
  error.value = null;
  try {
    dashboard.value = await tutorApi.graphDashboard(subjectId.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Impossible de charger le tableau de bord.";
    dashboard.value = null;
  } finally {
    loading.value = false;
  }
}

/** Découpe une liste en éléments visibles + reste */
function sliced(items: GraphDashboardItem[]) {
  return {
    visible: items.slice(0, MAX_VISIBLE),
    remaining: items.length - MAX_VISIBLE,
  };
}

/** Données des 4 cartes metrics (sans contradictory — type différent) */
const cards = computed(() => {
  if (!dashboard.value) return [];
  const d = dashboard.value;
  return [
    {
      key: "covered",
      icon: CheckCircle2,
      tone: "green" as const,
      count: d.covered.length,
      label: t("subjectDash.covered"),
      desc: t("subjectDash.coveredDesc"),
      items: d.covered,
    },
    {
      key: "uncovered",
      icon: CircleDot,
      tone: "slate" as const,
      count: d.uncovered.length,
      label: t("subjectDash.uncovered"),
      desc: t("subjectDash.uncoveredDesc"),
      items: d.uncovered,
    },
    {
      key: "unconfirmed",
      icon: HelpCircle,
      tone: "indigo" as const,
      count: d.unconfirmed.length,
      label: t("subjectDash.unconfirmed"),
      desc: t("subjectDash.unconfirmedDesc"),
      items: d.unconfirmed,
    },
  ];
});

onMounted(fetchDashboard);
</script>

<template>
  <section class="page subject-dashboard-page">
    <!-- En-tête -->
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t("subjectDash.kicker") }}</p>
        <h1>{{ t("subjectDash.title") }}</h1>
        <p>{{ t("subjectDash.copy") }}</p>
      </div>
    </header>

    <!-- Chargement -->
    <section v-if="loading" class="loading-state">
      <p>{{ t("app.loadingSubject") }}</p>
    </section>

    <!-- Erreur -->
    <section v-else-if="error" class="content-panel error-panel">
      <p>{{ error }}</p>
      <button type="button" class="secondary-action" @click="fetchDashboard">
        <RefreshCw :size="16" aria-hidden="true" /> {{ t("graph.build") }}
      </button>
    </section>

    <!-- État vide -->
    <section v-else-if="!cards.length" class="content-panel empty-state">
      <Network :size="48" aria-hidden="true" class="empty-icon" />
      <h2>{{ t("subjectDash.empty") }}</h2>
      <p>{{ t("subjectDash.emptyCopy") }}</p>
    </section>

    <!-- Contenu du tableau de bord -->
    <template v-else>
      <!-- Grille de métriques -->
      <section class="metric-grid" aria-label="Vue d'ensemble de la matière">
        <article
          v-for="card in cards"
          :key="card.key"
          class="metric-card"
        >
          <div class="metric-icon" :class="card.tone">
            <component :is="card.icon" :size="19" aria-hidden="true" />
          </div>
          <div>
            <span>{{ card.label }}</span>
            <strong>{{ card.count }}</strong>
            <small>{{ card.desc }}</small>
          </div>
        </article>
      </section>

      <!-- Détails par catégorie -->
      <div class="dashboard-columns">
        <section
          v-for="card in cards"
          :key="card.key"
          class="content-panel category-panel"
        >
          <div class="panel-heading">
            <div>
              <p class="eyebrow">{{ card.label }}</p>
              <h2>{{ card.count }} {{ card.label.toLowerCase() }}</h2>
            </div>
            <component :is="card.icon" :size="20" aria-hidden="true" />
          </div>

          <ul v-if="card.items.length" class="category-list">
            <li
              v-for="item in sliced(card.items).visible"
              :key="item.id"
              class="category-item"
            >
              <strong>{{ item.title }}</strong>
              <span v-if="item.mastery_score > 0">{{ Math.round(item.mastery_score * 100) }}%</span>
            </li>
          </ul>

          <p v-if="sliced(card.items).remaining > 0" class="others-label">
            {{ t("subjectDash.others", { count: sliced(card.items).remaining }) }}
          </p>

          <p v-if="!card.items.length" class="empty-copy">
            {{ t("subjectDash.empty") }}
          </p>
        </section>
      </div>

      <!-- Section contradictory (type différent : ContradictoryEdge[]) -->
      <section v-if="dashboard && dashboard.contradictory.length" class="content-panel category-panel" style="margin-top: 1rem;">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ t("subjectDash.contradictory") }}</p>
            <h2>{{ dashboard.contradictory.length }} {{ t("subjectDash.contradictory").toLowerCase() }}</h2>
          </div>
          <CircleAlert :size="20" aria-hidden="true" />
        </div>
        <ul class="category-list">
          <li v-for="(edge, idx) in dashboard.contradictory.slice(0, MAX_VISIBLE)" :key="idx" class="category-item">
            <strong>{{ edge.source }} → {{ edge.target }}</strong>
            <span>{{ edge.relation }}</span>
          </li>
        </ul>
        <p v-if="dashboard.contradictory.length > MAX_VISIBLE" class="others-label">
          {{ t("subjectDash.others", { count: dashboard.contradictory.length - MAX_VISIBLE }) }}
        </p>
      </section>
    </template>
  </section>
</template>

<style scoped>
/* ── Grille de métriques ──────────────────────────────── */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.metric-card {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  background: var(--surface-raised, #fff);
  border: 1px solid var(--border, #e2e2e2);
  border-radius: var(--radius-lg, 12px);
  padding: 1rem 1.2rem;
}

.metric-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius, 8px);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.metric-icon.green {
  background: var(--green-50, #f0fdf4);
  color: var(--green-600, #16a34a);
}

.metric-icon.slate {
  background: var(--slate-50, #f8fafc);
  color: var(--slate-500, #64748b);
}

.metric-icon.orange {
  background: var(--orange-50, #fff7ed);
  color: var(--orange-600, #ea580c);
}

.metric-icon.indigo {
  background: var(--indigo-50, #eef2ff);
  color: var(--indigo-600, #4f46e5);
}

.metric-card div {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.metric-card span {
  font-size: 0.82rem;
  opacity: 0.7;
}

.metric-card strong {
  font-size: 1.4rem;
}

.metric-card small {
  font-size: 0.78rem;
  opacity: 0.6;
}

/* ── Colonnes de détail ────────────────────────────────── */
.dashboard-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1rem;
}

.category-panel {
  padding: 1.2rem;
}

/* ── Liste de catégories ──────────────────────────────── */
.category-list {
  list-style: none;
  padding: 0;
  margin: 0.75rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.category-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.45rem 0.65rem;
  background: var(--surface, #fafafa);
  border-radius: var(--radius, 6px);
}

.category-item strong {
  font-size: 0.88rem;
  font-weight: 500;
}

.category-item span {
  font-size: 0.78rem;
  opacity: 0.55;
}

.others-label {
  margin-top: 0.5rem;
  font-size: 0.82rem;
  opacity: 0.6;
  text-align: center;
}

/* ── État vide ─────────────────────────────────────────── */
.empty-state {
  text-align: center;
  padding: 3rem 1.5rem;
}

.empty-icon {
  opacity: 0.3;
  margin-bottom: 1rem;
}
</style>
