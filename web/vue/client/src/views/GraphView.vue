<!-- EduNexus UI direction: Atelier de progression — le graphe rend visible la structure des notions et leurs dépendances. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  Network,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  CircleDot,
} from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";
import type { GraphNode, GraphEdge } from "@/types";

interface CompetencyGraph {
  subject_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  built_at?: string;
}

const { state } = useLearningStore();
const { t } = usePreferences();

const graph = ref<CompetencyGraph | null>(null);
const loading = ref(true);
const building = ref(false);
const error = ref<string | null>(null);
const validatingNodeId = ref<string | null>(null);

/** ID de la matière courante issu du store d'apprentissage */
const subjectId = computed(() => state.data?.subject.id ?? null);

/** Charger le graphe depuis l'API */
async function fetchGraph() {
  if (!subjectId.value) return;
  loading.value = true;
  error.value = null;
  try {
    graph.value = await tutorApi.getGraph(subjectId.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Impossible de charger le graphe.";
    graph.value = null;
  } finally {
    loading.value = false;
  }
}

/** Construire / reconstruire le graphe */
async function buildGraph() {
  if (!subjectId.value) return;
  building.value = true;
  error.value = null;
  try {
    await tutorApi.buildGraph(subjectId.value);
    // Recharger le graphe complet après construction
    graph.value = await tutorApi.getGraph(subjectId.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Erreur lors de la construction du graphe.";
  } finally {
    building.value = false;
  }
}

/** Confirmer un nœud non validé */
async function validateNode(nodeId: string) {
  validatingNodeId.value = nodeId;
  try {
    await tutorApi.validateNode(nodeId);
    // Mettre à jour le nœud localement
    const node = graph.value?.nodes.find((n) => n.id === nodeId);
    if (node) node.validation_status = "confirmed";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Erreur lors de la confirmation du nœud.";
  } finally {
    validatingNodeId.value = null;
  }
}

/** Rechercher le nom d'un nœud par son ID */
function nodeName(id: string): string {
  return graph.value?.nodes.find((n) => n.id === id)?.name ?? id;
}

/** Ton du statut de validation */
function validationTone(status: GraphNode["validation_status"]): "green" | "orange" | "slate" {
  if (status === "confirmed") return "green";
  if (status === "rejected") return "slate";
  return "orange";
}

/** Libellé i18n du statut de validation */
function validationLabel(status: GraphNode["validation_status"]): string {
  if (status === "confirmed") return t("graph.confirmed");
  if (status === "rejected") return t("graph.rejected");
  return t("graph.unconfirmed");
}

onMounted(fetchGraph);
</script>

<template>
  <section class="page graph-page">
    <!-- En-tête de la page -->
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t("graph.kicker") }}</p>
        <h1>{{ t("graph.title") }}</h1>
        <p>{{ t("graph.copy") }}</p>
      </div>
    </header>

    <!-- État de chargement -->
    <section v-if="loading" class="loading-state">
      <p>{{ t("app.loadingSubject") }}</p>
    </section>

    <!-- Message d'erreur -->
    <section v-else-if="error" class="content-panel error-panel">
      <p>{{ error }}</p>
      <button type="button" class="secondary-action" @click="fetchGraph">
        <RefreshCw :size="16" aria-hidden="true" /> {{ t("graph.build") }}
      </button>
    </section>

    <!-- État vide : aucun graphe -->
    <section v-else-if="!graph || graph.nodes.length === 0" class="content-panel empty-state">
      <Network :size="48" aria-hidden="true" class="empty-icon" />
      <h2>{{ t("graph.empty") }}</h2>
      <p>{{ t("graph.emptyCopy") }}</p>
      <button
        type="button"
        class="primary-action"
        :disabled="building"
        @click="buildGraph"
      >
        <RefreshCw v-if="building" :size="16" aria-hidden="true" class="spin" />
        {{ building ? t("graph.building") : t("graph.build") }}
      </button>
    </section>

    <!-- Contenu du graphe -->
    <template v-else>
      <!-- Barre d'action -->
      <div class="graph-actions">
        <button
          type="button"
          class="secondary-action"
          :disabled="building"
          @click="buildGraph"
        >
          <RefreshCw v-if="building" :size="16" aria-hidden="true" class="spin" />
          {{ building ? t("graph.building") : t("graph.build") }}
        </button>
        <span v-if="graph.built_at" class="built-at">
          {{ t("graph.builtAt", { date: graph.built_at }) }}
        </span>
      </div>

      <!-- Section Nœuds -->
      <section class="content-panel graph-nodes-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ t("graph.nodes") }}</p>
            <h2>{{ t("graph.nodeTitle") }}</h2>
          </div>
          <StatusPill tone="indigo">{{ graph.nodes.length }}</StatusPill>
        </div>

        <div class="node-grid">
          <article
            v-for="node in graph.nodes"
            :key="node.id"
            class="node-card"
            :class="{ 'node-confirmed': node.validation_status === 'confirmed' }"
          >
            <div class="node-card-head">
              <h3>{{ node.name }}</h3>
              <StatusPill :tone="validationTone(node.validation_status)">
                {{ validationLabel(node.validation_status) }}
              </StatusPill>
            </div>

            <!-- Barre de maîtrise -->
            <div class="node-metrics">
              <div class="node-metric">
                <span class="metric-label">{{ t("graph.mastery") }}</span>
                <div class="metric-bar">
                  <span><i :style="{ width: `${node.mastery_score}%` }"></i></span>
                  <b>{{ node.mastery_score }}%</b>
                </div>
              </div>
              <div class="node-metric">
                <span class="metric-label">{{ t("graph.confidence") }}</span>
                <div class="metric-bar">
                  <span><i :style="{ width: `${node.confidence}%` }"></i></span>
                  <b>{{ node.confidence }}%</b>
                </div>
              </div>
            </div>

            <!-- Bouton Confirmer pour les nœuds non validés -->
            <button
              v-if="node.validation_status === 'unconfirmed'"
              type="button"
              class="primary-action validate-btn"
              :disabled="validatingNodeId === node.id"
              @click="validateNode(node.id)"
            >
              <ShieldCheck :size="16" aria-hidden="true" />
              {{ validatingNodeId === node.id ? t("graph.validating") : t("graph.validate") }}
            </button>
          </article>
        </div>
      </section>

      <!-- Section Relations (Arêtes) -->
      <section class="content-panel graph-edges-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ t("graph.edges") }}</p>
            <h2>{{ t("graph.edgeTitle") }}</h2>
          </div>
          <StatusPill tone="slate">{{ graph.edges.length }}</StatusPill>
        </div>

        <div v-if="graph.edges.length" class="edge-list">
          <article v-for="edge in graph.edges" :key="edge.id" class="edge-row">
            <div class="edge-source">
              <CircleDot :size="14" aria-hidden="true" />
              <strong>{{ nodeName(edge.source_id) }}</strong>
            </div>
            <ArrowRight :size="16" aria-hidden="true" class="edge-arrow" />
            <div class="edge-target">
              <Circle :size="14" aria-hidden="true" />
              <strong>{{ nodeName(edge.target_id) }}</strong>
            </div>
            <StatusPill tone="slate" class="edge-relation">{{ edge.relation }}</StatusPill>
          </article>
        </div>
        <p v-else class="empty-copy">{{ t("graph.empty") }}</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.graph-page .graph-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.graph-page .built-at {
  font-size: 0.82rem;
  opacity: 0.6;
}

/* ── Grille de nœuds ─────────────────────────────────── */
.node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
  padding-top: 1rem;
}

.node-card {
  background: var(--surface-raised, #fff);
  border: 1px solid var(--border, #e2e2e2);
  border-radius: var(--radius-lg, 12px);
  padding: 1rem 1.2rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.node-card.node-confirmed {
  border-color: var(--green-300, #86efac);
}

.node-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.node-card-head h3 {
  margin: 0;
  font-size: 1rem;
}

/* ── Métriques nœud ─────────────────────────────────── */
.node-metrics {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.node-metric {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.metric-label {
  font-size: 0.78rem;
  width: 72px;
  flex-shrink: 0;
  opacity: 0.7;
}

.metric-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex: 1;
}

.metric-bar span {
  flex: 1;
  height: 6px;
  background: var(--border, #e2e2e2);
  border-radius: 3px;
  overflow: hidden;
}

.metric-bar i {
  display: block;
  height: 100%;
  background: var(--indigo-500, #6366f1);
  border-radius: 3px;
}

.metric-bar b {
  font-size: 0.78rem;
  width: 38px;
  text-align: right;
}

/* ── Bouton valider ──────────────────────────────────── */
.validate-btn {
  align-self: flex-start;
  margin-top: 0.25rem;
}

/* ── Liste d'arêtes ──────────────────────────────────── */
.edge-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding-top: 1rem;
}

.edge-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0.8rem;
  background: var(--surface-raised, #fff);
  border: 1px solid var(--border, #e2e2e2);
  border-radius: var(--radius, 8px);
}

.edge-source,
.edge-target {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.edge-arrow {
  opacity: 0.4;
  flex-shrink: 0;
}

.edge-relation {
  margin-left: auto;
  font-size: 0.78rem;
}

/* ── État vide ───────────────────────────────────────── */
.empty-state {
  text-align: center;
  padding: 3rem 1.5rem;
}

.empty-icon {
  opacity: 0.3;
  margin-bottom: 1rem;
}

/* ── Spin animation ──────────────────────────────────── */
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.spin {
  animation: spin 1s linear infinite;
}
</style>
