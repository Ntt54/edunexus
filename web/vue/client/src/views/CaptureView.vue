<!-- EduNexus UI direction: Capture de programme — importer et valider l'arbre de compétences depuis un fichier. -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowRight, Check, CheckCircle2, FileImage, FileText, Loader2, PencilLine, Upload } from "lucide-vue-next";
import StatusPill from "@/components/StatusPill.vue";
import { useLearningStore } from "@/stores/learning";
import { usePreferences } from "@/stores/preferences";

const { state } = useLearningStore();
const { t } = usePreferences();

/** ID de la matière active */
const subjectId = computed(() => state.data?.subject.id ?? "");

/* ── Types du programme ─────────────────────────────────────────── */
type NodeKind = "chapter" | "sub_part" | "competency";
type ValidationStatus = "confirmed" | "unconfirmed" | "rejected";

interface ProgramNode {
  id: string;
  kind: NodeKind;
  title: string;
  validation_status: ValidationStatus;
  children?: ProgramNode[];
}

interface Program {
  id: string;
  title: string;
  nodes: ProgramNode[];
  confirmed: boolean;
}

/* ── État local ─────────────────────────────────────────────────── */
const filePath = ref("");
const sourceType = ref<"photo" | "pdf">("pdf");
const program = ref<Program | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

/** Nœud en cours d'édition */
const editingNodeId = ref<string | null>(null);
const editTitle = ref("");
const savingNode = ref(false);

/** Chargement d'un programme existant au montage */
async function fetchProgram() {
  if (!subjectId.value) return;
  loading.value = true;
  error.value = null;
  try {
    const response = await fetch(`/api/tutor/subjects/${subjectId.value}/program`);
    if (response.status === 404) { program.value = null; return; }
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    program.value = await response.json();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de charger le programme.";
  } finally {
    loading.value = false;
  }
}

/** Capturer un programme depuis un fichier */
async function captureProgram() {
  if (!filePath.value.trim() || !subjectId.value) return;
  loading.value = true;
  error.value = null;
  try {
    const response = await fetch(`/api/tutor/subjects/${subjectId.value}/program/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: filePath.value.trim(), source_type: sourceType.value }),
    });
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    program.value = await response.json();
    filePath.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de capturer le programme.";
  } finally {
    loading.value = false;
  }
}

/** Ouvrir l'édition d'un nœud */
function startEdit(nodeId: string, currentTitle: string) {
  editingNodeId.value = nodeId;
  editTitle.value = currentTitle;
}

/** Sauvegarder le titre d'un nœud */
async function saveNodeTitle(nodeId: string) {
  if (!editTitle.value.trim() || !subjectId.value || !program.value) return;
  savingNode.value = true;
  try {
    const response = await fetch(
      `/api/tutor/subjects/${subjectId.value}/program/${program.value.id}/nodes/${nodeId}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: editTitle.value.trim() }),
      },
    );
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    // Mettre à jour le nœud dans l'arbre local
    updateNodeTitle(program.value.nodes, nodeId, editTitle.value.trim());
    editingNodeId.value = null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de sauvegarder le nœud.";
  } finally {
    savingNode.value = false;
  }
}

/** Récurse dans l'arbre pour mettre à jour un titre */
function updateNodeTitle(nodes: ProgramNode[], targetId: string, newTitle: string): boolean {
  for (const node of nodes) {
    if (node.id === targetId) { node.title = newTitle; return true; }
    if (node.children && updateNodeTitle(node.children, targetId, newTitle)) return true;
  }
  return false;
}

/** Confirmer le programme */
async function confirmProgram() {
  if (!subjectId.value || !program.value) return;
  loading.value = true;
  error.value = null;
  try {
    const response = await fetch(
      `/api/tutor/subjects/${subjectId.value}/program/${program.value.id}/confirm`,
      { method: "POST" },
    );
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    program.value.confirmed = true;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de confirmer le programme.";
  } finally {
    loading.value = false;
  }
}

/** Badge de kind */
const kindLabel = (kind: NodeKind) => t(`capture.kind.${kind}`);
const kindTone = (kind: NodeKind): "indigo" | "orange" | "green" => kind === "chapter" ? "indigo" : kind === "sub_part" ? "orange" : "green";
const statusTone = (status: ValidationStatus): "green" | "orange" | "slate" => status === "confirmed" ? "green" : status === "unconfirmed" ? "orange" : "slate";

/** État vide */
const isEmpty = computed(() => !loading && !error && !program.value);

/** Génération de parcours depuis le programme OCR */
const generatingPath = ref(false);

async function generatePathFromProgram() {
  if (!subjectId.value || !program.value) return;
  generatingPath.value = true;
  try {
    const response = await fetch(`/api/tutor/subjects/${subjectId.value}/path/from-program`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ program_id: program.value.id }),
    });
    if (!response.ok) throw new Error(`Erreur ${response.status}`);
    const result = await response.json();
    // Redirect to the path editor
    window.location.hash = "#/parcours";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Impossible de générer le parcours.";
  } finally {
    generatingPath.value = false;
  }
}

onMounted(fetchProgram);
</script>

<template>
  <!-- Chargement -->
  <section v-if="loading && !program" class="page loading-state">
    <Loader2 :size="24" class="spin" aria-hidden="true" />
    <p>{{ t('capture.loading') }}</p>
  </section>

  <!-- Erreur -->
  <section v-else-if="error && !program" class="page loading-state">
    <p class="error-text">{{ error }}</p>
    <button type="button" class="secondary-action" @click="fetchProgram">{{ t('capture.retry') }}</button>
  </section>

  <!-- État vide : formulaire de capture -->
  <section v-else-if="isEmpty" class="page capture-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('capture.kicker') }}</p>
        <h1>{{ t('capture.emptyTitle') }}</h1>
        <p>{{ t('capture.emptyCopy') }}</p>
      </div>
    </header>

    <article class="content-panel capture-form-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('capture.formSection') }}</p>
          <h2>{{ t('capture.formTitle') }}</h2>
        </div>
        <Upload :size="20" aria-hidden="true" />
      </div>

      <form class="capture-form" @submit.prevent="captureProgram">
        <label class="field-label" for="capture-path">{{ t('capture.filePath') }}</label>
        <input
          id="capture-path"
          v-model="filePath"
          type="text"
          :placeholder="t('capture.pathPlaceholder')"
          :aria-label="t('capture.filePath')"
        />

        <label class="field-label" for="capture-source">{{ t('capture.sourceType') }}</label>
        <select id="capture-source" v-model="sourceType">
          <option value="pdf">{{ t('capture.typePdf') }}</option>
          <option value="photo">{{ t('capture.typePhoto') }}</option>
        </select>

        <button type="submit" class="primary-action" :disabled="!filePath.trim() || loading">
          <Loader2 v-if="loading" :size="16" class="spin" aria-hidden="true" />
          <Upload v-else :size="16" aria-hidden="true" />
          {{ t('capture.captureButton') }}
        </button>
      </form>
    </article>
  </section>

  <!-- Programme affiché -->
  <section v-else class="page capture-page">
    <header class="page-intro">
      <div>
        <p class="eyebrow">{{ t('capture.kicker') }}</p>
        <h1>{{ program?.title ?? t('capture.programTitle') }}</h1>
        <p>{{ t('capture.programCopy') }}</p>
      </div>
    </header>

    <!-- Erreur éphémère -->
    <p v-if="error" class="error-text">{{ error }}</p>

    <!-- Arbre de nœuds -->
    <section class="content-panel program-tree-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">{{ t('capture.treeSection') }}</p>
          <h2>{{ t('capture.treeTitle', { count: program?.nodes.length ?? 0 }) }}</h2>
        </div>
        <StatusPill :tone="program?.confirmed ? 'green' : 'orange'">
          {{ program?.confirmed ? t('capture.confirmed') : t('capture.unconfirmed') }}
        </StatusPill>
      </div>

      <ul class="program-tree">
        <li v-for="node in program?.nodes" :key="node.id" class="tree-node">
          <!-- Nœud racine (chapitre) -->
          <div class="node-row">
            <StatusPill :tone="kindTone(node.kind)">{{ kindLabel(node.kind) }}</StatusPill>

            <!-- Mode édition -->
            <template v-if="editingNodeId === node.id">
              <input
                v-model="editTitle"
                type="text"
                class="node-edit-input"
                :aria-label="t('capture.editTitle')"
                @keyup.enter="saveNodeTitle(node.id)"
                @keyup.escape="editingNodeId = null"
              />
              <button type="button" class="icon-button" :disabled="savingNode" @click="saveNodeTitle(node.id)">
                <Check :size="15" />
              </button>
            </template>

            <!-- Mode lecture -->
            <template v-else>
              <strong>{{ node.title }}</strong>
              <StatusPill :tone="statusTone(node.validation_status)">
                {{ t(`capture.status.${node.validation_status}`) }}
              </StatusPill>
              <button type="button" class="icon-button" :aria-label="t('capture.correct')" @click="startEdit(node.id, node.title)">
                <PencilLine :size="15" />
              </button>
            </template>
          </div>

          <!-- Sous-nœuds (parties / compétences) -->
          <ul v-if="node.children?.length" class="tree-children">
            <li v-for="child in node.children" :key="child.id" class="tree-node child">
              <div class="node-row">
                <StatusPill :tone="kindTone(child.kind)">{{ kindLabel(child.kind) }}</StatusPill>

                <template v-if="editingNodeId === child.id">
                  <input
                    v-model="editTitle"
                    type="text"
                    class="node-edit-input"
                    :aria-label="t('capture.editTitle')"
                    @keyup.enter="saveNodeTitle(child.id)"
                    @keyup.escape="editingNodeId = null"
                  />
                  <button type="button" class="icon-button" :disabled="savingNode" @click="saveNodeTitle(child.id)">
                    <Check :size="15" />
                  </button>
                </template>

                <template v-else>
                  <strong>{{ child.title }}</strong>
                  <StatusPill :tone="statusTone(child.validation_status)">
                    {{ t(`capture.status.${child.validation_status}`) }}
                  </StatusPill>
                  <button type="button" class="icon-button" :aria-label="t('capture.correct')" @click="startEdit(child.id, child.title)">
                    <PencilLine :size="15" />
                  </button>
                </template>
              </div>

              <!-- Troisième niveau (compétences) -->
              <ul v-if="child.children?.length" class="tree-children">
                <li v-for="leaf in child.children" :key="leaf.id" class="tree-node leaf">
                  <div class="node-row">
                    <StatusPill :tone="kindTone(leaf.kind)">{{ kindLabel(leaf.kind) }}</StatusPill>

                    <template v-if="editingNodeId === leaf.id">
                      <input
                        v-model="editTitle"
                        type="text"
                        class="node-edit-input"
                        :aria-label="t('capture.editTitle')"
                        @keyup.enter="saveNodeTitle(leaf.id)"
                        @keyup.escape="editingNodeId = null"
                      />
                      <button type="button" class="icon-button" :disabled="savingNode" @click="saveNodeTitle(leaf.id)">
                        <Check :size="15" />
                      </button>
                    </template>

                    <template v-else>
                      <strong>{{ leaf.title }}</strong>
                      <StatusPill :tone="statusTone(leaf.validation_status)">
                        {{ t(`capture.status.${leaf.validation_status}`) }}
                      </StatusPill>
                      <button type="button" class="icon-button" :aria-label="t('capture.correct')" @click="startEdit(leaf.id, leaf.title)">
                        <PencilLine :size="15" />
                      </button>
                    </template>
                  </div>
                </li>
              </ul>
            </li>
          </ul>
        </li>
      </ul>
    </section>

    <!-- Actions -->
    <section class="capture-actions">
      <button
        type="button"
        class="primary-action"
        :disabled="program?.confirmed || loading"
        @click="confirmProgram"
      >
        <CheckCircle2 :size="16" aria-hidden="true" />
        {{ t('capture.confirmProgram') }}
      </button>
      <button
        v-if="program?.confirmed"
        type="button"
        class="primary-action"
        :disabled="generatingPath"
        @click="generatePathFromProgram"
      >
        <Loader2 v-if="generatingPath" :size="16" class="spin" aria-hidden="true" />
        <ArrowRight v-else :size="16" aria-hidden="true" />
        {{ generatingPath ? t('path.generating') : t('capture.generatePath') }}
      </button>
      <button type="button" class="secondary-action" @click="program = null">
        {{ t('capture.newCapture') }}
      </button>
    </section>
  </section>
</template>
