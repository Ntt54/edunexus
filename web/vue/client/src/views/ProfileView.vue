<!-- EduNexus UI direction: Atelier de progression — le profil pédagogique ancre la personnalisation dans des choix explicites. -->
<script setup lang="ts">
import { computed, onMounted, ref, reactive } from "vue";
import { BookOpen, Check, Lightbulb, LoaderCircle, Wand2 } from "lucide-vue-next";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state: learningState } = useLearningStore();
const { t } = usePreferences();

/** ID de la matière active depuis le store partagé */
const subjectId = computed(() => learningState.data?.subject?.id ?? null);

/* ── Types ──────────────────────────────────────────────────────────── */

interface PedagogicalTemplate {
  id: string;
  name: string;
  domain: string;
  level: string;
  objective: string;
  explanation_style: string;
  activities: string[];
  mastery_criteria: string[];
}

interface SubjectProfile {
  domain: string;
  level: string;
  objective: string;
  deadline: string;
  available_time: string;
  explanation_style: string;
  activities: string[];
  mastery_criteria: string[];
}

/* ── État local ─────────────────────────────────────────────────────── */

const templates = ref<PedagogicalTemplate[]>([]);
const loadingTemplates = ref(false);
const loadingProfile = ref(false);
const saving = ref(false);
const interpreting = ref(false);
const error = ref<string | null>(null);
const savedNotice = ref(false);

const form = reactive<SubjectProfile>({
  domain: "",
  level: "",
  objective: "",
  deadline: "",
  available_time: "",
  explanation_style: "",
  activities: [],
  mastery_criteria: [],
});

/** Champs textarea bruts — une ligne par item */
const activitiesRaw = ref("");
const criteriaRaw = ref("");

/** Synchronise les textareas vers le tableau */
function syncArraysFromRaw() {
  form.activities = activitiesRaw.value.split("\n").map((l) => l.trim()).filter(Boolean);
  form.mastery_criteria = criteriaRaw.value.split("\n").map((l) => l.trim()).filter(Boolean);
}

/** Affiche un tableau dans le textarea correspondant */
function loadArraysToRaw(profile: SubjectProfile) {
  activitiesRaw.value = (profile.activities ?? []).join("\n");
  criteriaRaw.value = (profile.mastery_criteria ?? []).join("\n");
}

/* ── Appels API ─────────────────────────────────────────────────────── */

const apiBase = import.meta.env.VITE_EDUNEXUS_API_BASE ?? "/api/tutor";

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok && response.status !== 404) throw new Error(`Erreur API ${response.status}`);
  if (response.status === 404) return null as T;
  return response.json() as Promise<T>;
}

/** Charge les templates pédagogiques */
async function fetchTemplates() {
  loadingTemplates.value = true;
  try {
    templates.value = await apiRequest<PedagogicalTemplate[]>("/pedagogical-templates");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de charger les templates.";
  } finally {
    loadingTemplates.value = false;
  }
}

/** Charge le profil existant de la matière */
async function fetchProfile(id: string) {
  loadingProfile.value = true;
  error.value = null;
  try {
    const profile = await apiRequest<SubjectProfile>(`/subjects/${id}/profile`);
    if (profile) {
      form.domain = profile.domain ?? "";
      form.level = profile.level ?? "";
      form.objective = profile.objective ?? "";
      form.deadline = profile.deadline ?? "";
      form.available_time = profile.available_time ?? "";
      form.explanation_style = profile.explanation_style ?? "";
      form.activities = profile.activities ?? [];
      form.mastery_criteria = profile.mastery_criteria ?? [];
      loadArraysToRaw(form);
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de charger le profil.";
  } finally {
    loadingProfile.value = false;
  }
}

/** Applique un template au formulaire */
function applyTemplate(template: PedagogicalTemplate) {
  form.domain = template.domain ?? "";
  form.level = template.level ?? "";
  form.objective = template.objective ?? "";
  form.explanation_style = template.explanation_style ?? "";
  form.activities = template.activities ?? [];
  form.mastery_criteria = template.mastery_criteria ?? [];
  loadArraysToRaw(form);
}

/** Enregistre le profil */
async function saveProfile() {
  const id = subjectId.value;
  if (!id) return;
  syncArraysFromRaw();
  saving.value = true;
  error.value = null;
  savedNotice.value = false;
  try {
    await apiRequest(`/subjects/${id}/profile`, {
      method: "PUT",
      body: JSON.stringify({ ...form }),
    });
    savedNotice.value = true;
    setTimeout(() => (savedNotice.value = false), 3000);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible d'enregistrer le profil.";
  } finally {
    saving.value = false;
  }
}

/** Interprète un objectif pour pré-remplir niveau, activités et critères */
async function interpretGoal() {
  const id = subjectId.value;
  if (!id || !form.objective.trim()) return;
  interpreting.value = true;
  error.value = null;
  try {
    const result = await apiRequest<{ level: string; activities: string[]; mastery_criteria: string[] }>(
      `/subjects/${id}/profile/interpret-goal`,
      { method: "POST", body: JSON.stringify({ goal: form.objective }) },
    );
    if (result) {
      if (result.level) form.level = result.level;
      if (result.activities?.length) {
        form.activities = result.activities;
        activitiesRaw.value = result.activities.join("\n");
      }
      if (result.mastery_criteria?.length) {
        form.mastery_criteria = result.mastery_criteria;
        criteriaRaw.value = result.mastery_criteria.join("\n");
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible d'interpréter l'objectif.";
  } finally {
    interpreting.value = false;
  }
}

/* ── Cycle de vie ───────────────────────────────────────────────────── */

onMounted(() => {
  fetchTemplates();
  if (subjectId.value) fetchProfile(subjectId.value);
});
</script>

<template>
  <!-- État vide : aucune matière sélectionnée -->
  <section v-if="!subjectId" class="page profile-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('profile.kicker') }}</p>
        <h1>{{ t('profile.title') }}</h1>
        <p>{{ t('profile.emptySubject') }}</p>
      </div>
      <div class="empty-icon"><BookOpen :size="48" aria-hidden="true" /></div>
    </header>
  </section>

  <!-- Vue principale -->
  <section v-else class="page profile-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('profile.kicker') }}</p>
        <h1>{{ t('profile.title') }}</h1>
        <p>{{ t('profile.context', { subject: learningState.data?.subject?.name ?? '' }) }}</p>
      </div>
    </header>

    <!-- Message d'erreur -->
    <p v-if="error" class="error-notice">{{ error }}</p>

    <!-- Templates pédagogiques -->
    <section class="content-panel templates-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('profile.templates') }}</p>
          <h2>{{ t('profile.templatesTitle') }}</h2>
        </div>
        <LoaderCircle v-if="loadingTemplates" :size="18" class="spin" aria-hidden="true" />
      </div>
      <div v-if="templates.length" class="template-grid">
        <button
          v-for="tpl in templates"
          :key="tpl.id"
          type="button"
          class="template-card"
          @click="applyTemplate(tpl)"
        >
          <Lightbulb :size="18" aria-hidden="true" />
          <strong>{{ tpl.name }}</strong>
          <span>{{ tpl.domain }} · {{ tpl.level }}</span>
        </button>
      </div>
      <p v-else-if="!loadingTemplates" class="empty-copy">{{ t('profile.noTemplates') }}</p>
    </section>

    <!-- Formulaire du profil pédagogique -->
    <article class="content-panel profile-form">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('profile.form') }}</p>
          <h2>{{ t('profile.formTitle') }}</h2>
        </div>
      </div>

      <div v-if="loadingProfile" class="loading-inline">
        <LoaderCircle :size="18" class="spin" aria-hidden="true" />
        <span>{{ t('profile.loadingProfile') }}</span>
      </div>

      <form v-else @submit.prevent="saveProfile" class="form-grid">
        <!-- Domaine -->
        <label class="form-field">
          <span>{{ t('profile.domain') }}</span>
          <input v-model="form.domain" type="text" :placeholder="t('profile.domainPlaceholder')" />
        </label>

        <!-- Niveau -->
        <label class="form-field">
          <span>{{ t('profile.level') }}</span>
          <input v-model="form.level" type="text" :placeholder="t('profile.levelPlaceholder')" />
        </label>

        <!-- Objectif -->
        <label class="form-field full-width">
          <span>{{ t('profile.objective') }}</span>
          <textarea v-model="form.objective" rows="3" :placeholder="t('profile.objectivePlaceholder')" />
        </label>

        <!-- Bouton Interpréter un objectif -->
        <div class="interpret-row">
          <button
            type="button"
            class="secondary-action"
            :disabled="interpreting || !form.objective.trim()"
            @click="interpretGoal"
          >
            <Wand2 :size="17" aria-hidden="true" />
            {{ interpreting ? t('profile.interpreting') : t('profile.interpretGoal') }}
          </button>
        </div>

        <!-- Date limite -->
        <label class="form-field">
          <span>{{ t('profile.deadline') }}</span>
          <input v-model="form.deadline" type="text" :placeholder="t('profile.deadlinePlaceholder')" />
        </label>

        <!-- Temps disponible -->
        <label class="form-field">
          <span>{{ t('profile.availableTime') }}</span>
          <input v-model="form.available_time" type="text" :placeholder="t('profile.availableTimePlaceholder')" />
        </label>

        <!-- Style d'explication -->
        <label class="form-field full-width">
          <span>{{ t('profile.explanationStyle') }}</span>
          <input v-model="form.explanation_style" type="text" :placeholder="t('profile.explanationStylePlaceholder')" />
        </label>

        <!-- Activités -->
        <label class="form-field full-width">
          <span>{{ t('profile.activities') }}</span>
          <textarea v-model="activitiesRaw" rows="4" :placeholder="t('profile.activitiesPlaceholder')" @blur="syncArraysFromRaw" />
        </label>

        <!-- Critères de maîtrise -->
        <label class="form-field full-width">
          <span>{{ t('profile.masteryCriteria') }}</span>
          <textarea v-model="criteriaRaw" rows="4" :placeholder="t('profile.masteryCriteriaPlaceholder')" @blur="syncArraysFromRaw" />
        </label>

        <!-- Bouton enregistrer -->
        <div class="form-actions">
          <button type="submit" class="primary-action" :disabled="saving">
            <Check :size="17" aria-hidden="true" />
            {{ saving ? t('profile.saving') : savedNotice ? t('profile.saved') : t('profile.save') }}
          </button>
        </div>
      </form>
    </article>
  </section>
</template>
