# Implementation Plan: 013 Vague 1 Socle Pédagogique

**Branch**: `013-vague1-socle` | **Date**: 2026-09-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-vague1-socle/spec.md` (clarified 2026-09-18 : seuils fixes overdue/urgent≤3j/warning≤7j, leçons en JSON strict)

## Summary

Socle pédagogique de la shortlist autreprojet (Vague 1) : I1 curriculum-as-data (leçons JSON + pièges classiques unifiés au diagnostic 5-cats), I2 prévision d'oubli par notion (read-model sur la formule FSRS existante, tri overdue-first), I6 prompts Markdown éditables + gabarit YAML avec FALLBACK, I7 pedagogy goldens déterministes (après/avec I6). Approche : nouveaux modules minces `tutor/` (curriculum, forecast-read, prompt packs), zéro nouveau moteur, zéro nouvelle dépendance, tests offline (dont `tests/pedagogy/`).

## Technical Context

**Language/Version**: Python 3.12 (venv, requires ≥3.11) + TypeScript 5 / Vue 3 (retouches UI mineures si affichage urgences)

**Primary Dependencies**: `fastapi`/`uvicorn[standard]` (thin transport), `httpx`, `numpy`, `pypdf`, `Pillow`, `python-multipart` ; FE inchangé sauf libellés

**Storage**: SQLite WAL via `LibraryStore` + fichiers JSON versionnés (`src/ollama_tutor/tutor/data/lessons/*.json`, lecture seule, checksum) + prompts Markdown (`assets/prompts/*.md`, FALLBACK intégré) + goldens (`tests/pedagogy/goldens/*.json`). Les leçons sont lues depuis le package (pas `EDUNEXUS_DATA_DIR`, réservé aux données utilisateur).

**Testing**: `python3 -m pytest tests/ -q` (MockTransport offline, `@pytest.mark.asyncio` explicite), `tests/contract/test_tutor_imports.py` (gate I), nouveau `tests/pedagogy/` (asserts sous-chaînes déterministes, aucun LLM-juge en CI)

**Target Platform**: Linux desktop, `127.0.0.1:9215/tutor` offline-first, CPU-only, ≤8 GB RAM

**Project Type**: Web-service + SPA (backend thin → `tutor/service.py/store.py`, frontend `web/vue` + `tutor.html` legacy)

**Performance Goals**: Chargement 10 leçons + 3 invalides <1 s avec logs ; file 6 notions triée <100 ms ; goldens 10 cas <30 s

**Constraints**: Constitution I (tutor/ sans fastapi/textual), II (réutiliser diagnostic 5-cats, FSRS, `build_evaluation_prompt`), III (offline strict, pas de LLM en CI), IV (127.0.0.1), V (stdlib : json/pathlib/re/datetime/math), VI (log-and-skip via `errors.log`)

**Scale/Scope**: ~50 leçons/vague, ~200 goldens max, 4 urgences/niveaux, 1 pack de prompts FR

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I Cœur découplé**: PASS — `tutor/curriculum.py`, `tutor/forecast.py` (ou read-model dans `review.py`), chargement prompts, goldens sous `tests/` ; `server.py` thin si exposition (réutiliser `/adaptation/stability`).
- **II Préservation**: PASS — taxonomie 5-cats unifiée (pas de doublon), formule FSRS réutilisée (pas de second moteur), `build_evaluation_prompt` étendu (pas de nouveau builder).
- **III Tests hors-ligne**: PASS — fixtures fichiers, MockTransport, asserts sous-chaînes ; aucun appel réseau/LLM en CI.
- **IV Sécurité locale**: PASS — fichiers lus depuis chemins versionnés/locaux uniquement, aucun secret dans les packs/prompts (à verrouiller par test).
- **V Légèreté**: PASS — stdlib uniquement (`json`, `pathlib`, `re`, `math`, `datetime`), aucune dép.
- **VI Observabilité**: PASS — loader tolérant log-and-skip via `_log_error`/`errors.log`, goldens citant le fragment fautif.

Re-check post-Phase 1: PASS — `data-model.md` ajoute 1 table max (leçons versionnées si DB ; sinon fichiers seuls), `contracts/` additif, `quickstart.md` couvre les 4 stories.

## Project Structure

### Documentation (this feature)

```text
specs/013-vague1-socle/
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
│   ├── curriculum.py     # (nouveau) loader JSON + schéma leçon + pièges
│   ├── forecast.py       # (nouveau, ou read-model review.py) urgences par notion
│   ├── prompts.py        # + chargement assets/prompts/*.md + gabarit YAML
│   ├── assessment.py     # + build_evaluation_prompt (hint_level, recurring_mistakes)
│   ├── review.py         # + exposition urgences (read-model FSRS existant)
│   └── data/lessons/     # leçons JSON FR (créées à part, versionnées)
├── assets/prompts/       # prompts Markdown FR + FALLBACK intégré
tests/
├── pedagogy/             # goldens must_include/must_not_include + fixtures
├── contract/test_013_*.py
├── integration/test_013_*.py
└── unit/
```

**Structure Decision**: Single project existant ; 2 modules neufs minces max (`curriculum.py`, `forecast.py`), sinon extension des modules existants ; contenus (leçons, prompts, goldens) en fichiers versionnés, jamais en DB utilisateur.

## Complexity Tracking

> No constitution violations to justify.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
