# Implementation Plan: Greffe des modules `autreprojet` (010)

**Branch**: `010-greffe-autreprojet` | **Date**: 2026-09-07 | **Spec**: [spec.md](./spec.md) | **Statut**: IMPLÉMENTÉ (T001–T055 sauf T005 gate daemon-live, voir tasks.md)

**Input**: Feature specification from `/specs/010-greffe-autreprojet/spec.md`

## Summary

Garder la base EduNexus (`TutorService`, SQLite WAL, offline-first) et y greffer, lot par
lot réversible, les modules mieux implémentés dans `autreprojet/` :
**P0-A sandbox Python** (`runner.py` + `safety.py` → preuve d'exécution pour `grade_answer`),
**P0-B registre providers** (`ProviderRegistry` + modes d'embedding),
**P1-A ingestion à jobs**, **P1-B contract test web**.
Zéro nouvelle dépendance d'exécution ; recherche préalable effectuée en lecture directe
(sous-agents en erreur réseau, non relancés).

## Technical Context

**Language/Version**: Python 3.12 via `./venv` (projet exige ≥ 3.11)

**Primary Dependencies**: `httpx`, `numpy`, `pypdf`, `Pillow`, `python-multipart` ;
extras `web` (fastapi/uvicorn), `dev` (pytest/pytest-asyncio/jsonschema).
Aucune nouvelle dépendance — les 4 lots n'utilisent que stdlib + existant.

**Storage**: SQLite WAL (`LibraryStore`, `~/.config/ollama-tui/`) + nouvelle table
`ingestion_jobs` (lot P1-A, migration idempotente style `PRAGMA table_info`) ;
vecteurs NumPy float32, dimensions auto-détectées.

**Testing**: `venv/bin/pytest tests/ -q` (~377 tests, < 20 s, offline) ;
`httpx.MockTransport` via `tests/conftest.py`, `@pytest.mark.asyncio` explicite
(`pythonpath = ["."]`, imports tests en `from src.ollama_tutor...`) ;
`node --check` sur `tutor.html` ; `./benchmark.sh` si `client.py` touché.

**Target Platform**: Linux local, machines modestes (≤ 8 Go RAM), serveur lié à
`127.0.0.1:9215` uniquement.

**Project Type**: application locale (moteur `tutor/` + transport `web/` + TUI existante).

**Performance Goals**: parité de débit avec `ollama run` (gate après touche
`client.py`/render) ; import/index en arrière-plan avec statut par livre ;
sandbox : timeout mural défaut 5 s (plafond 30 s), sortie tronquée à 32 Ko.

**Constraints**: offline-capable (aucun appel réseau en tests) ; **CPU-only**
(pas de GPU, pas de `torch`/CUDA — objectif machine actuelle) ; un seul
`llama-server` actif ; `tutor/` sans import UI ; mémoire adressable plafonnée
côté sandbox (`RLIMIT_AS` 256 Mo défaut).

**Scale/Scope**: 4 lots, ~8 fichiers sources nouveaux/adaptés, ~4 fichiers de
contrats, suite de tests étendue par lot ; pas de refonte UI.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Cœur découplé** : PASS — `tutor/sandbox.py`, `tutor/safety.py`,
  `tutor/providers/registry.py` vivent dans `tutor/`, sans `fastapi`/`textual` ;
  `web/server.py` ne fait que déléguer (nouvelles routes fines).
- **II. Préservation** : PASS — greffes additives + branchements fins
  (`grade_answer` garde son contrat, `client.py` garde le sien) ; chaque lot
  réversible ; FSRS complet différé.
- **III. Tests hors-ligne** : PASS — `MockTransport`/`MockLLMClient`/`skip_safety`,
  tests de régression par correctif, suite verte exigée par lot.
- **IV. Sécurité locale** : PASS — bind `127.0.0.1`, sandbox (env vide, cwd
  `0o700`, `-I`, rlimits, scan AST), allowlist docs, aucun secret commité.
- **V. Légèreté** : PASS — stdlib only, 0 nouvelle dép d'exécution, pas de
  Chroma/Milvus/LangChain/Next.js ; justification écrite ici même.
- **VI. Observabilité** : PASS — exceptions via `_log_error` → `errors.log`,
  jobs avec `error_message`, `phase_label` affichables.

*Re-check post-Phase 1 : aucun artefact de design n'introduit de violation
(nouveaux modules sous `tutor/`, contrats sans dépendance, quickstart offline).*

## Project Structure

### Documentation (this feature)

```text
specs/010-greffe-autreprojet/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── sandbox.md
│   ├── providers.md
│   ├── ingestion-jobs.md
│   └── web-contract-test.md
├── deepdive.md            # Deep-dive synthesis (4 projets + backlog P2)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/ollama_tutor/
├── tutor/
│   ├── sandbox.py          # NEW (P0-A, adapté de python-tutor runner.py)
│   ├── safety.py           # NEW (P0-A, adapté de python-tutor safety.py)
│   ├── assessment.py       # EDIT (P0-A, brancher preuve d'exécution FR-020)
│   ├── service.py          # EDIT (P0-B registry, P1-A jobs)
│   ├── store.py            # EDIT (P1-A table ingestion_jobs)
│   └── providers/
│       └── registry.py     # NEW (P0-B, adapté d'OpenTutor router+embedding registry)
├── web/
│   ├── server.py           # EDIT (P1-A statuts jobs, garde-fou fin)
│   └── static/tutor.html   # EDIT éventuel (affichage statut jobs)
└── client.py               # EDIT éventuel (P0-B, sous gate benchmark.sh)

tests/
├── contract/
│   └── test_web_contract.py  # NEW (P1-B, adapté d'open-tutor-ai)
├── unit/
│   ├── test_sandbox.py       # NEW (P0-A)
│   ├── test_safety.py        # NEW (P0-A)
│   └── test_provider_registry.py  # NEW (P0-B)
└── integration/
    └── test_ingestion_jobs.py     # NEW (P1-A)
```

**Structure Decision**: structure existante conservée (moteur `tutor/` + transport
`web/`, layout `src/`) ; ajouts sous `tutor/` et `tests/` uniquement, conformément
au principe I. Pas de frontend séparé, pas de nouveau top-level package.

## Complexity Tracking

> Aucune violation de la Constitution à justifier — section conservée vide par design.
