# API Contracts — 005-platform-ui-library

**Date** : 2026-08-25
Ce document décrit les **deltas** par rapport à l'API existante (59 routes +
WS `/ws/tutor`, voir `specs/004-local-ai-tutor/contracts/`). Toutes les
nouvelles routes sont préfixées `/api/tutor`, couvertes par le garde
same-origin existant, et renvoient des erreurs JSON `{detail}`.

## 1. Conversations

### GET /api/tutor/conversations
```json
{ "conversations": [
  { "id": "496fe88b", "title": "Apprendre Java", "subject_id": "abc123",
    "subject_name": "Réseaux", "updated_at": 1756123456.0,
    "message_count": 14, "active_sources": 4 }
] }
```
Trié par `updated_at` décroissant. Liste TOUTES les conversations.

### POST /api/tutor/conversations
Requête : `{"title": "Apprendre Java", "subject_id": "abc123"}` (les deux
optionnels ; titre défaut « Sans titre »).
Réponse 200 : `{"conversation": {"id","title","subject_id","created_at"}}`.
Effet : crée la session sous-jacente ; n'affecte aucune autre conversation.

### PATCH /api/tutor/conversations/{id}
Requête : `{"title": "Nouveau nom"}` → Réponse : `{"conversation": {...}}`.
404 si inconnue.

### DELETE /api/tutor/conversations/{id}
Réponse : `{"deleted": true}`. Supprime messages + sources actives (cascade,
journalisée). 404 si inconnue.

## 2. Sources actives d'une conversation

### GET /api/tutor/conversations/{id}/sources
```json
{ "book_ids": ["79dd9a2a"], 
  "books": [{"id":"79dd9a2a","title":"reseaux_test"}] }
```

### PUT /api/tutor/conversations/{id}/sources
Requête : `{"book_ids": ["79dd9a2a", "..."]}` (remplacement complet ;
liste vide = aucune source active).
Réponse : `{"active": 2}`.
Invariants : dédupliqué ; ids inconnus ignorés ; ne touche jamais à la
bibliothèque ni aux embeddings.

## 3. Frame WS `ask` — extension additive

Champs EXISTANTS inchangés (`type`,`subject`,`question`,`think`,`socratic`,
`level`). Nouveaux champs optionnels :

```json
{ "type": "ask", "subject": "Réseaux",
  "question": "…",
  "conversation_id": "496fe88b",
  "book_ids": ["79dd9a2a"] }
```

- `conversation_id` : associe la réponse à la conversation (historique) et
  charge ses sources actives si `book_ids` absent.
- `book_ids` : périmètre RAG explicite (prioritaire sur les sources de la
  conversation) ; liste vide ⇒ mode sans contexte documentaire (comportement
  déjà livré).
- Frames émis : INCHANGÉS (`start/sources/content_delta/thinking_delta/
  definition/stats/end/error/cancelled/transcript`).

## 4. Endpoints existants réutilisés tels quels (rappel)

`books`, `import`, `index-status` (avec `books[].status`), `categories`
(CRUD + membership + auto-classify), `subjects/*/concepts|progress|gaps|
prepare|reviews|quizzes|exams|sessions|resume|locate|rank-books|compare|
glossary|map`, `exercises/*`, `reviews/{id}/grade`, `quizzes/{id}/submit`,
`sessions/{id}/close`, `engine`, `models` GET/PUT, `log-error`.

Aucune route existante n'est modifiée ou supprimée.
