import { createRouter, createWebHashHistory } from "vue-router";
import DashboardView from "@/views/DashboardView.vue";
import PathView from "@/views/PathView.vue";
import LearningView from "@/views/LearningView.vue";
import LibraryView from "@/views/LibraryView.vue";
import PracticeView from "@/views/PracticeView.vue";
import QuizView from "@/views/QuizView.vue";
import ProgressView from "@/views/ProgressView.vue";
import TutorView from "@/views/TutorView.vue";
import SettingsView from "@/views/SettingsView.vue";
import GraphView from "@/views/GraphView.vue";
import DashboardSubjectView from "@/views/DashboardSubjectView.vue";
import NotebookView from "@/views/NotebookView.vue";
import CaptureView from "@/views/CaptureView.vue";
import ProfileView from "@/views/ProfileView.vue";
import LearnersView from "@/views/LearnersView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "dashboard", component: DashboardView },
    { path: "/parcours", name: "path", component: PathView },
    { path: "/reviser", name: "learning", component: LearningView },
    { path: "/sources", name: "library", component: LibraryView },
    { path: "/exercices", name: "practice", component: PracticeView },
    { path: "/quiz", name: "quiz", component: QuizView },
    { path: "/progression", name: "progress", component: ProgressView },
    { path: "/tuteur", name: "tutor", component: TutorView },
    { path: "/reglages", name: "settings", component: SettingsView },
    // Feature 008 — EduNexus adaptatif
    { path: "/graphe", name: "graph", component: GraphView },
    { path: "/tableau", name: "subjectDash", component: DashboardSubjectView },
    { path: "/carnet", name: "notebook", component: NotebookView },
    { path: "/capture", name: "capture", component: CaptureView },
    { path: "/profil", name: "profile", component: ProfileView },
    { path: "/apprenants", name: "learners", component: LearnersView },
  ],
});
