# Implementation Plan: Plateforme multi-vues & bibliothèque de connaissances

**Branch**: `005-platform-ui-library` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-platform-ui-library/spec.md`

## Summary

Faire évoluer EduNexus d'une page unique dense vers une plateforme multi-espaces
(Accueil, Conversations, Bibliothèque, Apprentissage, Entraîner, Quiz/Examens,
Progression, Explorer) avec conversations nommées persistantes, une bibliothèque
hiérarchique Domaine → Catégorie → Documents, la sélection de sources par groupe
ou individuellement, et des **sources actives propres à chaque conversation**
alimentant le RAG existant. Aucune fonctionnalité n'est supprimée : tout est
relocalisé. Approche technique : routage par hash dans le fichier UI unique,
extension du système de sessions existant en conversations nommées, filtrage du
RAG par identifiants de livres transmis au frame `ask`, composition du tableau
de bord à partir des endpoints existants.

## Technical Context

**Language/Version**: Python 3.11+ (backend FastAPI), JavaScript vanilla ES2022 (frontend)

**Primary Dependencies**: FastAPI + uvicorn, httpx, numpy, pypdf, python-multipart ; frontend autonome (CSS/JS inline, zéro framework)

**Storage**: SQLite (`~/.config/ollama-tui/library.db`, PRAGMA foreign_keys ON) + vecteurs NumPy float32 ; config JSON

**Testing**: pytest hors-ligne (httpx.MockTransport, marqueurs asyncio explicites) ; `node --check` pour le JS inline

**Target Platform**: application web locale (loopback 127.0.0.1:9215), navigateurs modernes, mobile = empilement vertical

**Project Type**: web-service + frontend mono-fichier (structure existante conservée)

**Performance Goals**: parcours hiérarchique fluide à 1 000+ documents (chargement progressif par niveau) ; recherche < 1 s ; stats RAG affichées à chaque réponse

**Constraints**: zéro nouvelle dépendance d'exécution ; un seul `llama-server` à la fois ; suite 209+ tests verte en permanence ; loopback uniquement ; garde same-origin inchangée

**Scale/Scope**: 8 espaces de navigation, ~10 nouveaux endpoints REST, 1 extension de frame WS, 2 nouvelles tables SQLite, 1 fichier UI (existant, restructuré)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principe | Statut | Notes |
|---|---|---|
| I. Cœur métier découplé | ✅ PASS | Toute nouvelle logique (conversations, périmètre RAG, agrégats dashboard) vit dans `tutor/` ; `server.py` reste transport fin ; `tutor.html` ne contient que du rendu |
| II. Préservation fonctionnelle | ✅ PASS | Matrice de relocalisation exhaustive (research.md §5) : chaque fonctionnalité actuelle garde exactement un emplacement ; approche incrémentale par espaces |
| III. Tests hors-ligne d'abord | ✅ PASS | Contrats testés via TestClient/MockTransport ; régression JS via `node --check` ; aucun démon requis |
| IV. Sécurité locale par défaut | ✅ PASS | Nouvelles routes couvertes par le middleware same-origin existant ; loopback inchangé ; pas de secret nouveau |
| V. Légèreté & simplicité | ✅ PASS | Zéro dépendance nouvelle ; UI toujours mono-fichier ; routage par hash natif plutôt qu'un routeur |
| VI. Observabilité | ✅ PASS | Nouveaux flux (suppression catégorie avec sources actives, échec de conversation) journalisés via `_log_error` |

**Verdict initial** : AUCUNE violation. Re-vérification après Phase 1 : ✅ (voir
section finale de ce fichier — la décision « conversations = sessions étendues »
évite une table parallèle, conforme au principe V).

## Project Structure

### Documentation (this feature)

```text
specs/005-platform-ui-library/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   └── api.md           #   deltas REST + frames WS
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created here)
```

### Source Code (repository root)

```text
src/ollama_tutor/
├── tutor/
│   ├── conversations.py     # NOUVEAU : service conversations (liste, création,
│   │                        # renommage, suppression, sources actives)
│   ├── store.py             # ÉTENDU : tables conversations + conversation_sources ;
│   │                        # colonnes sessions.title / sessions.updated_at
│   ├── retrieval.py         # ÉTENDU : filtre optionnel par book_ids
│   └── service.py           # ÉTENDU : ask() accepte un périmètre de livres
├── web/
│   ├── server.py            # ÉTENDU : ~10 routes conversations/dashboard ;
│   │                        # frame ask : champ optionnel book_ids
│   └── static/tutor.html    # RESTRUCTURÉ : coquille multi-vues (hash routing),
│                            # bibliothèque arborescente, sélecteur de sources,
│                            # tableau de bord — mêmes primitives CSS/JS
tests/
├── contract/                # + contrats conversations/sources/dashboard
├── integration/             # + parcours conversation ↔ RAG scopé
└── unit/                    # + store conversations, filtre retrieval
```

**Structure Decision**: structure existante conservée à l'identique (principes
II/V) — un seul nouveau module (`tutor/conversations.py`) pour isoler la logique
conversations, tout le reste est une extension des fichiers courants. Pas de
découpage frontend/backend supplémentaire : l'UI reste le fichier autonome
existant, réorganisé en vues.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| —         | —          | —                                   |

## Post-Design Constitution Re-check (Phase 1)

| Principe | Statut post-design |
|---|---|
| I. Découplage | ✅ `tutor/conversations.py` sans import UI ; périmètre RAG = paramètre de service |
| II. Préservation | ✅ matrice de relocalisation complète (research.md §5) ; déduplication auto-classify conservée |
| III. Tests | ✅ chaque FR a son contrat testable ; quickstart = validation manuelle E2E |
| IV. Sécurité | ✅ mêmes gardes ; aucune écoute réseau nouvelle |
| V. Légèreté | ✅ hash routing natif ; tables minimales ; pas de dépendance |
| VI. Observabilité | ✅ suppressions avec sources actives + échecs conversation journalisés |
