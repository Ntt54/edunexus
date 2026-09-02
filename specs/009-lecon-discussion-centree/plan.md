# Implementation Plan: Leçon cliquable — discussion centrée sur la notion

**Branch**: `009-lecon-discussion-centree` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-lecon-discussion-centree/spec.md`

## Summary

Transformer chaque leçon d'un parcours existant (3 méthodes) en point d'entrée cliquable qui ouvre une **discussion centrée sur la notion** (ex. "Variables"). Depuis cette vue dédiée (`/lecon/:id`), l'apprenant peut : (1) discuter librement en RAG filtré sur la notion, (2) générer un **cours** complet (800–1200 mots), (3) générer une **synthèse** condensée (150–250 mots, indépendante des sources si cours absent), (4) lancer des **exercices** ciblés (3–5, régénérés à chaque tentative) et (5) voir la leçon passer à `completed` automatiquement à ≥60% ou manuellement via bouton. Ajout d'une arborescence `vendor/llama.cpp/<version>/` et `models/gguf/` pour le runtime local b10632.

Approche : nouvelle entité `LessonDiscussion` isolée par `learner_id`, extensions `PathStep.status`, tables SQLite, services `tutor/lesson_discussion.py`, endpoints fins dans `web/server.py`, UI vue dédiée dans `tutor.html`. Aucune modification des 3 générateurs de parcours existants.

## Technical Context

**Language/Version**: Python ≥ 3.11 (venv `./venv`, Python 3.12)

**Primary Dependencies**: `httpx`, `numpy`, `pypdf`, `Pillow`, `python-multipart` (runtime) ; `fastapi`/`uvicorn[standard]` (web extra) ; `pytest`/`pytest-asyncio`/`jsonschema` (dev). Runtime local : `llama-server` b10632 (`vendor/llama.cpp/b10632/llama-server`) via `providers/llama_server.py`, modèles GGUF dans `models/gguf/` (`tutor.llama_models_dir`).

**Storage**: SQLite via `LibraryStore` (`tutor/store.py`), migrations idempotentes `PRAGMA table_info`. Nouvelles tables : `lesson_discussions` (`id`, `path_step_id`, `notion_id`, `subject_id`, `learner_id`, `status`, `created_at`), `lesson_messages` (`id`, `discussion_id`, `role`, `content`, `sources`, `created_at`), `generated_lesson_contents` (`id`, `discussion_id`, `kind` [`lesson_course`|`lesson_summary`], `content`, `sources`, `confidence`, `created_at`), `lesson_exercise_attempts` (`id`, `discussion_id`, `questions`, `answers`, `score`, `feedback`, `passed`, `created_at`). Extension `path_steps.status` (`not_started`|`in_progress`|`completed`).

**Testing**: `pytest` hors-ligne via `httpx.MockTransport`, `@pytest.mark.asyncio` explicite. Suite `venv/bin/pytest tests/ -q`. `node --check` sur script inline `tutor.html`. Pas de démon réel.

**Target Platform**: Linux CPU-only 8–16 Go RAM, navigateur (port 9215) + TUI.

**Project Type**: Application web + TUI (src-layout `src/ollama_tutor`).

**Performance Goals**: Clic leçon → discussion < 500 ms (SC-003) ; parcours complet (cours+synthèse+exercices) < 5 min hors lecture (SC-001) ; génération cours < 30 s sur CPU b10632.

**Constraints**: Cœur dans `tutor/` sans import `fastapi`/`textual` (principe I) ; zéro nouvelle dépendance d'exécution sans justification (V) ; tout local 127.0.0.1 (IV) ; tests hors-ligne (III) ; `errors.log` (VI) ; binaires/models git-ignorés (`vendor/`, `models/`).

**Scale/Scope**: 4 user stories (3×P1, 1×P2), 15 FR, 5 SC, 4 entités. Multi-learner (filtrage `learner_id`).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principe | Conformité | Justification |
|----------|-----------|---------------|
| I. Cœur découplé | ✅ | Logique `LessonDiscussion` dans `tutor/lesson_discussion.py` ; `web/server.py` = transport fin. Vérifié par `test_tutor_imports.py`. |
| II. Préservation fonctionnelle | ✅ | Réutilise `PathStep`, `GraphBuilder`, `TutorService`, `ProgressTracker` ; étend `path_steps.status` sans casser génération existante. |
| III. Tests hors-ligne | ✅ | LLM/RAG mockés via `httpx.MockTransport` ; exercices et génération mockés. |
| IV. Sécurité locale | ✅ | Tout local, discussions isolées par `learner_id`, pas de partage réseau. |
| V. Légèreté | ✅ | Aucune nouvelle dépendance ; `vendor/` et `models/` hors dépôt ; réutilise `TutorService.ask`. |
| VI. Observabilité | ✅ | Erreurs LLM/RAG loggées dans `data/errors.log` via `_log_error`, UI affiche message clair. |

**Gates**: Aucune violation. Complexity Tracking vide.

## Project Structure

### Documentation (this feature)

```text
specs/009-lecon-discussion-centree/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/ollama_tutor/
├── tutor/
│   ├── lesson_discussion.py        # NOUVEAU : LessonDiscussionService (cours/synthèse/exercices, RAG filtré)
│   ├── store.py                    # ÉTENDU : tables lesson_* + méthodes, extension path_steps.status
│   ├── models.py                   # ÉTENDU : LessonDiscussion, GeneratedLessonContent, LessonExerciseAttempt
│   ├── service.py                  # ÉTENDU : orchestration discussion (scope notion)
│   └── providers/llama_server.py   # RÉUTILISÉ : binaire vendor/llama.cpp/b10632/llama-server
├── web/
│   ├── server.py                   # ÉTENDU : endpoints /lecons, /lesson-discussions, /generate, /exercises
│   └── static/tutor.html           # ÉTENDU : vue dédiée /lecon/:id, leçons cliquables, badges statut
vendor/
└── llama.cpp/b10632/llama-server   # VENDU : binaire b10632 (non commité, .gitkeep)
models/
└── gguf/*.gguf                     # VENDU : GGUFs (non commité, .gitkeep)
tests/
├── unit/test_lesson_discussion.py  # NOUVEAU
├── contract/test_lesson_api.py     # NOUVEAU
└── integration/test_lesson_flow.py # NOUVEAU
```

**Structure Decision**: Structure src-layout conservée (Option 1). Un seul nouveau module métier `lesson_discussion.py` + extensions `store.py`/`models.py`; `vendor/` et `models/` hors dépôt.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

Aucune violation — tableau vide.
