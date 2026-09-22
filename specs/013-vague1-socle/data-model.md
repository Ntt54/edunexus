# Data Model: 013 Vague 1 Socle Pédagogique

**Feature**: 013 | **Source**: spec.md + research.md (D1-D5)

Stratégie : fichiers versionnés d'abord (leçons, prompts, goldens), 1 table DB max si versionnage requis, réutilisation stricte des tables existantes (`reviews`, `learning_paths`, `error_history`, `quizzes`).

## Fichiers versionnés (pas de DB utilisateur)

### LessonFile (leçon curriculum JSON)
- **Fichiers**: `tutor/data/lessons/*.json`, enveloppe `{schemaVersion: "1.0", id, title, prerequisites[], concepts[], exercise{prompt, starter, visible[], hidden[]}, mastery{pass_hidden_tests, explain_concept}, common_mistakes[]}`
- **Règle**: `id` unique ; `hidden` ≥1 avec raison d'échec chacun ; `common_mistakes` rédigées (pas générées) ; invalide = log-and-skip + warning doublon.
- **Lifecycle**: author (FR, à part) → validate (stdlib) → load → serve (parcours/quiz/diagnostic).

### PromptPack (prompts Markdown)
- **Fichiers**: `assets/prompts/*.md` (system + gabarit YAML : leçon/soumission/preuves/hint_level/recurring_mistakes) + FALLBACK intégré en code.
- **Règle**: fichier absent/illisible = FALLBACK, jamais de crash ; aucun secret dedans (test no-secrets).
- **Lifecycle**: edit (enseignant) → reload au boot → serve (`build_evaluation_prompt`).

### PedagogyGolden (goldens déterministes)
- **Fichiers**: `tests/pedagogy/goldens/*.json` (soumission + `must_include[]`/`must_not_include[]` + rubrique hint-first).
- **Règle**: asserts sous-chaînes sur sorties mockées uniquement ; nom `pedagogy` distinct du harness d'exécution.
- **Lifecycle**: add (avec chaque nouveau comportement tuteur) → run CI → échec = fragment fautif cité.

## Tables existantes réutilisées (aucune nouvelle table requise)

- **ForgettingForecast** : read-model sur `reviews` (`stability`, `last_review`) + `progress` (mastery) — AUCUNE table, projection `{notion_id, retrievability, days_until_threshold, predicted_drop_date, urgency}` triée overdue-first.
- **Diagnostic 5-cats** : `error_history` existant + `common_mistakes` comme evidence (pas de taxonomie parallèle).

## Relationships

- `LessonFile 1—* CommonMistake` (embarqué JSON, pas de FK)
- `LessonFile *—* Concept` (via `prerequisites[]`/`concepts[]`, logique)
- `PedagogyGolden *—1 PromptPack` version (golden épingle la version du prompt testé)
- `ForgettingForecast *—1 LearnerProfile` (via requêtes existantes, pas de FK)

## Validation Rules (from FR-001→FR-008)

- Lesson JSON : `id` non vide unique, `exercise.visible` 1-3 items, `hidden` ≥1, `mastery` complet → 400/log-and-skip sinon.
- Urgences : seuils fixes (overdue = passé, urgent ≤3 j, warning ≤7 j, ok) — test exact.
- Prompts : aucun secret (grep CI) ; FALLBACK testé (dossier vide).
- Goldens : solution complète interdite quand exigée, fragment cité à l'échec.

## State Transitions

- `LessonFile` : `authored → validated → served` (jamais muté par l'app).
- `PromptPack` : `edited → reloaded (boot) → served`, `missing → FALLBACK`.
- `ForgettingForecast` : recalculé à chaque révision notée (pas de persistance propre).
