# Research: 013 Vague 1 Socle Pédagogique

**Feature**: 013 | **Date**: 2026-09-18 | **Sources**: `docs/autreprojet-reanalyse.md` (I1/I2/I6/I7 + Gate G1), `specs/010-greffe-autreprojet/` (deepdive, R-001→R-008, T001→T055), code existant (`prompts.py`, `assessment.py`, `fsrs.py`, `review.py`)

## Décisions

### D1. Leçons JSON strict, loader tolérant log-and-skip (I1)
- **Decision**: Schéma `id/title/prérequis/concepts/exercice{prompt, starter, visible, hidden}/mastery{pass_hidden + explain_concept}` + `common_mistakes[]` en JSON validé en stdlib (`json`), sous `tutor/data/lessons/*.json` versionnés ; fichier invalide = log `errors.log` + skip, doublon = warning + premier gagnant.
- **Rationale**: Clarification 2026-09-18 (JSON strict, cohérent packs 012) ; python-tutor prouve le pattern (loader tolérant), sans importer son contenu EN.
- **Alternatives considered**: Markdown+YAML frontmatter (rejeté : parseur ad hoc, moins strict) ; tables SQL seedées (rejeté : mélange données livrées/progression).

### D2. Pièges unifiés au diagnostic 5-cats (I1, gate G1)
- **Decision**: Les `common_mistakes` alimentent le diagnostic 5-catégories existant (T033) comme evidence textuelle ; aucune seconde taxonomie.
- **Rationale**: Gate G1 exige l'unification ; le moteur de diagnostic (`assessment.py:diagnose_error`) accepte déjà des signaux externes.
- **Alternatives considered**: Taxonomie séparée par leçon (rejeté : divergence, double maintenance).

### D3. Prévision = read-model FSRS existant (I2, gate G1)
- **Decision**: Pas de second moteur : lecture de `stability`/`retrievability` (`fsrs.py`, `review.py`) + mapping seuils fixes (overdue/urgent≤3j/warning≤7j/ok, clarification 2026-09-18) ; exposition via `/adaptation/stability` enrichi.
- **Rationale**: Formule déjà testée ; read-model pur `math/datetime`, O(n).
- **Alternatives considered**: Nouveau `forecast.py` avec moteur propre (rejeté : duplication, risque de divergence) — un module mince reste OK s'il ne fait que projeter.

### D4. Prompts Markdown + FALLBACK + gabarit YAML (I6)
- **Decision**: `assets/prompts/*.md` FR (fence ou fichier entier), chargement au boot avec FALLBACK intégré si absent/illisible (jamais de crash) ; gabarit de contexte (`leçon/soumission/preuves/hint_level/recurring_mistakes`) injecté dans `build_evaluation_prompt`.
- **Rationale**: Un enseignant règle ton et hints sans coder ; FALLBACK = robustesse offline.
- **Alternatives considered**: Prompts en DB (rejeté : non versionnable, non diffusable) ; tout en dur (statu quo, rejeté : bloque la francisation fine).

### D5. Pedagogy goldens déterministes, après/avec I6 (I7, gate G1)
- **Decision**: `tests/pedagogy/goldens/*.json` (soumissions FR maths/physique/SVT + Python) + asserts sous-chaînes `must_include/must_not_include` sur sorties mockées ; nom `pedagogy` distinct du harness d'exécution ; séquencé avec/après I6 (prompts externalisés testés).
- **Rationale**: Aucun LLM-juge en CI (pas de flaky) ; verrouille hint-first et l'interdiction de solution complète à chaque changement de modèle local.
- **Alternatives considered**: LLM-as-judge (rejeté : flaky, coût, offline) ; goldens avant I6 (rejeté : testerait des prompts en dur voués à bouger).

## Inconnues résolues (aucune NEEDS CLARIFICATION restante)

- Seuils d'urgence : fixes simples décidés en clarification (pas de calibrage).
- Format leçons : JSON strict décidé en clarification.
- Portée goldens initiaux : 10 cas min (SC-004), FR d'abord (contenus à créer à part).
