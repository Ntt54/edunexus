# Contracts — 013 Vague 1 Socle Pédagogique

Tous les endpoints respectent `Origin`/`Host` localhost (principe IV), loguent via `_log_error`, et restent additifs.

## GET /api/tutor/curriculum/lessons
Liste des leçons chargées (I1, FR-001). Lecture seule.
- **200**: `{"lessons": [{"id": "...", "title": "...", "prerequisites": [...], "concepts": [...]}]}` (invalides exclues, comptées dans `skipped` loggé).

## GET /api/tutor/subjects/{subject_id}/adaptation/stability (enrichi, I2, FR-004)
File par urgence d'oubli exposée via la route `/adaptation/stability` EXISTANTE (feature 008, FR-019) — PAS de route `/forecast` (remédiation analyze F1). Read-model pur sur FSRS existant (`fsrs.py`/`review.py`), aucune table, aucun second moteur.
- **Clés existantes intactes** : `objective`, `main_notion`, `success_criterion` (inchangées).
- **Clé ajoutée** `forgetting_queue` : `{"items": [{"notion_id": "...", "retrievability": 0.62, "days_until_threshold": 2, "predicted_drop_date": "2026-09-20", "urgency": "urgent"}], "order": "overdue-first"}`.
- **Urgences (seuils fixes, clarification 2026-09-18)** : `overdue` (seuil déjà passé), `urgent` (≤ 3 j), `warning` (≤ 7 j), `ok` (au-delà). Tri overdue-first puis `days_until_threshold` croissant.
- **Recalcul** : à chaque révision notée, sans persistance propre. **400/404** comme les routes filtrées existantes.

## Prompts pack (I6, FR-005/FR-006)
Pas de nouvelle route : chargement au boot depuis `assets/prompts/*.md` + FALLBACK ; gabarit injecté dans `build_evaluation_prompt` existant. Contrat : fichier absent/illisible = FALLBACK, jamais 500.

### Layout contract (T004, doc-only — implémentation T014/T015)
- **Emplacement** : `assets/prompts/*.md` (un fichier par usage : `evaluation.md`, `hint.md`, `diagnosis.md`, …), UTF-8, versionnés.
- **Structure d'un fichier** : corps Markdown FR (ton, consignes hint-first, interdictions) + bloc gabarit YAML optionnel en fin de fichier délimité par fences ` ```yaml context ` / ` ``` `.
- **Champs du gabarit** : `lesson` (id leçon), `submission` (code élève), `proofs` (preuves/tests), `hint_level` (0 = indice léger … 2 = guidage fort, jamais la solution), `recurring_mistakes` (liste d'ids `common_mistakes` de la leçon, alimentant l'évaluation), `explain_concept` (question d'explication exigée pour la maîtrise complète, FR-006).
- **FALLBACK** : constantes FR intégrées en code (`tutor/prompts.py`) ; tout fichier absent, illisible ou au gabarit invalide → FALLBACK + log `errors.log`, jamais de crash ni 500.
- **Zéro secret** : aucun token/clé/chemin machine dans `assets/prompts/` (verrouillé par test no-secrets T013).

## Pedagogy goldens (I7, FR-007/FR-008)
Pas de route : `tests/pedagogy/goldens/*.json` + asserts sous-chaînes. Contrat : échec = fragment fautif cité ; aucun appel réseau/LLM en CI.
