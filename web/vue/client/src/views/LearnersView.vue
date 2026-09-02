<!-- EduNexus UI direction: Atelier de progression — la gestion des apprenants rend l'activité collective visible et simple. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, LoaderCircle, Plus, Trash2, User } from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state: learningState } = useLearningStore();
const { t } = usePreferences();

/** ID de la matière active depuis le store partagé */
const subjectId = computed(() => learningState.data?.subject?.id ?? null);

/* ── Types ──────────────────────────────────────────────────────────── */

interface Learner {
  id: string;
  name: string;
  avatar?: string;
  created_at: string;
  is_active?: boolean;
}

/* ── État local ─────────────────────────────────────────────────────── */

const learners = ref<Learner[]>([]);
const loading = ref(false);
const creating = ref(false);
const error = ref<string | null>(null);
const newName = ref("");

const apiBase = import.meta.env.VITE_EDUNEXUS_API_BASE ?? "/api/tutor";

/* ── Appels API ─────────────────────────────────────────────────────── */

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) throw new Error(`Erreur API ${response.status}`);
  if (response.status === 204) return null as T;
  return response.json() as Promise<T>;
}

/** Charge la liste des apprenants */
async function fetchLearners() {
  loading.value = true;
  error.value = null;
  try {
    const data = await apiRequest<{ learners: Learner[] }>("/learners");
    learners.value = data.learners ?? [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de charger les apprenants.";
  } finally {
    loading.value = false;
  }
}

/** Crée un nouvel apprenant */
async function createLearner() {
  const name = newName.value.trim();
  if (!name) return;
  creating.value = true;
  error.value = null;
  try {
    const created = await apiRequest<Learner>("/learners", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    if (created) learners.value.push(created);
    newName.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de créer l'apprenant.";
  } finally {
    creating.value = false;
  }
}

/** Active un apprenant */
async function activateLearner(learnerId: string) {
  error.value = null;
  try {
    await apiRequest(`/learners/${learnerId}/activate`, { method: "POST" });
    /* Met à jour localement le statut actif */
    for (const l of learners.value) l.is_active = l.id === learnerId;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible d'activer l'apprenant.";
  }
}

/** Supprime un apprenant */
async function deleteLearner(learnerId: string) {
  error.value = null;
  try {
    await apiRequest(`/learners/${learnerId}`, { method: "DELETE" });
    learners.value = learners.value.filter((l) => l.id !== learnerId);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de supprimer l'apprenant.";
  }
}

/* ── Cycle de vie ───────────────────────────────────────────────────── */

onMounted(() => {
  fetchLearners();
});
</script>

<template>
  <section class="page learners-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('learners.kicker') }}</p>
        <h1>{{ t('learners.title') }}</h1>
        <p>{{ t('learners.context') }}</p>
      </div>
      <div class="subject-token" v-if="subjectId">
        <span>{{ t('subject.active') }}</span>
        <strong>{{ learningState.data?.subject?.name ?? '' }}</strong>
      </div>
    </header>

    <!-- Message d'erreur -->
    <p v-if="error" class="error-notice">{{ error }}</p>

    <!-- Formulaire de création -->
    <article class="content-panel create-learner-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('learners.create') }}</p>
          <h2>{{ t('learners.createTitle') }}</h2>
        </div>
      </div>
      <form class="create-learner-form" @submit.prevent="createLearner">
        <div class="search-field">
          <User :size="18" aria-hidden="true" />
          <input
            v-model="newName"
            type="text"
            :placeholder="t('learners.namePlaceholder')"
            :aria-label="t('learners.namePlaceholder')"
            :disabled="creating"
          />
        </div>
        <button type="submit" class="primary-action" :disabled="creating || !newName.trim()">
          <Plus :size="17" aria-hidden="true" />
          {{ creating ? t('learners.creating') : t('learners.addButton') }}
        </button>
      </form>
    </article>

    <!-- Liste des apprenants -->
    <section class="content-panel learner-list">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('learners.list') }}</p>
          <h2>{{ t('learners.listTitle', { count: learners.length }) }}</h2>
        </div>
        <LoaderCircle v-if="loading" :size="18" class="spin" aria-hidden="true" />
      </div>

      <!-- État de chargement -->
      <div v-if="loading" class="loading-inline">
        <LoaderCircle :size="18" class="spin" aria-hidden="true" />
        <span>{{ t('learners.loading') }}</span>
      </div>

      <!-- État vide -->
      <p v-else-if="!learners.length" class="empty-copy">{{ t('learners.empty') }}</p>

      <!-- Cartes apprenants -->
      <div v-else class="learner-grid">
        <article
          v-for="learner in learners"
          :key="learner.id"
          class="learner-card"
          :class="{ active: learner.is_active }"
        >
          <div class="learner-avatar">
            <img v-if="learner.avatar" :src="learner.avatar" :alt="learner.name" />
            <User v-else :size="28" aria-hidden="true" />
          </div>
          <div class="learner-info">
            <div class="learner-name-row">
              <strong>{{ learner.name }}</strong>
              <span v-if="learner.is_active" class="active-badge">{{ t('learners.activeBadge') }}</span>
            </div>
            <small>{{ t('learners.createdAt', { date: learner.created_at }) }}</small>
          </div>
          <div class="learner-actions">
            <button
              v-if="!learner.is_active"
              type="button"
              class="secondary-action"
              @click="activateLearner(learner.id)"
            >
              <Check :size="16" aria-hidden="true" />
              {{ t('learners.activate') }}
            </button>
            <button
              type="button"
              class="text-button danger"
              :aria-label="t('learners.deleteAria', { name: learner.name })"
              @click="deleteLearner(learner.id)"
            >
              <Trash2 :size="16" aria-hidden="true" />
            </button>
          </div>
        </article>
      </div>
    </section>
  </section>
</template>
