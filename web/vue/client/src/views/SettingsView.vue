<!-- EduNexus UI direction: Atelier de progression — réglages complets avec persistance API -->
<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import {
  Check,
  Cpu,
  Database,
  Moon,
  ShieldCheck,
  LoaderCircle,
  RotateCcw,
  Plug,
} from "lucide-vue-next";
import { tutorApi } from "@/services/api";
import { usePreferences } from "@/stores/preferences";

const { t } = usePreferences();

/* ── Inference settings ──────────────────────────────────────────── */
const options = reactive({
  temperature: null as number | null,
  top_p: null as number | null,
  top_k: null as number | null,
  num_ctx: null as number | null,
  num_predict: null as number | null,
  repeat_penalty: null as number | null,
  num_thread: null as number | null,
  keep_alive: "" as string,
});

/* ── Tutor settings ──────────────────────────────────────────────── */
const tutor = reactive({
  think: false,
  socratic: false,
  level: "intermediate",
  top_k: null as number | null,
  llm_provider: "ollama",
  llm_base_url: "",
  llm_api_key: "",
  embed_batch_size: null as number | null,
  max_parallel_embed: null as number | null,
  nightly_enabled: false,
  nightly_start_at: "23:00",
  nightly_stop_at: "07:00",
  nightly_only_on_ac: true,
  nightly_max_runtime_minutes: null as number | null,
  nightly_prepare_enabled: false,
});

/* ── UI state ────────────────────────────────────────────────────── */
const loading = ref(true);
const saving = ref(false);
const saved = ref(false);
const statusMessage = ref("");
const statusType = ref<"ok" | "error" | "">("");

// Connection test
const testingConnection = ref(false);
const connectionResult = ref<{ ok: boolean; message: string } | null>(null);

// Nightly status
const nightlyStatus = ref("");
const nightlyLoading = ref(false);

// Maintenance
const maintenanceRunning = ref(false);

// Restart
const restarting = ref(false);

/* ── Data loading ────────────────────────────────────────────────── */
onMounted(async () => {
  try {
    const data = (await tutorApi.getSettings()) as Record<string, unknown>;
    const o = (data.options ?? {}) as Record<string, unknown>;
    const tt = (data.tutor ?? {}) as Record<string, unknown>;

    options.temperature = (o.temperature as number) ?? null;
    options.top_p = (o.top_p as number) ?? null;
    options.top_k = (o.top_k as number) ?? null;
    options.num_ctx = (o.num_ctx as number) ?? null;
    options.num_predict = (o.num_predict as number) ?? null;
    options.repeat_penalty = (o.repeat_penalty as number) ?? null;
    options.num_thread = (o.num_thread as number) ?? null;
    options.keep_alive = (o.keep_alive as string) ?? "";

    tutor.think = !!tt.think;
    tutor.socratic = !!tt.socratic;
    tutor.level = (tt.level as string) || "intermediate";
    tutor.top_k = (tt.top_k as number) ?? null;
    tutor.llm_provider = (tt.llm_provider as string) || "ollama";
    tutor.llm_base_url = (tt.llm_base_url as string) || "";
    tutor.llm_api_key = (tt.llm_api_key as string) || "";
    tutor.embed_batch_size = (tt.embed_batch_size as number) ?? null;
    tutor.max_parallel_embed = (tt.max_parallel_embed as number) ?? null;
    tutor.nightly_enabled = !!tt.nightly_enabled;
    tutor.nightly_start_at = (tt.nightly_start_at as string) || "23:00";
    tutor.nightly_stop_at = (tt.nightly_stop_at as string) || "07:00";
    tutor.nightly_only_on_ac = tt.nightly_only_on_ac !== false;
    tutor.nightly_max_runtime_minutes = (tt.nightly_max_runtime_minutes as number) ?? null;
    tutor.nightly_prepare_enabled = !!tt.nightly_prepare_enabled;

    // Fetch nightly status
    fetchNightlyStatus();
  } catch (e: unknown) {
    statusMessage.value = "Erreur chargement : " + (e instanceof Error ? e.message : String(e));
    statusType.value = "error";
  } finally {
    loading.value = false;
  }
});

async function fetchNightlyStatus() {
  try {
    nightlyLoading.value = true;
    const night = await tutorApi.getNightly();
    if (night.scheduler_running) {
      nightlyStatus.value = "Planificateur actif · " + (night.queue?.pending_count ?? 0) + " en attente";
    } else if (night.enabled) {
      nightlyStatus.value = "Planificateur activé · en attente de la fenêtre";
    } else {
      nightlyStatus.value = "Planificateur désactivé";
    }
  } catch {
    nightlyStatus.value = "Statut nocturne : —";
  } finally {
    nightlyLoading.value = false;
  }
}

/* ── Save settings ───────────────────────────────────────────────── */
async function saveAllSettings() {
  saving.value = true;
  saved.value = false;
  statusMessage.value = "Enregistrement…";
  statusType.value = "";
  try {
    const payload = {
      options: {
        temperature: options.temperature,
        top_p: options.top_p,
        top_k: options.top_k,
        num_ctx: options.num_ctx,
        num_predict: options.num_predict,
        repeat_penalty: options.repeat_penalty,
        num_thread: options.num_thread,
        keep_alive: options.keep_alive || null,
      },
      think: tutor.think,
      socratic: tutor.socratic,
      level: tutor.level,
      top_k: tutor.top_k,
      llm_provider: tutor.llm_provider,
      llm_base_url: tutor.llm_base_url,
      llm_api_key: tutor.llm_api_key,
      embed_batch_size: tutor.embed_batch_size,
      max_parallel_embed: tutor.max_parallel_embed,
      nightly_enabled: tutor.nightly_enabled,
      nightly_start_at: tutor.nightly_start_at,
      nightly_stop_at: tutor.nightly_stop_at,
      nightly_only_on_ac: tutor.nightly_only_on_ac,
      nightly_max_runtime_minutes: tutor.nightly_max_runtime_minutes,
      nightly_prepare_enabled: tutor.nightly_prepare_enabled,
    };
    await tutorApi.saveSettings(payload);
    statusMessage.value = "✓ Enregistré";
    statusType.value = "ok";
    saved.value = true;
    setTimeout(() => {
      saved.value = false;
      statusMessage.value = "";
    }, 3000);
  } catch (e: unknown) {
    statusMessage.value = "Erreur : " + (e instanceof Error ? e.message : String(e));
    statusType.value = "error";
  } finally {
    saving.value = false;
  }
}

/* ── Test connection ─────────────────────────────────────────────── */
async function testConnection() {
  testingConnection.value = true;
  connectionResult.value = null;
  try {
    const result = await tutorApi.testConnection();
    connectionResult.value = result;
  } catch {
    connectionResult.value = { ok: false, message: "Erreur réseau" };
  } finally {
    testingConnection.value = false;
  }
}

/* ── Maintenance ─────────────────────────────────────────────────── */
async function runMaintenance() {
  maintenanceRunning.value = true;
  try {
    const result = await tutorApi.runMaintenance();
    statusMessage.value = "Sauvegarde : " + (result.backup_path || "créée");
    statusType.value = "ok";
  } catch (e: unknown) {
    statusMessage.value = "Maintenance impossible : " + (e instanceof Error ? e.message : String(e));
    statusType.value = "error";
  } finally {
    maintenanceRunning.value = false;
  }
}

/* ── Restart server ──────────────────────────────────────────────── */
async function restartServer() {
  if (!window.confirm("Redémarrer le serveur EduNexus ?\nLa page se rechargera automatiquement.")) return;
  restarting.value = true;
  statusMessage.value = "Redémarrage…";
  statusType.value = "";
  try {
    await tutorApi.restartServer();
    statusMessage.value = "✓ Serveur en cours de redémarrage…";
    statusType.value = "ok";
    setTimeout(() => { location.reload(); }, 3000);
  } catch (e: unknown) {
    statusMessage.value = "Erreur : " + (e instanceof Error ? e.message : String(e));
    statusType.value = "error";
    restarting.value = false;
  }
}


</script>

<template>
  <section class="page settings-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('settings.kicker') }}</p>
        <h1>{{ t('settings.title') }}</h1>
        <p>{{ t('settings.copy') }}</p>
      </div>
    </header>

    <div v-if="loading" class="settings-loading">
      <LoaderCircle :size="24" class="spin" aria-hidden="true" />
      <span>Chargement des réglages…</span>
    </div>

    <template v-else>
      <section class="settings-layout">
        <article class="content-panel settings-form">

          <!-- ═══════════════ Section 1: Modèle & Fournisseur ═══════════════ -->
          <div class="settings-section">
            <div class="settings-icon">
              <Plug :size="20" aria-hidden="true" />
            </div>
            <div>
              <p class="eyebrow">Fournisseur LLM</p>
              <h2>Modèle &amp; Fournisseur</h2>
              <p>Configurez la connexion à votre moteur de raisonnement.</p>
            </div>
            <div class="form-grid">
              <label>
                Fournisseur
                <select v-model="tutor.llm_provider">
                  <option value="ollama">Ollama (local)</option>
                  <option value="openai">OpenAI-compatible</option>
                </select>
              </label>
              <label>
                URL de base
                <input
                  v-model="tutor.llm_base_url"
                  type="url"
                  placeholder="http://127.0.0.1:11434"
                />
              </label>
              <label>
                Clé API
                <input
                  v-model="tutor.llm_api_key"
                  type="password"
                  placeholder="sk-…"
                />
              </label>
            </div>
            <div class="settings-actions-inline">
              <button
                type="button"
                class="btn btn-sm"
                :disabled="testingConnection"
                @click="testConnection"
              >
                <LoaderCircle v-if="testingConnection" :size="14" class="spin" aria-hidden="true" />
                Tester la connexion
              </button>
              <span
                v-if="connectionResult"
                class="connection-result"
                :class="connectionResult.ok ? 'connection-ok' : 'connection-fail'"
              >
                {{ connectionResult.ok ? '✓' : '✗' }} {{ connectionResult.message }}
              </span>
            </div>
          </div>

          <!-- ═══════════════ Section 2: Paramètres d'inférence ═══════════════ -->
          <div class="settings-section">
            <div class="settings-icon">
              <Cpu :size="20" aria-hidden="true" />
            </div>
            <div>
              <p class="eyebrow">Inférence</p>
              <h2>Paramètres d'inférence</h2>
              <p>Contrôlez le comportement du modèle lors de la génération.</p>
            </div>
            <div class="form-grid">
              <label>
                Temperature
                <input
                  v-model.number="options.temperature"
                  type="number"
                  min="0"
                  max="2"
                  step="0.05"
                  placeholder="1.0"
                />
              </label>
              <label>
                Top P
                <input
                  v-model.number="options.top_p"
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  placeholder="0.9"
                />
              </label>
              <label>
                Top K
                <input
                  v-model.number="options.top_k"
                  type="number"
                  min="1"
                  max="100"
                  step="1"
                  placeholder="40"
                />
              </label>
              <label>
                Num CTX
                <input
                  v-model.number="options.num_ctx"
                  type="number"
                  min="512"
                  max="131072"
                  step="512"
                  placeholder="8192"
                />
              </label>
              <label>
                Num Predict
                <input
                  v-model.number="options.num_predict"
                  type="number"
                  min="-1"
                  max="32768"
                  step="1"
                  placeholder="1024"
                />
              </label>
              <label>
                Repeat Penalty
                <input
                  v-model.number="options.repeat_penalty"
                  type="number"
                  min="1.0"
                  max="2.0"
                  step="0.05"
                  placeholder="1.1"
                />
              </label>
              <label>
                Num Thread
                <input
                  v-model.number="options.num_thread"
                  type="number"
                  min="1"
                  max="32"
                  step="1"
                  placeholder="3"
                />
              </label>
              <label>
                Keep Alive
                <input
                  v-model="options.keep_alive"
                  type="text"
                  placeholder="5m"
                />
              </label>
            </div>
          </div>

          <!-- ═══════════════ Section 3: Indexation ═══════════════ -->
          <div class="settings-section">
            <div class="settings-icon">
              <Database :size="20" aria-hidden="true" />
            </div>
            <div>
              <p class="eyebrow">Ressources locales</p>
              <h2>Indexation</h2>
              <p>Commencez avec un batch de 16 et une concurrence de 1 sur un ordinateur modeste.</p>
            </div>
            <div class="form-grid">
              <label>
                Taille des lots d'embeddings
                <input
                  v-model.number="tutor.embed_batch_size"
                  type="number"
                  min="1"
                  max="64"
                  step="1"
                  placeholder="16"
                />
              </label>
              <label>
                Requêtes simultanées
                <input
                  v-model.number="tutor.max_parallel_embed"
                  type="number"
                  min="1"
                  max="8"
                  step="1"
                  placeholder="1"
                />
              </label>
            </div>
          </div>

          <!-- ═══════════════ Section 4: Planification nocturne ═══════════════ -->
          <div class="settings-section">
            <div class="settings-icon">
              <Moon :size="20" aria-hidden="true" />
            </div>
            <div>
              <p class="eyebrow">Automatisations</p>
              <h2>Planification nocturne</h2>
              <p>La file traite les documents un par un et peut préparer des contenus pédagogiques après l'indexation.</p>
            </div>
            <div class="form-grid">
              <label class="toggle-row">
                <span>
                  <strong>Planificateur nocturne actif</strong>
                  <small>Activer le traitement automatique la nuit</small>
                </span>
                <input v-model="tutor.nightly_enabled" type="checkbox" />
              </label>
              <label class="toggle-row">
                <span>
                  <strong>Seulement sur secteur</strong>
                  <small>Ne pas utiliser la batterie</small>
                </span>
                <input v-model="tutor.nightly_only_on_ac" type="checkbox" />
              </label>
              <label>
                Début
                <input v-model="tutor.nightly_start_at" type="time" />
              </label>
              <label>
                Fin
                <input v-model="tutor.nightly_stop_at" type="time" />
              </label>
              <label>
                Durée maximale (minutes)
                <input
                  v-model.number="tutor.nightly_max_runtime_minutes"
                  type="number"
                  min="1"
                  max="1440"
                  step="1"
                  placeholder="420"
                />
              </label>
              <label class="toggle-row">
                <span>
                  <strong>Préparer fiches et glossaire la nuit</strong>
                  <small>Après une indexation réussie</small>
                </span>
                <input v-model="tutor.nightly_prepare_enabled" type="checkbox" />
              </label>
            </div>
            <p class="hint" style="margin: 8px 0 0;">
              Réglage conseillé sur i5-7300U : lots 16, concurrence 1. Le pré-calcul pédagogique utilise le LLM local et peut prolonger la tâche.
            </p>
            <div class="settings-actions-inline" style="margin-top: 10px;">
              <span class="hint">{{ nightlyStatus }}</span>
            </div>
          </div>

          <!-- ═══════════════ Section 5: Tutorat ═══════════════ -->
          <div class="settings-section">
            <div class="settings-icon">
              <ShieldCheck :size="20" aria-hidden="true" />
            </div>
            <div>
              <p class="eyebrow">Tutorat</p>
              <h2>Pédagogie</h2>
              <p>Paramètres d'adaptation du tuteur IA.</p>
            </div>
            <div class="form-grid">
              <label class="toggle-row">
                <span>
                  <strong>Mode réflexion (Think)</strong>
                  <small>Afficher le raisonnement interne</small>
                </span>
                <input v-model="tutor.think" type="checkbox" />
              </label>
              <label class="toggle-row">
                <span>
                  <strong>Pédagogie socratique</strong>
                  <small>Guider par questions plutôt que donner la réponse</small>
                </span>
                <input v-model="tutor.socratic" type="checkbox" />
              </label>
              <label>
                Niveau cible
                <select v-model="tutor.level">
                  <option value="beginner">Débutant</option>
                  <option value="intermediate">Intermédiaire</option>
                  <option value="advanced">Avancé</option>
                </select>
              </label>
              <label>
                Top K conocimiento
                <input
                  v-model.number="tutor.top_k"
                  type="number"
                  min="1"
                  max="20"
                  step="1"
                  placeholder="5"
                />
              </label>
            </div>
          </div>

          <!-- ═══════════════ Section 6: Système ═══════════════ -->
          <div class="settings-section settings-system">
            <div>
              <p class="eyebrow">Système</p>
              <h2>Maintenance &amp; redémarrage</h2>
            </div>
            <div class="settings-actions-inline" style="margin-top: 10px;">
              <button
                type="button"
                class="btn btn-sm"
                :disabled="maintenanceRunning"
                @click="runMaintenance"
              >
                <LoaderCircle v-if="maintenanceRunning" :size="14" class="spin" aria-hidden="true" />
                Vérifier et sauvegarder la bibliothèque
              </button>
              <button
                type="button"
                class="btn btn-sm btn-danger"
                :disabled="restarting"
                @click="restartServer"
              >
                <RotateCcw v-if="!restarting" :size="14" aria-hidden="true" />
                <LoaderCircle v-else :size="14" class="spin" aria-hidden="true" />
                Redémarrer le serveur
              </button>
            </div>
          </div>

          <!-- ═══════════════ Save footer ═══════════════ -->
          <div class="settings-actions">
            <button
              type="button"
              class="primary-action save-button"
              :disabled="saving"
              @click="saveAllSettings"
            >
              <LoaderCircle v-if="saving" :size="17" class="spin" aria-hidden="true" />
              <Check v-else :size="17" aria-hidden="true" />
              {{ saved ? t('settings.saved') : t('settings.save') }}
            </button>
            <span
              v-if="statusMessage"
              class="hint"
              :class="{
                'status-ok': statusType === 'ok',
                'status-error': statusType === 'error',
              }"
            >
              {{ statusMessage }}
            </span>
          </div>
        </article>

        <!-- ═══════════════ Sidebar ═══════════════ -->
        <aside class="settings-aside">
          <article class="content-panel">
            <ShieldCheck :size="22" aria-hidden="true" />
            <p class="eyebrow">{{ t('settings.privacy') }}</p>
            <h3>{{ t('settings.local') }}</h3>
            <p>{{ t('settings.localCopy') }}</p>
          </article>
        </aside>
      </section>
    </template>
  </section>
</template>

<style scoped>
.settings-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--muted, #6b7280);
  font-size: 14px;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.settings-actions-inline {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.connection-result {
  font-size: 13px;
  font-weight: 500;
}

.connection-ok {
  color: #2d8a4e;
}

.connection-fail {
  color: #c0392b;
}

.settings-system {
  border-bottom: none;
}

.settings-system .eyebrow,
.settings-system h2 {
  grid-column: 1 / -1;
}

.btn-danger {
  background: var(--danger, #e05555);
  color: #fff;
}

.btn-danger:hover {
  background: var(--danger-hover, #c93c3c);
}

.status-ok {
  color: #2d8a4e;
}

.status-error {
  color: #c0392b;
}
</style>
