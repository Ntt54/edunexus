# Contracts — 011 Subject & Learner Context

## PATCH /api/tutor/subjects/{id}
Renomme une matière (y compris "Non classé").
- **Body**: `{"name": "Python"}` (trim, 1..64)
- **200**: `{"id": "...", "name": "Python"}`
- **400**: `{"detail": "Nom déjà utilisé" | "Nom requis"}`
- Auth: `X-Learner-Id` facultatif pour scoping (008)

## DELETE /api/tutor/subjects/{id}
Supprime avec confirmation préalables côté UI.
- **200**: `{"deleted": "...", "fallbackSubjectId": "..."}` (si active supprimée)
- **404** si id inconnu
- Cascade: `subject_books`, `learning_paths`, `lesson_discussions`

## GET /api/tutor/learning-paths?subject_id=&learner_id=
Filtre obligatoire par couple. Sans params = tous (compat historique, mais UI doit toujours fournir le couple).
- **200**: `{"paths": [{"id","subject_id","learner_id","steps":[...] , "progress":0.42 }] }`

## GET /api/tutor/dashboard?subject_id=&learner_id=
Stats "Prochaine étape", Sources/Notions/Étape active — nouvel endpoint ou extension de `/api/tutor/stats`.
- **200**: `{"nextStep": {"notion":"...","progress":..} | null, "counts": {"sources":8,"notions":12}}`
- Sans couple → `nextStep:null` si matière vide (FR-007 état vide)

## GET /api/tutor/books?subject_id=&all=false
Bibliothèque filtrée (Q4=C).
- `?subject_id=xxx` → seulement cette matière
- `?all=true` ou sans `subject_id` → toutes (toggle UI)
- **200**: `{"books": [...] }`

## GET /api/tutor/learners?subject_id=
Filtre par matière dérivé (Q3=B).
- Sans `subject_id` → tous les learners (existence)
- Avec `subject_id` → `SELECT learners WHERE EXISTS(path OR discussion WITH (subject_id, learner_id))`
- **200**: `{"learners": [{"id","name","created_at"}]}`

## POST /api/tutor/learners
Create learner pour matière active (FR-005).

## POST /api/tutor/learners/{id}/activate + DELETE

Tous les endpoints respectent `Origin`/`Host` localhost (principle IV) et log erreurs via `_log_error` + `POST /api/log-error`.

## UI Contract

- `preferences.activeSubjectId` + `preferences.activeLearnerId` persistés `localStorage`.
- Changement matière → `emit('subjectChange')` → invalide `filteredPaths`, `dashboard`, `learnersFiltered`, `booksFiltered`.
- Suppression matière active → fallback `subjects[0]` + toast "Matière supprimée".
