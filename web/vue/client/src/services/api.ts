/**
 * EduNexus API adapter — connecte le frontend Vue aux routes FastAPI.
 *
 * Pour activer l'API réelle, définir VITE_EDUNEXUS_DEMO_MODE=false
 * et VITE_EDUNEXUS_API_BASE=http://127.0.0.1:9215/api/tutor
 */
import type {
  CompetencyGraph,
  DashboardData,
  GraphDashboard,
  GraphDashboardItem,
  GraphEdge,
  GraphNode,
  LearningPath,
  LearningStep,
  LibraryCategory,
  QueueState,
  QueueStatus,
  SearchResult,
  SourceBook,
  ValidationStatus,
} from "@/types";

export type {
  CompetencyGraph,
  GraphDashboard,
  GraphDashboardItem,
  GraphEdge,
  GraphNode,
  LibraryCategory,
  QueueStatus,
  SearchResult,
  ValidationStatus,
};

const apiBase = import.meta.env.VITE_EDUNEXUS_API_BASE ?? "/api/tutor";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) throw new Error(`Erreur API ${response.status}`);
  return response.json() as Promise<T>;
}

/** FormData-compatible request — omits Content-Type so the browser sets the multipart boundary. */
async function requestForm<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, { method: "POST", body });
  if (!response.ok) {
    let detail = `Erreur API ${response.status}`;
    try { const j = await response.json(); if (j?.detail) detail = String(j.detail); } catch { /* ignore */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

// ── Types header (engine, models) ──────────────────────────────
export interface EngineInfo { embedding: string; ocr: boolean; }
export interface ModelSources { ollama: string[]; cloud: string[]; }
export interface ModelInfo {
  embedding: string[];
  llm: string[];
  sources: ModelSources;
  current: { embedding: string; llm: string };
}
export interface SubjectInfo { id: string; name: string; }
export interface SubjectsResponse { subjects: SubjectInfo[]; active_id: string | null; }

// ── Types Feature 008 (supplémentaires par rapport à types.ts) ────
export interface GraphBuildResult { nodes: number; edges: number; ai_proposed: number; }
export interface PedagogicalTemplate { id: string; name: string; activities: string[]; proof_types: string[]; default_style: string; }
export interface SubjectProfile {
  subject_id: string; domain: string; level: string; objective: string; deadline: string;
  available_time: string; prerequisites: string[]; competencies: string[];
  explanation_style: string; activities: string[]; mastery_criteria: string[];
  constraints: Record<string, unknown>; template_id: string;
}
export interface LearnerProfile { id: string; name: string; avatar: string; created_at: string; updated_at: string; }
export interface CapturedProgram {
  id: string; subject_id: string; source_type: string; status: string;
  recognized_text: string; validation_status: string; created_at: string;
  nodes: Array<{ id: string; program_id: string; parent_id: string; title: string; kind: string; origin: string; validation_status: string }>;
}
export interface NotebookData {
  notebook: {
    id: string; subject_id: string; notes: string[];
    sources: Array<{ id: string; title: string; chapter: string }>;
    outputs: Array<{ id: string; kind: string; content: string; sources: unknown[]; created_at: string }>;
  };
}
export interface ConversationPhoto {
  id: string; conversation_id: string; path: string;
  recognized_text: string; confirmation_status: string; source_linkage: string;
}

/** Mapping isolé : adapte les contrats FastAPI au frontend. */
export const tutorApi = {
  // ── Header: engine, models, subjects ────────────────────────────
  async getEngine(): Promise<EngineInfo> {
    return request<EngineInfo>("/engine");
  },
  async getModels(): Promise<ModelInfo> {
    return request<ModelInfo>("/models");
  },
  async setModel(model: { embedding?: string; llm?: string }): Promise<ModelInfo> {
    return request<ModelInfo>("/models", {
      method: "PUT",
      body: JSON.stringify(model),
    });
  },
  async getSubjects(): Promise<SubjectsResponse> {
    return request<SubjectsResponse>("/subjects");
  },

  // ── Dashboard existant ─────────────────────────────────────────
  async dashboard(): Promise<DashboardData> {
    const subjects = await request<{ subjects: Array<{ id: string; name: string }> }>("/subjects");
    const subject = subjects.subjects[0];
    if (!subject) throw new Error("Aucune matière disponible");
    const [paths, progress, reviews, books, queue] = await Promise.all([
      request<{ paths: LearningPath[] }>(`/paths?subject_id=${encodeURIComponent(subject.id)}`),
      request<{ progress: DashboardData["concepts"] }>(`/subjects/${subject.id}/progress`),
      request<{ due: DashboardData["reviews"] }>(`/subjects/${subject.id}/reviews/due`),
      request<{ books: SourceBook[] }>("/books"),
      request<QueueState>("/index-queue"),
    ]);
    const path = paths.paths.find((item) => item.status === "active") ?? paths.paths[0];
    if (!path) throw new Error("Aucun parcours disponible");
    return { subject: { ...subject, level: "À définir" }, path, concepts: progress.progress, reviews: reviews.due, books: books.books, queue };
  },
  async setQueue(running: boolean): Promise<QueueState> {
    return request<QueueState>(`/index-queue/${running ? "start" : "stop"}`, { method: "POST" });
  },
  async completeStep(stepId: string): Promise<void> {
    await request(`/paths/steps/${encodeURIComponent(stepId)}/complete`, { method: "POST" });
  },

  // ── Feature 008 — Graphe de compétences (US2) ─────────────────
  async getGraph(subjectId: string): Promise<CompetencyGraph> {
    return request<CompetencyGraph>(`/subjects/${encodeURIComponent(subjectId)}/graph`);
  },
  async buildGraph(subjectId: string): Promise<GraphBuildResult> {
    return request<GraphBuildResult>(`/subjects/${encodeURIComponent(subjectId)}/graph/build`, { method: "POST" });
  },
  async validateNode(nodeId: string): Promise<{ node_id: string; validation_status: string }> {
    return request(`/graph/nodes/${encodeURIComponent(nodeId)}/validate`, { method: "POST" });
  },
  async graphDashboard(subjectId: string): Promise<GraphDashboard> {
    return request<GraphDashboard>(`/subjects/${encodeURIComponent(subjectId)}/graph/dashboard`);
  },

  // ── Feature 008 — Profil pédagogique (US1) ────────────────────
  async getTemplates(): Promise<{ templates: PedagogicalTemplate[] }> {
    return request<{ templates: PedagogicalTemplate[] }>("/pedagogical-templates");
  },
  async getProfile(subjectId: string): Promise<{ profile: SubjectProfile }> {
    return request<{ profile: SubjectProfile }>(`/subjects/${encodeURIComponent(subjectId)}/profile`);
  },
  async saveProfile(subjectId: string, profile: Partial<SubjectProfile>): Promise<{ profile: SubjectProfile }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/profile`, { method: "PUT", body: JSON.stringify(profile) });
  },
  async interpretGoal(subjectId: string, goal: string): Promise<{ parameters: Record<string, unknown> }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/profile/interpret-goal`, { method: "POST", body: JSON.stringify({ goal }) });
  },

  // ── Feature 008 — Multi-utilisateur familial (US9) ─────────────
  async getLearners(): Promise<{ learners: LearnerProfile[] }> {
    return request<{ learners: LearnerProfile[] }>("/learners");
  },
  async createLearner(name: string, avatar = ""): Promise<LearnerProfile> {
    return request("/learners", { method: "POST", body: JSON.stringify({ name, avatar }) });
  },
  async activateLearner(learnerId: string): Promise<{ learner: LearnerProfile; subjects: unknown[] }> {
    return request(`/learners/${encodeURIComponent(learnerId)}/activate`, { method: "POST" });
  },
  async deleteLearner(learnerId: string): Promise<{ deleted: string }> {
    return request(`/learners/${encodeURIComponent(learnerId)}`, { method: "DELETE" });
  },

  // ── Feature 008 — Capture de programme (US6) ──────────────────
  async captureProgram(subjectId: string, path: string, sourceType = "photo"): Promise<{ program: CapturedProgram }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/program/capture`, {
      method: "POST", body: JSON.stringify({ path, source_type: sourceType }),
    });
  },
  async getProgram(subjectId: string, programId: string): Promise<{ program: CapturedProgram }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/program/${encodeURIComponent(programId)}`);
  },
  async correctProgramNode(subjectId: string, programId: string, nodeId: string, title: string): Promise<{ id: string; title: string; validation_status: string }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/program/${encodeURIComponent(programId)}/nodes/${encodeURIComponent(nodeId)}`, {
      method: "PUT", body: JSON.stringify({ title }),
    });
  },
  async confirmProgram(subjectId: string, programId: string): Promise<{ program: CapturedProgram }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/program/${encodeURIComponent(programId)}/confirm`, { method: "POST" });
  },

  // ── Feature 008 — Carnet de matière (US8) ─────────────────────
  async getNotebook(subjectId: string): Promise<NotebookData> {
    return request<NotebookData>(`/subjects/${encodeURIComponent(subjectId)}/notebook`);
  },
  async addNotebookNote(subjectId: string, note: string): Promise<{ notebook: { notes: string[] } }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/notebook/notes`, {
      method: "POST", body: JSON.stringify({ note }),
    });
  },
  async runNotebookAction(subjectId: string, action: string): Promise<{ output: { id: string; kind: string; content: string; sources: unknown[]; created_at: string } }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/notebook/actions`, {
      method: "POST", body: JSON.stringify({ action, params: {} }),
    });
  },
  async deleteNotebookOutput(outputId: string): Promise<{ deleted: boolean }> {
    return request(`/notebook-outputs/${encodeURIComponent(outputId)}`, { method: "DELETE" });
  },

  // ── Feature 008 — Photo de conversation (US7) ─────────────────
  async importConversationPhoto(conversationId: string, path: string): Promise<ConversationPhoto> {
    return request(`/conversations/${encodeURIComponent(conversationId)}/photo`, {
      method: "POST", body: JSON.stringify({ path }),
    });
  },
  async confirmConversationPhoto(photoId: string): Promise<ConversationPhoto> {
    return request(`/conversation-photos/${encodeURIComponent(photoId)}/confirm`, { method: "POST" });
  },

  // ── Settings ────────────────────────────────────────────────────
  async getSettings(): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/settings");
  },
  async saveSettings(settings: Record<string, unknown>): Promise<void> {
    await request("/settings", { method: "PUT", body: JSON.stringify(settings) });
  },
  async testConnection(): Promise<{ ok: boolean; message: string; models?: string[] }> {
    return request<{ ok: boolean; message: string; models?: string[] }>("/test-connection", { method: "POST" });
  },

  // ── Nightly scheduler ───────────────────────────────────────────
  async getNightly(): Promise<{ scheduler_running: boolean; enabled: boolean; queue?: { pending_count: number } }> {
    return request("/nightly");
  },
  async startNightly(): Promise<void> {
    await request("/nightly/start", { method: "POST" });
  },
  async stopNightly(): Promise<void> {
    await request("/nightly/stop", { method: "POST" });
  },

  // ── Maintenance ─────────────────────────────────────────────────
  async runMaintenance(): Promise<{ backup_path?: string; message: string }> {
    return request("/maintenance", {
      method: "POST",
      body: JSON.stringify({ backup: true, vacuum: false }),
    });
  },

  // ── Restart ─────────────────────────────────────────────────────
  async restartServer(): Promise<void> {
    await request("/restart", { method: "POST" });
  },

  // ── Feature 008 — Adaptation (US4) ────────────────────────────
  async getStabilityPortion(subjectId: string): Promise<{ objective: string; main_notion: string; success_criterion: string }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/adaptation/stability`);
  },
  async recomputeWindow(subjectId: string, anchorStepId?: string): Promise<{ path: unknown; window: string[] }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/adaptation/recompute`, {
      method: "POST", body: JSON.stringify({ anchor_step_id: anchorStepId ?? null }),
    });
  },

  // ── Feature 008 — Parcours adaptatif (US3) ────────────────────
  async generatePath(subjectId: string): Promise<{ path: unknown }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/path/generate`, { method: "POST" });
  },
  async reorderPath(subjectId: string, steps: Array<{ id: string; excluded?: boolean }>): Promise<{ path: unknown }> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/path`, { method: "PUT", body: JSON.stringify({ steps }) });
  },

  // ── Génération de parcours (depuis livres / OCR) ─────────────
  async generateFromBooks(subjectId: string, bookIds: string[]): Promise<unknown> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/path/generate-from-books`, {
      method: "POST", body: JSON.stringify({ book_ids: bookIds }),
    });
  },
  async pathFromProgram(subjectId: string, programId: string): Promise<unknown> {
    return request(`/subjects/${encodeURIComponent(subjectId)}/path/from-program`, {
      method: "POST", body: JSON.stringify({ program_id: programId }),
    });
  },

  // ── Conversations (tutor chat) ──────────────────────────────
  async getConversations(): Promise<{
    conversations: Array<{
      id: string; title: string; subject_id: string; subject_name?: string;
      created_at: string; updated_at?: string; started_at?: string;
      message_count?: number; source_count?: number; active_sources?: number;
    }>;
  }> {
    return request("/conversations");
  },
  async createConversation(subjectId: string, title?: string): Promise<{
    conversation: { id: string; title: string; subject_id: string };
  }> {
    return request("/conversations", {
      method: "POST",
      body: JSON.stringify({ subject_id: subjectId, ...(title ? { title } : {}) }),
    });
  },
  async getConversation(id: string): Promise<{
    conversation: {
      id: string; title: string; messages: Array<{ role: string; content: string }>;
    };
  }> {
    return request(`/conversations/${encodeURIComponent(id)}`);
  },
  async renameConversation(id: string, title: string): Promise<void> {
    await request(`/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },
  async deleteConversation(id: string): Promise<void> {
    await request(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  async getConversationSources(id: string): Promise<{ book_ids: string[] }> {
    return request(`/conversations/${encodeURIComponent(id)}/sources`);
  },
  async setConversationSources(id: string, bookIds: string[]): Promise<void> {
    await request(`/conversations/${encodeURIComponent(id)}/sources`, {
      method: "PUT",
      body: JSON.stringify({ book_ids: bookIds }),
    });
  },

  // ── Library / Bibliothèque ─────────────────────────────────────
  async getCategories(): Promise<{ categories: LibraryCategory[] }> {
    return request<{ categories: LibraryCategory[] }>("/categories");
  },
  async createCategory(name: string): Promise<{ category_id: number }> {
    return request("/categories", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  async renameCategory(id: number, name: string): Promise<void> {
    await request(`/categories/${id}/rename`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  async deleteCategory(id: number): Promise<void> {
    await request(`/categories/${id}`, { method: "DELETE" });
  },
  async autoClassify(): Promise<{ assignments: Array<{ book_id: string; category: string }>; categories_created?: string[]; failed_batches?: number; message?: string }> {
    return request("/categories/auto-classify", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  async getBooks(subject?: string): Promise<{ books: SourceBook[] }> {
    const qs = subject ? `?subject=${encodeURIComponent(subject)}` : "";
    return request<{ books: SourceBook[] }>(`/books${qs}`);
  },
  async importDocument(file: File, subject: string, fmt?: string, queue = true): Promise<{ book_id: string }> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    fd.append("queue", queue ? "true" : "false");
    if (fmt) fd.append("fmt", fmt);
    return requestForm<{ book_id: string }>("/import", fd);
  },
  async deleteBook(id: string): Promise<void> {
    await request(`/books/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  async reindexBook(id: string): Promise<void> {
    await request(`/books/${encodeURIComponent(id)}/reindex`, { method: "POST" });
  },
  async getBookCategories(bookId: string): Promise<{ categories: Array<{ id: number; name: string }> }> {
    return request(`/books/${encodeURIComponent(bookId)}/categories`);
  },
  async setBookCategories(bookId: string, categoryIds: number[]): Promise<void> {
    await request(`/books/${encodeURIComponent(bookId)}/categories`, {
      method: "PUT",
      body: JSON.stringify({ category_ids: categoryIds }),
    });
  },
  async removeBookCategory(bookId: string, categoryId: number): Promise<void> {
    await request(`/books/${encodeURIComponent(bookId)}/categories/${categoryId}`, {
      method: "DELETE",
    });
  },
  async searchSemantic(subject: string, query: string, k = 6): Promise<{ results: SearchResult[] }> {
    return request("/search", {
      method: "POST",
      body: JSON.stringify({ subject, query, k }),
    });
  },
  async getQueueStatus(): Promise<QueueStatus> {
    return request<QueueStatus>("/index-queue");
  },
  async startQueue(): Promise<QueueStatus> {
    return request<QueueStatus>("/index-queue/start", { method: "POST" });
  },
  async stopQueue(): Promise<QueueStatus> {
    return request<QueueStatus>("/index-queue/stop", { method: "POST" });
  },
  async getIndexStatus(): Promise<{ books: Array<{ id: string; status: string }> }> {
    return request("/index-status");
  },

  // ── Learning paths CRUD ──────────────────────────────────────────
  async getPaths(subjectId: string): Promise<{ paths: Array<{ id: string; title: string; description: string; status: string; progress?: number }> }> {
    return request(`/paths?subject_id=${encodeURIComponent(subjectId)}`);
  },
  async createPath(subjectId: string, title: string, description = ""): Promise<{ id: string; title: string; description: string; status: string }> {
    return request("/paths", { method: "POST", body: JSON.stringify({ subject_id: subjectId, title, description }) });
  },
  async getPath(pathId: string): Promise<{ id: string; title: string; description: string; status: string; progress: number; steps: LearningStep[] }> {
    return request(`/paths/${encodeURIComponent(pathId)}`);
  },
  async updatePath(pathId: string, patch: { title?: string; description?: string; status?: string }): Promise<void> {
    await request(`/paths/${encodeURIComponent(pathId)}`, { method: "PUT", body: JSON.stringify(patch) });
  },
  async deletePath(pathId: string): Promise<void> {
    await request(`/paths/${encodeURIComponent(pathId)}`, { method: "DELETE" });
  },
  async addPathStep(pathId: string, activityType: string, activityId: string, title = ""): Promise<LearningStep> {
    return request(`/paths/${encodeURIComponent(pathId)}/steps`, { method: "POST", body: JSON.stringify({ activity_type: activityType, activity_id: activityId, title }) });
  },
  async reorderPathSteps(pathId: string, stepIds: string[]): Promise<void> {
    await request(`/paths/${encodeURIComponent(pathId)}/steps/reorder`, { method: "PUT", body: JSON.stringify({ step_ids: stepIds }) });
  },
  async deletePathStep(stepId: string): Promise<void> {
    await request(`/paths/steps/${encodeURIComponent(stepId)}`, { method: "DELETE" });
  },
};
