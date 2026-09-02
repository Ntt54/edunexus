# Implementation Plan: EduNexus adaptatif — profil pédagogique, parcours explicable, capture de programme & carnet de matière

**Branch**: `008-subject-pedagogical-profiles` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-subject-pedagogical-profiles/spec.md`

## Summary

EduNexus devient un tuteur adaptatif complet en intégrant les recommandations du document « Analyse des méthodes d'apprentissage et conception d'un EduNexus adaptatif ». La feature ajoute : (1) un **profil pédagogique de matière** structuré avec modèles prédéfinis, (2) un **graphe de compétences** construit à partir des livres importés, (3) un **parcours d'apprentissage explicable** et éditable, (4) une **adaptation après séance** par fenêtre locale, (5) un **tableau de bord** de traçabilité, (6) la **capture de programme par OCR** (photo/PDF), (7) l'**import de photo dans les conversations**, (8) un **carnet de matière** inspiré de NotebookLM, et (9) le **multi-utilisateur familial** sur un seul PC.

L'approche technique s'appuie sur l'infrastructure existante (spec 004 + spec 006 partielle) : `LibraryStore` (SQLite), `ProgressTracker`, `TutorService`, providers OCR/LLM locaux. Le graphe et le parcours sont construits par **règles déterministes** (le LLM ne propose que des candidats soumis à validation), conformément au principe anti-hallucination du document.

## Technical Context

**Language/Version**: Python ≥ 3.11 (venv `./venv`, Python 3.12)

**Primary Dependencies**: `httpx`, `numpy`, `pypdf`, `Pillow`, `python-multipart` (runtime) ; `fastapi`/`uvicorn[standard]` (web extra) ; `pytest`/`pytest-asyncio`/`jsonschema` (dev). OCR local via provider Granite-Docling (`tutor/providers/docling_ocr.py`) + rasterisation `pdftoppm` ; moteur GGUF via `llama-server` (`providers/llama_server.py`).

**Storage**: SQLite via `LibraryStore` (`tutor/store.py`), répertoire de données projet `data/` (config_dir). Vecteurs NumPy float32. Migrations idempotentes style PRAGMA-table_info. Nouvelles tables : `subject_profiles`, `competency_graph` (nodes/edges), `captured_programs`, `program_nodes`, `conversation_photos`, `notebooks`, `notebook_outputs`, `learner_profiles`.

**Testing**: `pytest` (hors-ligne via `httpx.MockTransport`, marqueur explicite `@pytest.mark.asyncio`). Suite complète `venv/bin/pytest tests/ -q` (~391 tests). `node --check` sur le script inline de `tutor.html` après toute modif UI. `./benchmark.sh` après modif de `client.py`/chemins de rendu.

**Target Platform**: Linux, CPU-only, 8–16 Go RAM, navigateur (web GUI `ollama-webgui`, port 9215) + TUI Textual (`ollama-tui`).

**Project Type**: Application web + TUI (src-layout package `src/ollama_tutor`, console scripts `edunexus`).

**Performance Goals**: Génération de parcours < 5 s (SC-003) ; capture OCR photo/PDF < 30 s (SC-009) ; création de profil < 3 min (SC-001) ; fonctionnement complet sur CPU 8 Go RAM sans épuiser la mémoire (SC-008).

**Constraints**: Cœur métier dans `tutor/` sans import fastapi/textual (principe I) ; aucune nouvelle dépendance d'exécution sans justification écrite (principe V) ; tout local, zéro cloud (principe IV) ; tests hors-ligne (principe III) ; erreurs journalisées dans `data/errors.log` (principe VI).

**Scale/Scope**: 9 user stories, 39 FR, 13 SC, 15 entités. Multi-utilisateur familial (plusieurs profils locaux sur un seul PC, sans comptes ni concurrence).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principe | Conformité | Justification |
|----------|-----------|---------------|
| I. Cœur découplé de l'interface | ✅ | Toute logique (graphe, parcours, OCR, carnet, profils) vit dans `tutor/` ; `web/server.py` reste une couche de transport fine. Vérifié par `tests/contract/test_tutor_imports.py`. |
| II. Préservation fonctionnelle | ✅ | Réutilise l'infrastructure spec 004/006 (subjects, concepts, progress, paths) ; étend `LearningPath`/`PathStep` sans casser l'existant. |
| III. Tests hors-ligne d'abord | ✅ | Tous les flux OCR/LLM simulés via `httpx.MockTransport` ; marqueur `@pytest.mark.asyncio` explicite. |
| IV. Sécurité locale par défaut | ✅ | OCR, graphe, parcours, carnet, profils : tout local, aucune donnée hors du PC ; serveur lié à 127.0.0.1. |
| V. Légèreté & simplicité | ✅ | Règles déterministes pour le graphe/parcours (pas de dépendance LLM) ; OCR réutilise le provider Granite-Docling existant ; aucune nouvelle dépendance d'exécution. |
| VI. Observabilité des erreurs | ✅ | Toute exception journalisée avec traceback dans `data/errors.log` via `_log_error` ; statuts visibles (OCR, indexation, parcours). |

**Gates**: Aucune violation. Aucune justification de complexité requise (tableau Complexity Tracking vide).

## Project Structure

### Documentation (this feature)

```text
specs/008-subject-pedagogical-profiles/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/ollama_tutor/
├── tutor/                          # Cœur métier (principe I — aucun import fastapi/textual)
│   ├── profiles.py                 # NOUVEAU : SubjectProfile, PedagogicalTemplate, conversion objectif→paramètres
│   ├── graph.py                    # NOUVEAU : construction du graphe de compétences (règles déterministes + candidats LLM)
│   ├── path_builder.py             # NOUVEAU : génération du parcours explicable (sélection + tri topologique)
│   ├── adaptation.py               # NOUVEAU : révision par fenêtre locale après séance + preuves multiples
│   ├── program_capture.py          # NOUVEAU : pipeline OCR photo/PDF (prétraitement, structuration, rapprochement, validation)
│   ├── conversation_photo.py       # NOUVEAU : import de photo dans une conversation
│   ├── notebook.py                 # NOUVEAU : carnet de matière (actions, sorties liées aux sources)
│   ├── learners.py                 # NOUVEAU : profils d'apprenant multi-utilisateur familial
│   ├── store.py                    # ÉTENDU : nouvelles tables + méthodes (profiles, graph, programs, notebooks, learners)
│   ├── models.py                   # ÉTENDU : SubjectProfile, CompetencyNode, GraphEdge, CapturedProgram, etc.
│   ├── service.py                  # ÉTENDU : orchestration des nouveaux services
│   └── progress.py                 # RÉUTILISÉ : ProgressTracker (maîtrise continue par nœud)
├── web/
│   ├── server.py                   # ÉTENDU : endpoints REST/WS pour profils, graphe, parcours, OCR, carnet, profils
│   └── static/tutor.html           # ÉTENDU : UI profil, graphe, parcours, tableau de bord, capture, carnet, sélecteur d'utilisateur
└── utils/platform.py               # RÉUTILISÉ : chemins de données

tests/
├── unit/
│   ├── test_profiles.py            # NOUVEAU
│   ├── test_graph.py               # NOUVEAU
│   ├── test_path_builder.py        # NOUVEAU
│   ├── test_adaptation.py          # NOUVEAU
│   ├── test_program_capture.py     # NOUVEAU
│   ├── test_conversation_photo.py  # NOUVEAU
│   ├── test_notebook.py            # NOUVEAU
│   └── test_learners.py            # NOUVEAU
├── contract/
│   ├── test_tutor_profiles_api.py  # NOUVEAU
│   ├── test_tutor_graph_api.py     # NOUVEAU
│   ├── test_tutor_capture_api.py   # NOUVEAU
│   └── test_tutor_learners_api.py  # NOUVEAU
└── integration/
    └── test_adaptive_flow.py       # NOUVEAU : flux de bout en bout
```

**Structure Decision**: Structure src-layout existante conservée (Option 1). Toute la logique métier nouvelle est ajoutée dans `src/ollama_tutor/tutor/` sous forme de modules dédiés (un par domaine de la spec), le `LibraryStore` est étendu pour la persistance, `web/server.py` expose des endpoints fins, et `tutor.html` porte l'UI. Aucun nouveau package racine.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

Aucune violation — tableau vide.
