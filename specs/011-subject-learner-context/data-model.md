# Data Model: Subject & Learner Context Isolation

**Feature**: 011 | **Source**: spec.md + research.md

## Entities (existantes, lecture seule — pas de nouvelle migration)

### Subject
- **Table**: `subjects(id, name, created_at, last_used_at, learner_id)` — `learner_id` nullable FK vers `learner_profiles`.
- **Règle**: unicité `(learner_id, name)` insensible à la casse via check app + `UNIQUE(subject_id,name)` pour index secondaire; "Non classé" est une ligne ordinaire (`name='Non classé'`).
- **Lifecycle**: create → rename (PATCH) → delete (DELETE + CASCADE sur `subject_books`, `learning_paths`, etc.) avec confirmation; active via `preferences.activeSubjectId`.

### LearnerProfile
- **Table**: `learner_profiles(id, name, avatar, created_at, updated_at)` — globale.
- **Filtrage par matière** (Q3=B): pas de colonne `subject_id`; le scope est dérivé : `SELECT DISTINCT learner_id FROM learning_paths WHERE subject_id=? UNION SELECT DISTINCT learner_id FROM lesson_discussions WHERE subject_id=?`.
- **Lifecycle**: create (POST learners) → activate (POST learners/{id}/activate met `activeLearnerId`) → delete (DELETE learners/{id} cascade `subjects.learner_id`, `learning_paths.learner_id`) avec confirmation.

### LearningPath (= Parcours)
- **Table**: `learning_paths(id, subject_id, learner_id, title, status, created_at)` + `path_steps(path_id, notion_id, step_index)`.
- **Scoping**: toujours filtré par `WHERE subject_id=? AND learner_id=?` (couple, FR-006).
- **Stats**: `progress = completedSteps / totalSteps` par couple.

### LessonDiscussion / PathStep
- `lesson_discussions(id, path_step_id, notion_id, subject_id, learner_id, status)` — unique `(path_step_id, learner_id)` + index `idx_lesson_discussions_learner`.
- Permet de dériver "apprenants ayant travaillé sur la matière".

### Book / Chunk
- `books(id, ...)` + `book_corpora(corpora_id, book_id)` + `chunks(subject_id, book_id, ...)` et `subject_books(subject_id, book_id)`.
- Bibliothèque filtrée: `JOIN subject_books WHERE subject_id=?`; toggle "Toutes" = sans WHERE.

## Relationships

- `LearnerProfile 1—* Subject` (via `subjects.learner_id`)
- `Subject 1—* LearningPath` (via `learning_paths.subject_id`)
- `Subject 1—* Book` (via `subject_books`)
- `LearningPath 1—* LessonDiscussion` (via `subject_id, learner_id`)
- `LearnerProfile 1—* LessonDiscussion` (via `learner_id`)

## Validation Rules (from FR-008)

- `Subject.name`: trim non vide, 1..64 chars, unique per `learner_id` case-insensitive → 400 "Nom déjà utilisé".
- `Learner.name`: trim non vide, 1..32 chars, unique global case-insensitive → 400.
- Delete matière "Non classé" renommée: même validation que autres, retour 409 si dernière matière et requêtes dépendantes.

## State Transitions

- `activeSubjectId` : `null → <id> → <otherId>` (persists localStorage, FR-003). Suppression active → fallback sur `subjects[0].id`.
- `showAllSources: bool` : toggle local uniquement.
