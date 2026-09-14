<!-- EduNexus UI direction: Atelier de progression — explorer les connaissances par navigation, classement, comparaison, glossaire et carte. -->
<script setup lang="ts">
import { computed, ref, onMounted } from "vue";
import {
  BookOpen,
  GitCompareArrows,
  Loader2,
  MapPinned,
  Search,
  Trophy,
  Sparkles,
  Eye,
  Info,
} from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { tutorApi } from "@/services/api";

const { state } = useLearningStore();

/* ── Subject resolution ────────────────────────────────────── */
const subjectId = computed(() => state.data?.subject.id ?? "");
const subjectIdResolved = ref("");

async function resolveSubjectId(): Promise<string> {
  if (subjectId.value) { subjectIdResolved.value = subjectId.value; return subjectId.value; }
  if (subjectIdResolved.value) return subjectIdResolved.value;
  try {
    const resp = await tutorApi.getSubjects();
    const id = resp.active_id || resp.subjects[0]?.id || "";
    subjectIdResolved.value = id;
    return id;
  } catch { return ""; }
}

/* ── Notion input ─────────────────────────────────────────── */
const notion = ref("");
const notionErr = ref("");
function validateNotion(): boolean {
  notionErr.value = "";
  const v = notion.value.trim();
  if (!v) { notionErr.value = "Saisissez une notion d'abord."; return false; }
  if (v.length < 2 || v.length > 100) { notionErr.value = "La notion doit faire 2–100 caractères."; return false; }
  if (/[<>]/.test(v)) { notionErr.value = "Les caractères < et > ne sont pas autorisés."; return false; }
  return true;
}
const activeSubjectLabel = computed(() => state.data?.subject.name ?? subjectIdResolved.value ?? "—");

/* ── Locate ───────────────────────────────────────────────── */
interface LocateRow { book: string; chapter?: string; page?: number | null; text: string; score: number; }
const locateLoading = ref(false);
const locateResults = ref<LocateRow[]>([]);
const locateGrouped = computed(() => {
  const map = new Map<string, LocateRow[]>();
  for (const r of locateResults.value) {
    const arr = map.get(r.book) ?? [];
    arr.push(r);
    map.set(r.book, arr);
  }
  return Array.from(map.entries());
});
async function doLocate() {
  if (!validateNotion()) return;
  const sid = await resolveSubjectId();
  if (!sid) { notionErr.value = "Aucun espace actif."; return; }
  locateLoading.value = true;
  try {
    const data = await tutorApi.locate(sid, notion.value.trim()) as { results: LocateRow[] };
    locateResults.value = (data.results ?? []) as LocateRow[];
  } catch (e) { notionErr.value = e instanceof Error ? e.message : "Erreur de localisation."; }
  finally { locateLoading.value = false; }
}

/* ── Rank books ───────────────────────────────────────────── */
interface RankRow { book: string; score: number; }
const rankLoading = ref(false);
const rankResults = ref<RankRow[]>([]);
async function doRank() {
  if (!validateNotion()) return;
  const sid = await resolveSubjectId();
  if (!sid) { notionErr.value = "Aucun espace actif."; return; }
  rankLoading.value = true;
  try {
    const data = await tutorApi.rankBooks(sid, notion.value.trim()) as { results: RankRow[] };
    rankResults.value = (data.results ?? []) as RankRow[];
  } catch (e) { notionErr.value = e instanceof Error ? e.message : "Erreur de classement."; }
  finally { rankLoading.value = false; }
}

/* ── Compare (streaming NDJSON) ───────────────────────────── */
interface CompareSource { book: string; chapter?: string; page?: number | null; score: number; }
const compareLoading = ref(false);
const compareAnswer = ref("");
const compareThinking = ref("");
const compareSources = ref<CompareSource[]>([]);
const compareBoxVisible = ref(false);
const apiBase = (import.meta.env.VITE_EDUNEXUS_API_BASE as string | undefined) ?? "/api/tutor";

async function doCompare() {
  if (!validateNotion()) return;
  const sid = await resolveSubjectId();
  if (!sid) { notionErr.value = "Aucun espace actif."; return; }
  compareLoading.value = true;
  compareBoxVisible.value = true;
  compareAnswer.value = "";
  compareThinking.value = "";
  compareSources.value = [];
  try {
    const resp = await fetch(`${apiBase}/subjects/${encodeURIComponent(sid)}/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notion: notion.value.trim() }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body?.getReader();
    if (!reader) throw new Error("Flux indisponible");
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).trim(); buf = buf.slice(idx + 1);
        if (!line) continue;
        try {
          const frame = JSON.parse(line) as { type?: string; sources?: CompareSource[]; text?: string; message?: string };
          if (frame.type === "sources" && frame.sources) compareSources.value = frame.sources;
          else if (frame.type === "content_delta" && frame.text) compareAnswer.value += frame.text;
          else if (frame.type === "thinking_delta" && frame.text) compareThinking.value += frame.text;
          else if (frame.type === "error" && frame.message) compareAnswer.value += `\n[erreur: ${frame.message}]`;
        } catch { /* ignore malformed */ }
      }
    }
  } catch (e) { notionErr.value = e instanceof Error ? e.message : "Erreur de comparaison."; }
  finally { compareLoading.value = false; }
}

/* ── Glossary ─────────────────────────────────────────────── */
interface GlossaryTerm { term: string; definition: string; }
const glossLoading = ref(false);
const glossTerms = ref<GlossaryTerm[]>([]);
const glossError = ref<string | null>(null);
const explaining = ref<Record<string, boolean>>({});
const explanations = ref<Record<string, string>>({});

async function loadGlossary() {
  const sid = await resolveSubjectId();
  if (!sid) { glossError.value = "Aucun espace actif."; return; }
  glossLoading.value = true; glossError.value = null;
  try {
    const data = await tutorApi.getGlossary(sid) as { terms: GlossaryTerm[] };
    glossTerms.value = (data.terms ?? []) as GlossaryTerm[];
  } catch (e) { glossError.value = e instanceof Error ? e.message : "Erreur glossaire."; }
  finally { glossLoading.value = false; }
}

async function explainTerm(term: string) {
  const sid = await resolveSubjectId();
  if (!sid) return;
  explaining.value = { ...explaining.value, [term]: true };
  explanations.value = { ...explanations.value, [term]: "explication…" };
  try {
    const resp = await fetch(`${apiBase}/subjects/${encodeURIComponent(sid)}/glossary/${encodeURIComponent(term)}/explain`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body?.getReader();
    if (!reader) throw new Error("Flux indisponible");
    const decoder = new TextDecoder();
    let buf = ""; let answer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).trim(); buf = buf.slice(idx + 1);
        if (!line) continue;
        try {
          const f = JSON.parse(line) as { type?: string; text?: string; definition?: string };
          if (f.type === "content_delta" || f.type === "delta" || f.type === "definition") answer += f.text ?? f.definition ?? "";
          else if (f.text) answer += f.text;
        } catch { answer += line; }
      }
    }
    explanations.value = { ...explanations.value, [term]: answer || "(aucune explication renvoyée)" };
  } catch {
    explanations.value = { ...explanations.value, [term]: "échec de l'explication." };
  } finally {
    explaining.value = { ...explaining.value, [term]: false };
  }
}

/* ── Knowledge map ────────────────────────────────────────── */
interface MapNode { id: string; name: string; }
interface MapEdge { from_concept_id: string; to_concept_id: string; relation: string; }
const mapLoading = ref(false);
const mapNodes = ref<MapNode[]>([]);
const mapEdges = ref<MapEdge[]>([]);
const mapError = ref<string | null>(null);
async function loadMap() {
  const sid = await resolveSubjectId();
  if (!sid) { mapError.value = "Aucun espace actif."; return; }
  mapLoading.value = true; mapError.value = null;
  try {
    const data = await tutorApi.getKnowledgeMap(sid) as { nodes: MapNode[]; edges: MapEdge[] };
    mapNodes.value = (data.nodes ?? []) as MapNode[];
    mapEdges.value = (data.edges ?? []) as MapEdge[];
  } catch (e) { mapError.value = e instanceof Error ? e.message : "Erreur carte."; }
  finally { mapLoading.value = false; }
}

function mapPos(id: string): { x: number; y: number } {
  const W = 600, H = 420, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 60;
  const idx = mapNodes.value.findIndex((n) => n.id === id);
  if (idx < 0 || mapNodes.value.length === 0) return { x: cx, y: cy };
  const ang = (2 * Math.PI * idx) / mapNodes.value.length - Math.PI / 2;
  return { x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang) };
}

onMounted(() => { resolveSubjectId(); });
</script>

<template>
  <section class="page explorer-page">
    <header class="page-intro explorer-intro">
      <div>
        <p class="eyebrow">Explorer</p>
        <h1>Explorer les connaissances</h1>
        <p>Naviguer, comparer, glossaire et carte des connaissances de l'espace actif · <strong>{{ activeSubjectLabel }}</strong></p>
      </div>
    </header>

    <!-- Notion bar (shared) -->
    <section class="content-panel notion-bar">
      <label class="field-label" for="exp-notion">Notion</label>
      <div class="notion-row">
        <input
          id="exp-notion"
          v-model="notion"
          type="text"
          placeholder="Notion à localiser ou comparer…"
          maxlength="100"
          @keydown.enter.prevent="doLocate"
        />
        <span class="notion-actions">
          <button type="button" class="secondary-action notion-btn" :disabled="locateLoading" @click="doLocate">
            <Search :size="14" aria-hidden="true" /> <span v-if="locateLoading">…</span><span v-else>Localiser</span>
          </button>
          <button type="button" class="secondary-action notion-btn" :disabled="rankLoading" @click="doRank">
            <Trophy :size="14" aria-hidden="true" /> <span v-if="rankLoading">…</span><span v-else>Classer les livres</span>
          </button>
          <button type="button" class="primary-action notion-btn" :disabled="compareLoading" @click="doCompare">
            <GitCompareArrows :size="14" aria-hidden="true" /> <span v-if="compareLoading">Comparaison…</span><span v-else>Comparer</span>
          </button>
        </span>
      </div>
      <p v-if="notionErr" class="field-error" role="alert">{{ notionErr }}</p>
      <p class="hint">2–100 caractères, sans &lt; ni &gt; · 1 notion alimente les 3 outils ci-dessous.</p>
    </section>

    <!-- Main grid -->
    <div class="explorer-layout">
      <!-- Left: locate / rank / compare -->
      <div class="explorer-left">
        <!-- Locate -->
        <article class="content-panel studio-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Outil 1</p>
              <h2><Search :size="18" aria-hidden="true" /> Localiser</h2>
            </div>
            <StatusPill tone="indigo">{{ locateResults.length }} passages</StatusPill>
          </div>
          <p class="card-hint">Passages classés par pertinence lexicale — sans LLM.</p>
          <div v-if="locateLoading" class="card-loading"><Loader2 :size="16" class="spin" /> Localisation…</div>
          <div v-else-if="locateGrouped.length" class="result-groups">
            <div v-for="[book, items] in locateGrouped" :key="book" class="result-group">
              <div class="src-group-head"><BookOpen :size="13" aria-hidden="true" /> {{ book }}</div>
              <div v-for="(it, i) in items" :key="i" class="result">
                <span class="result-meta">[{{ Number(it.score).toFixed(3) }}{{ it.chapter ? ' — chap. ' + it.chapter : '' }}{{ it.page != null ? ', p. ' + it.page : '' }}]</span>
                <span class="result-text">{{ it.text }}</span>
              </div>
            </div>
          </div>
          <p v-else class="empty-copy">Aucun résultat — saisissez une notion puis « Localiser ».</p>
        </article>

        <!-- Rank -->
        <article class="content-panel studio-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Outil 2</p>
              <h2><Trophy :size="18" aria-hidden="true" /> Classer les livres</h2>
            </div>
            <StatusPill tone="slate">{{ rankResults.length }} livres</StatusPill>
          </div>
          <p class="card-hint">Agrégation des scores par ouvrage.</p>
          <div v-if="rankLoading" class="card-loading"><Loader2 :size="16" class="spin" /> Classement…</div>
          <div v-else-if="rankResults.length" class="rank-list">
            <div v-for="(row, i) in rankResults" :key="i" class="rank-row">
              <span class="rank-index">{{ i + 1 }}</span>
              <span class="rank-book">{{ row.book }}</span>
              <span class="rank-score">{{ Number(row.score).toFixed(3) }}</span>
            </div>
          </div>
          <p v-else class="empty-copy">Utilisez « Classer les livres » pour voir les ouvrages les plus pertinents.</p>
        </article>

        <!-- Compare -->
        <article class="content-panel studio-card compare-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Outil 3</p>
              <h2><GitCompareArrows :size="18" aria-hidden="true" /> Comparer</h2>
            </div>
            <StatusPill v-if="compareLoading" tone="orange"><Loader2 :size="12" class="spin" /> synthèse…</StatusPill>
            <StatusPill v-else-if="compareSources.length" tone="indigo">{{ compareSources.length }} sources</StatusPill>
          </div>
          <p class="card-hint">Synthèse multi-ouvrages citée · streaming NDJSON (sources → delta* → fin).</p>
          <div v-if="compareBoxVisible" class="compare-box">
            <div v-if="compareSources.length" class="compare-sources">
              <div v-for="(s, i) in compareSources" :key="i" class="result">
                [Livre {{ s.book ?? "?" }}{{ s.chapter ? ' — chap. ' + s.chapter : '' }}{{ s.page != null ? ', p. ' + s.page : '' }}] (score {{ Number(s.score).toFixed(3) }})
              </div>
            </div>
            <div class="stream-answer" :class="{ hasAnswer: compareAnswer }">
              <p v-if="!compareAnswer && !compareLoading" class="empty-copy" style="padding:8px 0;">Lancez une comparaison pour voir la synthèse.</p>
              <p v-else style="white-space: pre-wrap; line-height: 1.6;">{{ compareAnswer }}</p>
              <p v-if="compareThinking" class="tutor-think">💭 {{ compareThinking }}</p>
            </div>
          </div>
          <p v-else class="empty-copy">La comparaison s'affichera ici.</p>
        </article>
      </div>

      <!-- Right: glossaire + carte -->
      <div class="explorer-right">
        <!-- Glossary -->
        <article class="content-panel studio-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Référence</p>
              <h2><BookOpen :size="18" aria-hidden="true" /> Glossaire</h2>
            </div>
            <button type="button" class="secondary-action" :disabled="glossLoading" @click="loadGlossary">
              <Loader2 v-if="glossLoading" :size="14" class="spin" />
              <Sparkles v-else :size="14" aria-hidden="true" />
              {{ glossTerms.length ? "Recharger" : "Charger le glossaire" }}
            </button>
          </div>
          <p v-if="glossError" class="field-error">{{ glossError }}</p>
          <div v-if="glossLoading" class="card-loading"><Loader2 :size="16" class="spin" /> Chargement…</div>
          <div v-else-if="glossTerms.length" class="gloss-list">
            <div v-for="t in glossTerms" :key="t.term" class="term-card">
              <div class="term-head">
                <strong>{{ t.term }}</strong>
                <button type="button" class="text-button term-explain-btn" :disabled="!!explaining[t.term]" @click="explainTerm(t.term)">
                  <Eye :size="12" aria-hidden="true" />
                  {{ explaining[t.term] ? "…" : "Expliquer davantage" }}
                </button>
              </div>
              <p class="term-def">{{ t.definition }}</p>
              <div v-if="explanations[t.term]" class="explain">
                <Info :size="12" aria-hidden="true" />
                <span style="white-space: pre-wrap;">{{ explanations[t.term] }}</span>
              </div>
            </div>
          </div>
          <p v-else class="empty-copy">Aucun terme. Lancez « Préparer le cours » dans Apprentissage ou cliquez « Charger ».</p>
        </article>

        <!-- Map -->
        <article class="content-panel studio-card">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Structure</p>
              <h2><MapPinned :size="18" aria-hidden="true" /> Carte des connaissances</h2>
            </div>
            <button type="button" class="secondary-action" :disabled="mapLoading" @click="loadMap">
              <Loader2 v-if="mapLoading" :size="14" class="spin" />
              <MapPinned v-else :size="14" aria-hidden="true" />
              {{ mapNodes.length ? "Recharger" : "Afficher la carte" }}
            </button>
          </div>
          <p v-if="mapError" class="field-error">{{ mapError }}</p>
          <div v-if="mapLoading" class="card-loading"><Loader2 :size="16" class="spin" /> Construction…</div>
          <div v-else-if="mapNodes.length" class="map-wrap">
            <svg :viewBox="`0 0 600 420`" class="knowledge-map" role="img" aria-label="Carte des connaissances">
              <!-- edges -->
              <line
                v-for="(e, i) in mapEdges"
                :key="'e-'+i"
                :x1="mapPos(e.from_concept_id).x" :y1="mapPos(e.from_concept_id).y"
                :x2="mapPos(e.to_concept_id).x" :y2="mapPos(e.to_concept_id).y"
                stroke="var(--accent-line, #cbd1ff)" stroke-width="1.6"
              >
                <title>{{ e.relation }}</title>
              </line>
              <!-- nodes -->
              <circle
                v-for="n in mapNodes"
                :key="'n-'+n.id"
                :cx="mapPos(n.id).x" :cy="mapPos(n.id).y" r="7"
                fill="var(--indigo, #4f46e5)"
              />
              <text
                v-for="n in mapNodes"
                :key="'t-'+n.id"
                :x="mapPos(n.id).x" :y="mapPos(n.id).y + 20"
                font-size="11.5" text-anchor="middle" fill="currentColor"
              >{{ n.name }}</text>
            </svg>
            <p class="hint" style="margin:8px 0 0;">{{ mapNodes.length }} notions · {{ mapEdges.length }} relations</p>
          </div>
          <p v-else class="empty-copy">Aucune relation entre notions.<br>Lancez « Préparer le cours » dans Apprentissage.</p>
        </article>
      </div>
    </div>
  </section>
</template>

<script lang="ts">
export default { name: "ExplorerView" };
</script>

<style scoped>
.explorer-intro p { max-width: 760px; }

/* — Notion bar — */
.notion-bar { padding: 18px 20px 14px; margin-bottom: 16px; }
.notion-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.notion-row input { flex: 1 1 260px; min-height: 44px; }
.notion-actions { display: inline-flex; gap: 8px; flex-wrap: wrap; }
.notion-btn { min-height: 40px; padding: 8px 14px; font-size: 13px; white-space: nowrap; }
.field-error { margin: 8px 0 0; color: #b42318; font-size: 12px; font-weight: 600; }
.hint { margin: 8px 0 0; color: var(--muted); font-size: 11.5px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

/* — Layout — */
.explorer-layout { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(320px, .85fr); gap: 16px; align-items: start; }
.explorer-left, .explorer-right { display: grid; gap: 16px; }
.studio-card { padding: 20px; }
.card-hint { margin: 8px 0 14px; color: var(--muted); font-size: 12px; line-height: 1.5; }
.empty-copy { margin: 0; color: var(--muted); font-size: 13px; text-align: center; padding: 18px 10px; line-height: 1.5; }
.card-loading { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; padding: 10px 0; }

.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.panel-heading h2 { display: inline-flex; align-items: center; gap: 8px; margin: 4px 0 0; font: 700 17px/1.15 "Fraunces", Georgia, serif; letter-spacing: -.02em; }

/* — Locate — */
.result-groups { display: grid; gap: 14px; }
.src-group-head { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 800; color: var(--indigo-deep); margin-bottom: 6px; }
.result { padding: 10px 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; font-size: 13px; line-height: 1.55; }
.result-meta { display: block; color: var(--indigo-deep); font-weight: 700; font-size: 11.5px; margin-bottom: 4px; }
.result-text { white-space: pre-wrap; overflow-wrap: anywhere; }

/* — Rank — */
.rank-list { display: grid; gap: 6px; }
.rank-row { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 8px 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; }
.rank-index { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: var(--indigo-soft); color: var(--indigo-deep); font-size: 12px; font-weight: 800; flex: 0 0 auto; }
.rank-book { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 650; }
.rank-score { flex: 0 0 auto; font-family: ui-monospace, monospace; font-size: 12px; font-weight: 700; color: var(--indigo-deep); background: var(--indigo-soft); padding: 3px 8px; border-radius: 999px; }

/* — Compare — */
.compare-card .compare-box { border: 1px solid #cbd1ff; background: #f8f8ff; border-radius: 14px; padding: 12px 14px; }
.compare-sources { display: grid; gap: 6px; margin-bottom: 10px; }
.compare-sources .result { background: #fff; font-size: 12.5px; }
.stream-answer { min-height: 44px; padding: 10px 12px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; }
.tutor-think { margin: 8px 0 0; color: #9aa3bc; font-size: 12.5px; font-style: italic; white-space: pre-wrap; }

/* — Glossary — */
.gloss-list { display: grid; gap: 10px; }
.term-card { padding: 12px 13px; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; }
.term-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.term-head strong { font-size: 14px; }
.term-explain-btn { font-size: 12px; white-space: nowrap; }
.term-def { margin: 6px 0 0; color: var(--muted); font-size: 12.5px; line-height: 1.55; }
.explain { display: flex; gap: 6px; margin-top: 8px; padding: 8px 10px; border: 1px dashed #d9defb; border-radius: 10px; background: #f7f8ff; font-size: 12.5px; line-height: 1.5; color: var(--ink-2); }
.explain svg { flex: 0 0 auto; margin-top: 2px; color: var(--indigo); }

/* — Map — */
.map-wrap { overflow: hidden; border: 1px solid var(--line-soft); border-radius: 12px; background: #fff; padding: 10px; }
.knowledge-map { width: 100%; max-width: 620px; display: block; margin: 0 auto; }

/* — Responsive — */
@media (max-width: 980px) {
  .explorer-layout { grid-template-columns: 1fr; }
  .notion-row { flex-direction: column; align-items: stretch; }
  .notion-actions { width: 100%; }
  .notion-actions .notion-btn { flex: 1 1 0; justify-content: center; }
}
@media (max-width: 640px) {
  .studio-card { padding: 16px; }
  .panel-heading { flex-direction: column; }
}
</style>
