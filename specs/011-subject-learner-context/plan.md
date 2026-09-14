# Implementation Plan: 011 Subject & Learner Context Isolation

**Branch**: `011-subject-learner-context` | **Date**: 2026-09-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-subject-learner-context/spec.md` (clarified 2026-09-14, 4 Q&A)

## Summary

Corriger le bug multi-matières : le sélecteur header "Non classé / java" ne filtre aucune vue (Accueil montre toujours le parcours Python, Mon parcours liste "Non classé" même en "java", badge #/apprenants figé à "Non classé"). Rendre "Non classé" renommable/supprimable comme les autres matières (avec confirmation), isoler la progression par couple (matière × apprenant), et filtrer la bibliothèque par matière (filtrée par défaut + toggle "Toutes"). Approche: extension compatible des query params `subject_id`/`learner_id` + liste `learners?subject_id=` dérivée, invalidation cache côté Vue via `preferences.ts` (localStorage), pas de nouvelle migration lourde, réutilisation indexes existants.

## Technical Context

**Language/Version**: Python 3.12 (venv, requires ≥3.11) + TypeScript 5 / Vue 3 (web/vue/client, Vite)

**Primary Dependencies**: `fastapi`/`uvicorn[standard]` (web, thin transport), `httpx`, `numpy`, `pypdf`, `Pillow`, `python-multipart`; FE: Vue 3, Vite, vue-tsc

**Storage**: SQLite WAL via `LibraryStore` (`~/.config/ollama-tui/library.db` ou `EDUNEXUS_DATA_DIR=/.../data` via `edunexus-local.sh`) + embeddings/FTS; tables `subjects(id, name, learner_id)`, `learner_profiles`, `learning_paths(subject_id, learner_id)`, `lesson_discussions`, `books/subject_books`

**Testing**: `venv/bin/pytest tests/ -q` (MockTransport offline, `@pytest.mark.asyncio`), `tests/contract/test_core_imports.py` (gate I), `npm --prefix web/vue/client run build` (vue-tsc), `node --check` sur tutor.html

**Target Platform**: Linux desktop, `127.0.0.1:9215/tutor` offline-first, CPU-only, single `llama-server` (llama.cpp) exclusif, ≤8 GB RAM

**Project Type**: Web-service + SPA (backend `web/server.py` thin → `tutor/service.py/store.py`, frontend `web/vue/client` + `web/static/tutor.html` legacy)

**Performance Goals**: Switch matière <2s sans flash ancien contenu (SC-001), création apprenant <30s (SC-004), cohérence header/badge 95%+ (SC-003), 60fps inval cache

**Constraints**: Constitution I découplé (tutor/ n'importe ni fastapi/textual), II préservation (réutiliser endpoints), III offline tests, IV 127.0.0.1 + Origin/Host, V YAGNI (pas de nouvelle dép runtime), VI observabilité `errors.log` + `POST /api/log-error`, auto-detect embedding dims

**Scale/Scope**: ~10 matières, ~50 apprenants, ~15 étapes/parcours, 8 documents initiaux, <5k chunks, <10k req/jour local

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I Cœur découplé**: PASS — tout filtrage métier dans `tutor/service.py`+`store.py` (queries `WHERE subject_id=? AND learner_id=?`); `web/server.py` reste transport thin (parse query → delegate). Vérifié par `test_tutor_imports.py`.
- **II Préservation**: PASS — extension compatible de `GET/POST` existants avec query params optionnels; pas de drop d'endpoint, réutilise `book_corpora`/`chunks`.
- **III Tests hors-ligne**: PASS — nouveaux tests `httpx.MockTransport` pour `?subject_id=&learner_id=` + tests Vue getters `filteredPaths`; pas de démon live.
- **IV Sécurité locale**: PASS — `127.0.0.1:9215`, validation `Origin/Host`, pas de secret commit, data hors dépôt.
- **V Légèreté**: PASS — aucune nouvelle dép runtime; toggle `showAllSources` en `localStorage` stdlib.
- **VI Observabilité**: PASS — erreurs 400/403/404 loguées `_log_error` + `errors.log`, UI toast clair, `POST /api/log-error` relay.

Re-check post-Phase 1: PASS — `data-model.md` n'ajoute aucune table, `contracts/api.md` reste additive, `quickstart.md` couvre 10 switches test.

## Project Structure

### Documentation (this feature)

```text
specs/011-subject-learner-context/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── api.md           # Phase 1 output
└── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/ollama_tutor/
├── tutor/
│   ├── store.py          # subjects, learner_profiles, learning_paths filtering
│   ├── service.py        # TutorService.generate_course / stats scoping
│   ├── learners.py       # LearnerService.filtered()
│   └── config.py
├── web/
│   ├── server.py         # GET /api/tutor/* ?subject_id=&learner_id=, PATCH/DELETE subjects
│   └── static/tutor.html # legacy single-file UI
web/vue/client/
├── src/stores/preferences.ts  # activeSubjectId, activeLearnerId, showAllSources
├── src/views/DashboardView.vue  # uses filtered stats/nextStep
├── src/views/PathView.vue       # uses filtered learning-paths
├── src/views/LearnersView.vue   # filtered learners + badge cohérent
├── src/views/LibraryView.vue    # books?subject_id + toggle Toutes
└── src/services/api.ts          # new query param helpers
tests/
├── contract/test_web_contract.py  # allowlist + ?subject_id
├── integration/test_subject_filter.py
└── integration/test_learner_scope.py
```

**Structure Decision**: Single project + Vue SPA existante (cf. 010). Pas de backend/frontend séparés physiquement; le "frontend" est `web/vue/client` buildé vers `web/static/dist`.

## Complexity Tracking

> No constitution violations to justify.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
