import { computed, reactive } from "vue";
import { tutorApi } from "@/services/api";
import type { DashboardData, LearningStep } from "@/types";

const state = reactive<{ data: DashboardData | null; loading: boolean; error: string | null; notice: string }>({
  data: null,
  loading: false,
  error: null,
  notice: "",
});

export function useLearningStore() {
  const nextStep = computed<LearningStep | null>(() => state.data?.path.steps.find((step) => step.status !== "completed") ?? null);
  const masteredCount = computed(() => state.data?.concepts.filter((item) => item.score >= 75).length ?? 0);
  const weakestConcept = computed(() => [...(state.data?.concepts ?? [])].sort((a, b) => a.score - b.score)[0] ?? null);

  async function hydrate() {
    if (state.loading || state.data) return;
    state.loading = true;
    state.error = null;
    try { state.data = await tutorApi.dashboard(); }
    catch (error) { state.error = error instanceof Error ? error.message : "Impossible de charger vos données."; }
    finally { state.loading = false; }
  }

  async function toggleQueue() {
    if (!state.data) return;
    state.data.queue = await tutorApi.setQueue(!state.data.queue.running);
    state.notice = state.data.queue.running ? "La file d’indexation est démarrée." : "La file d’indexation est en pause.";
  }

  async function completeStep(stepId: string) {
    if (!state.data) return;
    const step = state.data.path.steps.find((item) => item.id === stepId);
    if (!step || step.status === "completed") return;
    await tutorApi.completeStep(stepId);
    step.status = "completed";
    const complete = state.data.path.steps.filter((item) => item.status === "completed").length;
    state.data.path.progress = Math.round((complete / state.data.path.steps.length) * 100);
    state.notice = `Étape terminée : ${step.title}.`;
  }

  function dismissNotice() { state.notice = ""; }
  return { state, nextStep, masteredCount, weakestConcept, hydrate, toggleQueue, completeStep, dismissNotice };
}
