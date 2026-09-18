# Contracts — 012 Real Learning Packs

Tous les endpoints respectent `Origin`/`Host` localhost (principe IV), loguent via `_log_error`, et restent additifs (aucun endpoint existant modifié de façon incompatible).

## GET /api/tutor/reminders?subject_id=&learner_id=
Vue « À réviser » (FR-001). Délègue à `due_reviews` enrichi.
- **200**: `{"due": [{"kind": "carte", "id": "...", "title": "...", "overdue_days": 0}], "due_count": 3, "stale_plan": false}` (v1 : items cartes uniquement ; notions fragiles prises en compte dans le recompactage, pas d'items `notion`)
- `stale_plan: true` quand retard >7 j (recompactage proposé, jamais d'empilement infini).

## POST /api/tutor/quizzes (recall_written)
Quiz à rappel rédigé (FR-002). `kind: "recall_written"` accepté dans le payload existant.
- **200**: quiz avec questions `type: "recall_written"` (énoncé + réponse attendue cachée).
- Correction via `POST /quizzes/{id}/submit` existant (juge LLM, fallback exact offline).

## GET /api/tutor/packs / packs/{key}
Référentiel curriculum hybride (FR-005). Lecture seule.
- **200**: `{"packs": [{"key": "cm/terminale-c", "title": "...", "classe": "Terminale", "version": "1.0.0", "statut": "squelette_à_valider"}]}` / contenu `{schemaVersion, packKey, classe, examen, matieres}` (enveloppe du loader `tutor/packs.py`).
- **404** si clé inconnue.

## POST /api/tutor/exams (blueprint + /20)
Épreuves blanches BEPC/probatoire/bac (FR-006). Étend le payload existant.
- Body: `{..., "blueprint": "cm/terminale-c", "duree_min": 240}` ; **200**: `ExamSession` + `{"score_20": 13.5, "detail_competences": [...]}`.
- Verrouillage au temps imparti ; reprise après coupure → statut `interrompue` puis verrouillée.
- **400** blueprint inconnu ou durée invalide.

## GET /api/tutor/parent/overview?token=
Vue parent lecture seule (FR-008, parents seuls v1).
- **200**: `{"temps_semaine_min": 95, "maitrise": {"Maths": 62}, "erreurs_frequentes": [...], "prochain_jalon": "Probatoire blanc — 12 juin"}`.
- **403** si token absent/révoqué/sans consentement. Aucune écriture sur cette route.

## POST /api/tutor/learners/{id}/share + DELETE
Consentement parent (FR-008). `POST` accorde (génère `share_token`), `DELETE` révoque (token mort immédiat).
- **200**: `{"share_token": "..."}` / `{"revoked": true}`. **404** apprenant inconnu.

## POST /api/tutor/notes/atomic + GET /notes/atomic
Notes atomiques liées (FR-010).
- Body: `{"title": "affirmation", "body": "en propres mots", "concept_ids": [...], "source_refs": [...]}` ; **201**: note + `id`.
- **POST /notes/atomic/{id}/links** `{to_id, rel}` ; **GET /notes/atomic/plan?question=** assemble un plan depuis les liens. **400** `rel` invalide.

## POST /api/tutor/planner/semester
Planning semestre (FR-011).
- Body: `{"ues": [{"subject_id": "...", "heures": 120}], "epreuves": [{"date": "...", "subject_id": "..."}]}`.
- **200**: créneaux hebdo, aucune semaine >150 % de la moyenne. **400** si impossible sans dépassement.

## GET /api/tutor/readability (profil) + PUT
Mode lisibilité WCAG 2.2 AA (FR-013). Persisté sur le profil apprenant.
- **200**: `{fontScale: 100, lineHeight: 1.0, letterSpacing: "normal", theme: "systeme", dyslexia: false}` ; presets `dyslexie` BDA.
- Thèmes tous pré-vérifiés ≥4.5:1 (texte) / 3:1 (grand texte + UI).

## Réponses citées (FR-009)
Pas de nouvelle route : `{answer, sources}` existant, rendu inline (affirmation → paragraphe exact) côté `LessonView`. Contrat : toute affirmation factuelle porte sa source cliquable.

## XP sain (FR-012)
Pas de nouvelle route : `XP = base(difficulté) × decay(répétition <7j)` aux points existants, cap journalier, série hebdo + 1 joker. Observable via `GET learner profile` existant.
