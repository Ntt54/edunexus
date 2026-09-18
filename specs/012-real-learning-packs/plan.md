# Implementation Plan: 012 Real Learning Packs

**Branch**: `012-real-learning-packs` | **Date**: 2026-09-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-real-learning-packs/spec.md` (clarified 2026-09-18, 5 Q&A: single-shot delivery, parents-only, Cameroon BEPC/probatoire/bac, WCAG 2.2 AA, hybrid curriculum skeleton+import)

## Summary

Faire d'EduNexus un vrai instrument d'apprentissage du secondaire au supérieur en un seul chantier : (1) rappels de révisions + quiz à rappel rédigé + entremêlement adossés au FSRS existant ; (2) programmes camerounais embarqués + épreuves blanches BEPC/probatoire/bac + vue parent ; (3) citations traçables + notes atomiques liées + planning semestre/ECTS ; (4) gamification anti-grinding + mode lisibilité WCAG 2.2 AA. Approche : extension compatible des moteurs existants (review, assessment, paths, notebook, XP), nouvelles tables minimales (consentement parent, notes atomiques, packs curriculum), données JSON versionnées hors DB utilisateur, zéro nouvelle dépendance d'exécution.

## Technical Context

**Language/Version**: Python 3.12 (venv, requires ≥3.11) + TypeScript 5 / Vue 3 (web/vue, Vite)

**Primary Dependencies**: `fastapi`/`uvicorn[standard]` (web, thin transport), `httpx`, `numpy`, `pypdf`, `Pillow`, `python-multipart`; FE: Vue 3, Vue Router, Vite, vue-tsc (no test runner — contract/integration via pytest)

**Storage**: SQLite WAL via `LibraryStore` (`~/.config/ollama-tui/library.db` ou `EDUNEXUS_DATA_DIR`) + JSON packs versionnés sous `data/curriculum/cm/` (lecture seule, checksum en DB) ; embeddings/FTS existants réutilisés pour citations

**Testing**: `python3 -m pytest tests/ -q` (MockTransport offline, `@pytest.mark.asyncio` explicite), `tests/contract/test_tutor_imports.py` (gate I), `npm --prefix web/vue run build` (vue-tsc), `node --check` sur tutor.html

**Target Platform**: Linux desktop, `127.0.0.1:9215/tutor` offline-first, CPU-only, single `llama-server` exclusif, ≤8 GB RAM

**Project Type**: Web-service + SPA (backend `web/server.py` thin → `tutor/service.py/store.py`, frontend `web/vue` + `web/static/tutor.html` legacy)

**Performance Goals**: Rappels visibles <10 s à l'ouverture (SC-001) ; épreuve blanche générée <2 min de préparation (SC-003) ; planning semestre <1 min (SC-006) ; switch lisibilité sans rechargement

**Constraints**: Constitution I découplé (tutor/ sans fastapi/textual), II préservation (réutiliser review/assessment/paths/notebook/XP), III tests hors-ligne, IV 127.0.0.1 + Origin/Host, V YAGNI (aucune nouvelle dép runtime — validation JSON en stdlib), VI observabilité `errors.log` + `POST /api/log-error`

**Scale/Scope**: ~10 matières, ~50 apprenants, packs BEPC/1ère/Terminale A/C/D, ~15 étapes/parcours, <5k chunks, <10k req/jour local

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I Cœur découplé**: PASS — rappels/quiz/entremêlement/citations/notes/planning/XP dans `tutor/` ; `web/server.py` thin (parse → delegate) ; lisibilité en CSS variables + prefs Vue. Vérifié par `test_tutor_imports.py`.
- **II Préservation**: PASS — extension de `due_reviews`, `QuizEngine`, `create_exam`, `NotebookService`, `add_xp` existants ; pas de drop d'endpoint ; `subject_learners` (011) réutilisé pour la vue parent.
- **III Tests hors-ligne**: PASS — TestClient + stores tmp, LLM mockés, `@pytest.mark.asyncio` ; WCAG vérifié par checklist + bookmarklet d'espacement, pas de démon live.
- **IV Sécurité locale**: PASS — 127.0.0.1, validation Origin/Host, consentement parent explicite/révocable (données mineur), aucune donnée d'examen réel sous droit copiée (squelettes titres/objectifs uniquement).
- **V Légèreté**: PASS — aucune nouvelle dép runtime (validation packs en stdlib `json`, CSS pur, minuteur `setTimeout`) ; minuteur de rappel = poll au focus, pas de daemon.
- **VI Observabilité**: PASS — erreurs 400/404/422 via `_log_error` + `errors.log`, toast UI, `POST /api/log-error` ; épreuves « interrompues » journalisées.

Re-check post-Phase 1: PASS — `data-model.md` ajoute 4 tables minimales + 1 colonne, `contracts/api.md` reste additif, `quickstart.md` couvre les 4 stories.

## Project Structure

### Documentation (this feature)

```text
specs/012-real-learning-packs/
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
│   ├── review.py         # due_reviews enrichi (compteurs, retard)
│   ├── assessment.py     # recall_written kind, entremêlement, barème /20
│   ├── adaptation.py     # échantillonneur entremêlé (remplace round-robin)
│   ├── exams.py          # (nouveau, extrait de service) blueprints BEPC/probatoire/bac
│   ├── packs.py          # (nouveau) chargement/validation packs JSON curriculum
│   ├── notes.py          # (nouveau, extrait de notebook) notes atomiques liées
│   ├── planner.py        # (nouveau) planning semestre/ECTS
│   ├── rewards.py        # (nouveau, extrait XP) XP pondéré + caps + série hebdo
│   ├── sharing.py        # (nouveau) consentement parent + vue agrégée
│   ├── store.py          # 4 tables + consentement LearnerProfile
│   ├── service.py        # TutorService: délégation aux nouveaux services
│   └── data/packs/       # curriculum JSON versionnés (BEPC, 1ère, Terminale A/C/D)
├── web/
│   ├── server.py         # routes thin: rappels, épreuves, partage, notes, planning
│   └── static/tutor.html # legacy (correctifs minimaux si concerné)
web/vue/
├── src/views/RemindersView.vue   # (nouveau) À réviser
├── src/views/ExamView.vue        # épreuves blanches (chrono, verrouillage, /20)
├── src/views/ParentView.vue      # (nouveau) suivi parent
├── src/views/PlannerView.vue     # (nouveau) planning semestre
├── src/stores/preferences.ts     # readability:{fontScale,contrast,dyslexia}
├── src/services/api.ts           # helpers packs/examens/partage/notes/planning
└── src/index.css                 # variables mode lisibilité (presets)
tests/
├── contract/test_012_*.py
├── integration/test_012_*.py
└── unit/test_012_*.py
```

**Structure Decision**: Single project + Vue SPA existante (cf. 010/011). Nouveaux services `tutor/` extraits par domaine (exams, packs, notes, planner, rewards, sharing) pour ne pas gonfler `service.py` ; données curriculum hors DB utilisateur sous `data/` via `EDUNEXUS_DATA_DIR`.

## Complexity Tracking

> No constitution violations to justify.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
