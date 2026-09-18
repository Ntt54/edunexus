<!-- EduNexus UI direction: Atelier de progression — les rappels dus sont visibles en <10 s, sans jargon. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ArrowRight, BellRing, CircleAlert, Loader2, RotateCcw } from "lucide-vue-next";
import { usePreferences } from "@/stores/preferences";
import { tutorApi, invalidateSubjectCaches, type RemindersPayload } from "@/services/api";

const { locale, activeSubjectId, activeLearnerId } = usePreferences();

// ── 012 · Messages inline fr/en ────────────────────────────────────
const msg = computed(() => locale.value === "fr" ? {
  kicker: "Mémorisation active",
  title: "À réviser",
  copy: "Vos rappels dus, classés par retard. Une courte séance maintenant stabilise la mémoire.",
  loading: "Chargement de vos rappels…",
  empty: "Rien à réviser pour l’instant.",
  emptyCopy: "Revenez après votre prochaine séance : les rappels apparaîtront ici.",
  stale: "Certains rappels ont plus de 7 jours de retard — recompactez votre plan plutôt que tout empiler.",
  late: "en retard",
  today: "due aujourd’hui",
  review: "Réviser",
  retry: "Réessayer",
  error: "Chargement impossible — réessayez.",
  noSpace: "Sélectionnez une matière pour voir vos rappels.",
  count: "rappel(s) dû(s)",
} : {
  kicker: "Active recall",
  title: "To review",
  copy: "Your due reminders, sorted by lateness. A short session now stabilises memory.",
  loading: "Loading your reminders…",
  empty: "Nothing to review right now.",
  emptyCopy: "Come back after your next session: reminders will show up here.",
  stale: "Some reminders are over 7 days late — recompact your plan instead of stacking everything.",
  late: "late",
  today: "due today",
  review: "Review",
  retry: "Retry",
  error: "Could not load — please retry.",
  noSpace: "Select a subject to see your reminders.",
  count: "due reminder(s)",
});

const data = ref<RemindersPayload | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
let gen = 0;

function currentCouple(): { sid: string; lid: string } {
  const sid = (activeSubjectId.value || localStorage.getItem("edunexus.space") || localStorage.getItem("edunexus:subject") || "").trim();
  const lid = (activeLearnerId.value || localStorage.getItem("edunexus.learner") || localStorage.getItem("edunexus:learner") || "").trim();
  return { sid, lid };
}

async function load() {
  const { sid, lid } = currentCouple();
  const myGen = ++gen;
  if (!sid) { data.value = { due: [], due_count: 0, stale_plan: false }; loading.value = false; error.value = null; return; }
  loading.value = true; error.value = null; data.value = null;
  try {
    const res = await tutorApi.getReminders(sid, lid || undefined);
    if (myGen !== gen) return;
    data.value = res;
  } catch (e) {
    if (myGen !== gen) return;
    error.value = e instanceof Error ? e.message : msg.value.error;
    data.value = { due: [], due_count: 0, stale_plan: false };
  } finally {
    if (myGen === gen) loading.value = false;
  }
}

function onCoupleChange() {
  invalidateSubjectCaches();
  void load();
}

function lateLabel(days: number): string {
  if (days <= 0) return msg.value.today;
  return `J+${days} · ${msg.value.late}`;
}

watch(() => activeSubjectId.value, () => { onCoupleChange(); });
watch(() => activeLearnerId.value, () => { onCoupleChange(); });

onMounted(async () => {
  await load();
  window.addEventListener("edunexus:subjectChange", onCoupleChange as EventListener);
  window.addEventListener("subjectChange", onCoupleChange as EventListener);
  window.addEventListener("edunexus:learnerChange", onCoupleChange as EventListener);
  window.addEventListener("learnerChange", onCoupleChange as EventListener);
});
onUnmounted(() => {
  window.removeEventListener("edunexus:subjectChange", onCoupleChange as EventListener);
  window.removeEventListener("subjectChange", onCoupleChange as EventListener);
  window.removeEventListener("edunexus:learnerChange", onCoupleChange as EventListener);
  window.removeEventListener("learnerChange", onCoupleChange as EventListener);
});
</script>

<template>
  <section class="page reminders-page reading-surface">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ msg.kicker }}</p>
        <h1>{{ msg.title }}
          <span v-if="data && data.due_count > 0" class="due-badge" role="status">{{ data.due_count }} {{ msg.count }}</span>
        </h1>
        <p>{{ msg.copy }}</p>
      </div>
      <div class="subject-token"><BellRing :size="18" aria-hidden="true" /><span>{{ msg.title }}</span></div>
    </header>

    <p v-if="loading" class="loading-state"><Loader2 :size="16" class="spin" aria-hidden="true" /> {{ msg.loading }}</p>

    <template v-else>
      <p v-if="error" class="field-error" role="alert">{{ error }}
        <button type="button" class="secondary-action" @click="load"><RotateCcw :size="14" aria-hidden="true" /> {{ msg.retry }}</button>
      </p>

      <div v-if="data?.stale_plan" class="stale-banner" role="alert">
        <CircleAlert :size="18" aria-hidden="true" />
        <p>{{ msg.stale }}</p>
      </div>

      <div v-if="data?.due.length" class="reminders-list">
        <article v-for="item in data.due" :key="item.id" class="reminder-row">
          <div class="reminder-mark"><BellRing :size="18" aria-hidden="true" /></div>
          <div class="reminder-body">
            <strong>{{ item.title }}</strong>
            <small>{{ item.kind }} · {{ lateLabel(item.overdue_days) }}</small>
          </div>
          <span class="status-pill" :class="item.overdue_days > 0 ? 'status-pill--orange' : 'status-pill--indigo'">J+{{ item.overdue_days }}</span>
          <RouterLink to="/reviser" class="secondary-action">{{ msg.review }} <ArrowRight :size="16" aria-hidden="true" /></RouterLink>
        </article>
      </div>

      <div v-else class="empty-panel">
        <BellRing :size="48" aria-hidden="true" />
        <h2>{{ currentCouple().sid ? msg.empty : msg.noSpace }}</h2>
        <p>{{ msg.emptyCopy }}</p>
        <div class="empty-actions">
          <RouterLink to="/reviser" class="primary-action">{{ msg.review }} <ArrowRight :size="18" /></RouterLink>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.reminders-page { display: grid; gap: 16px; }
.due-badge { display: inline-block; margin-left: 10px; font-size: 12px; font-weight: 800; padding: 4px 12px; border-radius: 999px; background: var(--orange-soft); color: var(--orange-deep, #9a3412); vertical-align: middle; }
.stale-banner { display: flex; gap: 10px; align-items: flex-start; padding: 14px 16px; border: 1px solid #f0c98a; border-radius: 12px; background: #fff8ec; }
.stale-banner p { margin: 0; font-size: 13.5px; line-height: 1.5; }
.reminders-list { display: grid; gap: 10px; }
.reminder-row { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border: 1px solid var(--line); border-radius: 14px; background: #fff; }
.reminder-mark { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 50%; background: var(--indigo-soft); color: var(--indigo-deep); flex: 0 0 auto; }
.reminder-body { display: grid; gap: 2px; min-width: 0; flex: 1; }
.reminder-body strong { font-size: 14px; line-height: 1.4; }
.reminder-body small { color: var(--muted); font-size: 12px; }
.field-error { color: #b42318; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
</style>
