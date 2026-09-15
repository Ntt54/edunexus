<!-- EduNexus UI direction: Atelier de progression — la gestion des apprenants rend l'activité collective visible et simple. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Check, LoaderCircle, Plus, Trash2, User } from "lucide-vue-next";
import { tutorApi, invalidateSubjectCaches } from "@/services/api";
import { usePreferences } from "@/stores/preferences";

const { t, activeSubjectId, activeLearnerId, setActiveLearnerId } = usePreferences();

interface Learner {
  id: string;
  name: string;
  avatar?: string;
  created_at: string;
  is_active?: boolean;
}

const learners = ref<Learner[]>([]);
const loading = ref(false);
const creating = ref(false);
const error = ref<string | null>(null);
const newName = ref("");
const subjectName = ref("");
let learnersGen = 0;

// Confirm delete modal (reuse pattern AppShell)
const showDelete = ref(false);
const deleteTarget = ref<Learner | null>(null);

const activeSubject = computed(() => activeSubjectId.value || localStorage.getItem("edunexus.space") || localStorage.getItem("edunexus:subject") || "");
const activeLearner = computed(() => activeLearnerId.value || localStorage.getItem("edunexus.learner") || "");

async function fetchLearners() {
  const sid = (activeSubject.value || "").trim();
  const gen = ++learnersGen;
  // garde: si pas de matière, pas de fetch 400 — vide local immédiat (évite loop + blink)
  if (!sid) {
    learners.value = [];
    loading.value = false;
    subjectName.value = "";
    error.value = null;
    return;
  }
  // No-flash: clear immediately on subject switch
  learners.value = [];
  loading.value = true;
  error.value = null;
  async function fetchLearnersOnce(retried = false): Promise<{ learners: unknown[] }> {
    try {
      return await tutorApi.getLearnersFiltered(sid) as unknown as { learners: unknown[] };
    } catch (e) {
      const st = (e as { status?: number }).status;
      if (st === 400 || st === 404) return { learners: [] };
      if (!retried) { await new Promise(r => setTimeout(r, 700)); return fetchLearnersOnce(true); }
      throw e;
    }
  }
  try {
    const [data, subjRes] = await Promise.all([
      fetchLearnersOnce().catch(() => ({ learners: [] as unknown[] })),
      tutorApi.getSubjects().catch(() => ({ subjects: [] as Array<{ id: string; name: string }>, active_id: null })),
    ]);
    if (gen !== learnersGen) return;
    const list = (data.learners ?? []) as unknown as Learner[];
    // mark active
    const al = activeLearner.value;
    learners.value = list.map(l => ({ ...l, is_active: l.id === al }));
    const found = (subjRes.subjects || []).find(s => s.id === sid);
    subjectName.value = found?.name || "";
  } catch (e) {
    if (gen !== learnersGen) return;
    error.value = e instanceof Error ? e.message : "Impossible de charger les apprenants.";
    learners.value = [];
  } finally {
    if (gen === learnersGen) loading.value = false;
  }
}

async function createLearner() {
  const name = newName.value.trim();
  if (!name) { error.value = "Nom requis (1..32 caractères)"; return; }
  if (name.length < 1 || name.length > 32) { error.value = "Nom invalide : 1 à 32 caractères"; return; }
  if (!activeSubject.value) { error.value = "Aucune matière active"; return; }
  creating.value = true;
  error.value = null;
  try {
    await tutorApi.createLearner(name);
    // backend does not auto-scope by subject_id for create, but list filtered will show if coupled via paths; for 011 spec, create is for active subject
    newName.value = "";
    invalidateSubjectCaches();
    await fetchLearners();
  } catch (e) {
    const st = (e as { status?: number }).status;
    const msg = e instanceof Error ? e.message : String(e);
    if (st === 400 || msg.includes("déjà utilisé") || msg.includes("existe")) error.value = "Nom déjà utilisé ou invalide (1..32)";
    else error.value = msg || "Impossible de créer l'apprenant.";
  } finally { creating.value = false; }
}

async function activateLearner(learnerId: string) {
  error.value = null;
  try {
    await tutorApi.activateLearner(learnerId);
    setActiveLearnerId(learnerId);
    for (const l of learners.value) l.is_active = l.id === learnerId;
    invalidateSubjectCaches();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible d'activer l'apprenant.";
  }
}

function askDelete(learner: Learner) {
  deleteTarget.value = learner;
  showDelete.value = true;
}
async function confirmDelete() {
  if (!deleteTarget.value) return;
  const id = deleteTarget.value.id;
  const wasActive = deleteTarget.value.is_active;
  error.value = null;
  try {
    await tutorApi.deleteLearner(id);
    learners.value = learners.value.filter(l => l.id !== id);
    if (wasActive) {
      const next = learners.value[0];
      if (next) {
        setActiveLearnerId(next.id);
        for (const l of learners.value) l.is_active = l.id === next.id;
      } else {
        setActiveLearnerId("");
      }
    }
    invalidateSubjectCaches();
    showDelete.value = false;
    deleteTarget.value = null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de supprimer l'apprenant.";
    showDelete.value = false;
  }
}

function onSubjectChangeLearners() { void fetchLearners(); }
function onLearnerChangeLearners() {
  const al = activeLearner.value;
  for (const l of learners.value) l.is_active = l.id === al;
}

watch(() => activeSubject.value, () => onSubjectChangeLearners());
watch(() => activeLearner.value, () => onLearnerChangeLearners());

onMounted(() => {
  void fetchLearners();
  window.addEventListener("edunexus:subjectChange", onSubjectChangeLearners as EventListener);
  window.addEventListener("subjectChange", onSubjectChangeLearners as EventListener);
  window.addEventListener("edunexus:learnerChange", onLearnerChangeLearners as EventListener);
  window.addEventListener("learnerChange", onLearnerChangeLearners as EventListener);
});
onUnmounted(() => {
  window.removeEventListener("edunexus:subjectChange", onSubjectChangeLearners as EventListener);
  window.removeEventListener("subjectChange", onSubjectChangeLearners as EventListener);
  window.removeEventListener("edunexus:learnerChange", onLearnerChangeLearners as EventListener);
  window.removeEventListener("learnerChange", onLearnerChangeLearners as EventListener);
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
      <div class="subject-token">
        <span>{{ t('subject.active') }}</span>
        <strong>{{ subjectName || '—' }}</strong>
      </div>
    </header>

    <p v-if="error" class="error-notice">{{ error }}</p>

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
            maxlength="32"
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
      <p style="margin:8px 0 0;color:var(--faint);font-size:11px">1 à 32 caractères — matière active : {{ subjectName || '—' }}</p>
    </article>

    <section class="content-panel learner-list">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('learners.list') }}</p>
          <h2>{{ t('learners.listTitle', { count: learners.length }) }}</h2>
        </div>
        <LoaderCircle v-if="loading" :size="18" class="spin" aria-hidden="true" />
      </div>

      <div v-if="loading" class="loading-inline">
        <LoaderCircle :size="18" class="spin" aria-hidden="true" />
        <span>{{ t('learners.loading') }}</span>
      </div>

      <p v-else-if="!learners.length" class="empty-copy">{{ t('learners.empty') }}</p>

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
              @click="askDelete(learner)"
            >
              <Trash2 :size="16" aria-hidden="true" />
            </button>
          </div>
        </article>
      </div>
    </section>

    <!-- Confirm delete (FR-005) -->
    <div v-if="showDelete" class="modal-overlay" @click.self="showDelete=false" role="dialog" aria-modal="true">
      <div class="modal-panel content-panel" style="width:min(420px,92vw);padding:22px">
        <h3 style="margin:0 0 8px">Supprimer l'apprenant ?</h3>
        <p style="margin:0 0 12px;color:var(--muted)">« {{ deleteTarget?.name }} » sera supprimé.</p>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="secondary-action" @click="showDelete=false">Annuler</button>
          <button class="primary-action" style="background:#b42318" @click="confirmDelete">Supprimer</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.modal-overlay{position:fixed;inset:0;z-index:100;display:grid;place-items:center;background:rgba(15,15,25,.45);backdrop-filter:blur(4px)}
.modal-panel{border-radius:16px;background:#fff;box-shadow:0 24px 80px rgba(0,0,0,.18)}
.error-notice{margin:12px 0;color:#b42318;background:#fef3f2;border:1px solid #fecaca;border-radius:10px;padding:8px 12px;font-size:13px}
</style>
