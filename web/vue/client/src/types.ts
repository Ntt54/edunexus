export type ActivityType = "concept" | "reading" | "exercise" | "quiz" | "flashcard_review";
export type StepStatus = "pending" | "completed";
export type MasteryState = "à revoir" | "en cours" | "maîtrisé";

export interface Subject { id: string; name: string; level: string; }

export interface LearningStep {
  id: string;
  title: string;
  activityType: ActivityType;
  duration: number;
  status: StepStatus;
  source: string;
}

export interface LearningPath {
  id: string;
  title: string;
  description: string;
  progress: number;
  status: "active" | "draft" | "completed";
  steps: LearningStep[];
}

export interface ConceptProgress {
  id: string;
  name: string;
  score: number;
  recentFailures: number;
  nextReview?: string;
}

export interface ReviewItem { id: string; concept: string; due: string; source: string; }

export interface SourceBook {
  id: string;
  title: string;
  chapter: string;
  status: "indexed" | "indexing" | "pending" | "error";
  updatedAt: string;
  sourceType: "PDF" | "EPUB" | "Note";
  subject_id?: string;
  category_ids?: number[];
  format?: string;
  pages?: number;
  chunks_total?: number;
}

export interface LibraryCategory {
  id: number;
  name: string;
  book_count: number;
}

export interface SearchResult {
  score: number;
  text: string;
  book_id: string;
  book_title?: string;
  chapter?: string;
  page?: number;
}

export interface QueueStatus {
  running: boolean;
  pending_count: number;
  completed_count: number;
  current_title?: string;
}

export interface QueueState {
  running: boolean;
  currentBookTitle?: string;
  pendingCount: number;
  completedCount: number;
}

export interface DashboardData {
  subject: Subject;
  path: LearningPath;
  concepts: ConceptProgress[];
  reviews: ReviewItem[];
  books: SourceBook[];
  queue: QueueState;
}

/* ── Graphe de compétences ─────────────────────────────────────────── */

export type ValidationStatus = "confirmed" | "unconfirmed" | "rejected";

export interface GraphNode {
  id: string;
  name: string;
  description?: string;
  mastery_score: number;
  confidence: number;
  validation_status: ValidationStatus;
  source_concept_id?: string;
}

export interface GraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  relation: string;
}

export interface CompetencyGraph {
  subject_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  built_at?: string;
}

export interface GraphDashboardItem {
  id: string;
  title: string;
  mastery_score: number;
  confidence: number;
  validation_status: string;
  sources: unknown[];
}

export interface ContradictoryEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphDashboard {
  subject_id: string;
  covered: GraphDashboardItem[];
  uncovered: GraphDashboardItem[];
  contradictory: ContradictoryEdge[];
  unconfirmed: GraphDashboardItem[];
}
