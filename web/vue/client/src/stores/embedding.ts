/**
 * Shared reactive state for embedding model / RAG status.
 *
 * AppShell writes `currentEmbedding` when loading or changing models.
 * Any view (PathView, etc.) can read `isEmbeddingDisabled` to adapt
 * behaviour when the user has disabled the embedding model.
 */
import { computed, ref } from "vue";

/** Sentinel values that mean "embedding is off". */
export const EMBED_SENTINELS = new Set(["disabled", "none", "off"]);

/** Current embedding model name (empty string = off, or a sentinel). */
export const currentEmbedding = ref("");

/** Whether RAG embedding is effectively disabled. */
export const isEmbeddingDisabled = computed(() =>
  !currentEmbedding.value || EMBED_SENTINELS.has(currentEmbedding.value),
);
