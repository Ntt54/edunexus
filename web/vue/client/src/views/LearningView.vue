<!-- EduNexus UI direction: Atelier de progression — une séance calme, structurée en compréhension, rappel et vérification. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowRight, BookOpenCheck, BrainCircuit, Headphones, RotateCcw, Loader2, Send, Volume2, Check, X } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";
import { tutorApi } from "@/services/api";

const { state } = useLearningStore();
const { t } = usePreferences();
const selected = ref("");
const reviewMoment = "/manus-storage/edunexus-review-moment_492064e9.png";

// Learning store concept fallback
const concepts = computed(() => state.data?.concepts ?? []);
const current = computed(() => concepts.value.find((c) => c.id === selected.value) ?? concepts.value[0] ?? null);

interface DueCard { id: string; concept_id: string; question: string; answer: string; level?: string; due?: string; }
const dueCards = ref<DueCard[]>([]);
const loadingDue = ref(false);
const dueError = ref<string | null>(null);
const activeDue = ref<DueCard | null>(null);
const userRecall = ref("");
const showAnswer = ref(false);
const grading = ref(false);
const ttsPlaying = ref(false);

const subjectId = computed(() => state.data?.subject.id ?? "");

onMounted(async () => {
  if (concepts.value.length && !selected.value) selected.value = concepts.value[0].id;
  await loadDue();
});

async function resolveSubjectId(): Promise<string> {
  if (subjectId.value) return subjectId.value;
  const resp = await tutorApi.getSubjects();
  return resp.active_id || resp.subjects[0]?.id || "";
}

async function loadDue() {
  loadingDue.value = true; dueError.value = null;
  try {
    const sid = await resolveSubjectId();
    if (!sid) { dueError.value = "Aucun espace actif."; return; }
    const data = await tutorApi.getReviewsDue(sid);
    dueCards.value = ((data as unknown as { due: DueCard[] }).due ?? []) as DueCard[];
    if (dueCards.value.length && !activeDue.value) activeDue.value = dueCards.value[0];
  } catch (e) { dueError.value = e instanceof Error ? e.message : "Chargement impossible"; }
  finally { loadingDue.value = false; }
}

function selectDue(card: DueCard) {
  activeDue.value = card;
  showAnswer.value = false;
  userRecall.value = "";
}

async function grade(success: boolean) {
  if (!activeDue.value) return;
  grading.value = true;
  try {
    await tutorApi.gradeReview(activeDue.value.id, success);
    dueCards.value = dueCards.value.filter(c => c.id !== activeDue.value!.id);
    if (dueCards.value.length) activeDue.value = dueCards.value[0];
    else activeDue.value = null;
    showAnswer.value = false;
    userRecall.value = "";
  } catch (e) { dueError.value = e instanceof Error ? e.message : "Correction impossible"; }
  finally { grading.value = false; }
}

function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "fr-FR";
    u.onstart = () => { ttsPlaying.value = true; };
    u.onend = () => { ttsPlaying.value = false; };
    u.onerror = () => { ttsPlaying.value = false; };
    window.speechSynthesis.speak(u);
  } catch { ttsPlaying.value = false; }
}
function stopSpeak() {
  try { window.speechSynthesis.cancel(); } catch { /* ignore */ }
  ttsPlaying.value = false;
}

const totalDue = computed(() => dueCards.value.length);
const mastery = computed(() => current.value?.score ?? 0);
const hasTTS = typeof window !== "undefined" && "speechSynthesis" in window;
</script>

<template>
  <section v-if="state.data" class="page learning-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('learning.kicker') }}</p>
        <h1>{{ t('learning.title') }}</h1>
        <p>{{ t('learning.copy') }}</p>
      </div>
    </header>

    <section class="session-plan">
      <article><span>01</span><div><strong>{{ t('learning.choose') }}</strong><small>{{ t('learning.chooseCopy') }}</small></div></article>
      <article><span>02</span><div><strong>{{ t('learning.recall') }}</strong><small>{{ t('learning.recallCopy') }}</small></div></article>
      <article><span>03</span><div><strong>{{ t('learning.check') }}</strong><small>{{ t('learning.checkCopy') }}</small></div></article>
    </section>

    <section class="learning-grid">
      <article class="content-panel concept-focus">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">{{ t('learning.current') }}</p>
            <h2>{{ current?.name ?? "—" }}</h2>
          </div>
          <StatusPill :tone="mastery < 40 ? 'orange' : 'indigo'">{{ t('learning.mastery', { score: mastery }) }}</StatusPill>
        </div>

        <label class="field-label" for="concept">{{ t('learning.change') }}</label>
        <select id="concept" v-model="selected">
          <option v-for="concept in concepts" :key="concept.id" :value="concept.id">{{ concept.name }} · {{ concept.score }} %</option>
        </select>

        <div class="mastery-scale">
          <span><i :style="{ width: `${mastery}%` }"></i></span>
          <small>{{ t('learning.scoreReason') }}</small>
        </div>

        <!-- Due flashcard active -->
        <div v-if="loadingDue" class="recall-card" style="justify-content:center;">
          <Loader2 :size="18" class="spin" aria-hidden="true" /> Chargement des révisions…
        </div>

        <div v-else-if="activeDue" class="flashcard-active">
          <div class="recall-card">
            <BrainCircuit :size="22" aria-hidden="true" />
            <div>
              <strong>{{ t('learning.recallQuestion') }}</strong>
              <p class="q-text">{{ activeDue.question }}</p>
            </div>
          </div>

          <textarea v-model="userRecall" rows="4" :placeholder="t('learning.answerPlaceholder')" :aria-label="t('learning.answerPlaceholder')"></textarea>

          <div class="flashcard-actions">
            <button class="secondary-action" type="button" @click="showAnswer = !showAnswer">
              <BookOpenCheck :size="16" aria-hidden="true" />
              {{ showAnswer ? "Masquer la réponse" : "Voir la réponse" }}
            </button>
            <button v-if="hasTTS" type="button" class="text-button" @click="ttsPlaying ? stopSpeak() : speak(activeDue.question + ' . ' + activeDue.answer)">
              <Volume2 :size="16" aria-hidden="true" />
              {{ ttsPlaying ? "Arrêter" : "Écouter" }}
            </button>
          </div>

          <div v-if="showAnswer" class="answer-reveal">
            <p class="eyebrow">Réponse attendue</p>
            <p class="answer-text">{{ activeDue.answer }}</p>
          </div>

          <div v-if="showAnswer || userRecall.trim()" class="grade-row">
            <span class="grade-hint">Comment s'est passé le rappel ?</span>
            <span class="grade-btns">
              <button type="button" class="secondary-action" style="min-height:36px;padding:7px 12px;" :disabled="grading" @click="grade(false)">
                <X :size="14" aria-hidden="true" /> Raté
              </button>
              <button type="button" class="primary-action" style="min-height:36px;padding:7px 12px;" :disabled="grading" @click="grade(true)">
                <Check :size="14" aria-hidden="true" /> Réussi
              </button>
            </span>
          </div>

          <button v-else class="primary-action" type="button" @click="showAnswer = true">
            {{ t('learning.checkReasoning') }} <ArrowRight :size="17" aria-hidden="true" />
          </button>
        </div>

        <div v-else-if="dueError" class="recall-card" style="border-color:#f5bb9e;background:var(--orange-soft);">
          <p style="margin:0;font-size:13px;">{{ dueError }} <button type="button" class="text-button" style="margin-left:8px;" @click="loadDue">Réessayer</button></p>
        </div>

        <div v-else class="recall-card">
          <BrainCircuit :size="22" aria-hidden="true" />
          <div>
            <strong>{{ t('learning.recallQuestion') }}</strong>
            <p>{{ t('learning.recallPrompt') }}</p>
          </div>
        </div>

        <div v-if="!activeDue && !loadingDue && !dueError" class="empty-recall">
          <p>Aucune carte due pour l'instant. Choisis une notion ci-dessus et fais un rappel libre, ou importe des sources puis « Préparer le cours » pour générer des flashcards.</p>
          <textarea rows="4" v-model="userRecall" :placeholder="t('learning.answerPlaceholder')" :aria-label="t('learning.answerPlaceholder')"></textarea>
          <button class="primary-action" type="button" disabled style="opacity:.6;">
            {{ t('learning.checkReasoning') }} <ArrowRight :size="17" />
          </button>
        </div>
      </article>

      <aside class="learning-side">
        <article class="illustrated-card">
          <img :src="reviewMoment" :alt="t('learning.due')" @error="($event.currentTarget as HTMLImageElement).style.display = 'none'" />
          <div>
            <p class="eyebrow">{{ t('learning.due') }}</p>
            <h2>{{ t('learning.toDo', { count: totalDue }) }}</h2>
            <p>{{ t('learning.dueReason') }}</p>
            <RouterLink to="/parcours" class="text-link">{{ t('learning.pathSteps') }}</RouterLink>
          </div>
        </article>

        <!-- Due list -->
        <article class="content-panel due-list-panel">
          <div class="panel-heading" style="margin-bottom:12px;">
            <div><p class="eyebrow">File</p><h2 style="font-size:18px;">Cartes dues</h2></div>
            <span class="status-pill status-pill--orange" v-if="totalDue">{{ totalDue }}</span>
          </div>
          <div v-if="loadingDue" class="due-loading"><Loader2 :size="16" class="spin" /> Chargement…</div>
          <div v-else-if="dueCards.length" class="due-cards">
            <button
              v-for="card in dueCards.slice(0,8)"
              :key="card.id"
              type="button"
              class="due-chip"
              :class="{ active: activeDue?.id === card.id }"
              @click="selectDue(card)"
            >
              <span class="due-chip-q">{{ card.question.slice(0, 70) }}{{ card.question.length > 70 ? "…" : "" }}</span>
              <small>{{ card.level ?? "" }}</small>
            </button>
          </div>
          <p v-else class="empty-copy" style="padding:8px 0;">Aucune carte due — reviens demain ou crée des flashcards via la préparation.</p>
          <button v-if="dueCards.length" type="button" class="text-button" style="margin-top:8px;" @click="loadDue">
            <RotateCcw :size="14" aria-hidden="true" /> Actualiser
          </button>
        </article>

        <article class="content-panel audio-card">
          <Headphones :size="21" aria-hidden="true" />
          <div>
            <p class="eyebrow">{{ t('learning.audio') }}</p>
            <h2>{{ t('learning.reviewDifferently') }}</h2>
            <p>{{ t('learning.audioCopy') }}</p>
            <button type="button" class="secondary-action" :disabled="!activeDue" @click="activeDue && speak(activeDue.question + ' . ' + activeDue.answer)">
              <Volume2 :size="16" aria-hidden="true" />
              {{ t('learning.prepareSummary') }} <ArrowRight :size="16" />
            </button>
          </div>
        </article>

        <article class="content-panel flashcard-card">
          <RotateCcw :size="21" aria-hidden="true" />
          <div>
            <p class="eyebrow">{{ t('learning.cards') }}</p>
            <h2>{{ t('learning.spaced') }}</h2>
            <p>{{ t('learning.cardsReason', { count: current?.recentFailures ?? 0 }) }}</p>
          </div>
        </article>
      </aside>
    </section>
  </section>

  <section v-else class="page loading-state">
    <Loader2 :size="24" class="spin" aria-hidden="true" />
    <p>{{ t('app.loadingWorkshop') }}</p>
  </section>
</template>

<style scoped>
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
.flashcard-active { display: grid; gap: 12px; margin-top: 4px; }
.q-text { margin: 6px 0 0; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
.flashcard-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.answer-reveal { padding: 12px; border: 1px solid #d9defb; border-radius: 12px; background: #f7f8ff; }
.answer-text { margin: 6px 0 0; font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
.grade-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border: 1px solid #cbd1ff; border-radius: 12px; background: #fff; }
.grade-hint { font-size: 12px; font-weight: 700; color: var(--muted); }
.grade-btns { display: flex; gap: 8px; }
.empty-recall { display: grid; gap: 10px; margin-top: 8px; }
.empty-recall p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
.due-list-panel { padding: 20px; }
.due-cards { display: grid; gap: 8px; }
.due-chip { display: grid; gap: 2px; text-align: left; padding: 10px 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; cursor: pointer; transition: border-color .15s, background .15s; }
.due-chip:hover { border-color: #cbd1ff; background: #f8f8ff; }
.due-chip.active { border-color: var(--indigo); background: var(--indigo-soft); }
.due-chip-q { font-size: 13px; line-height: 1.4; white-space: pre-wrap; }
.due-chip small { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
.due-loading { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
@media (max-width: 980px) { .learning-grid { grid-template-columns: 1fr; } }
</style>
