# Data Model: 012 Real Learning Packs

**Feature**: 012 | **Source**: spec.md + research.md (D4, D6, D8, D9)

Stratégie : 4 tables minimales + 2 extensions de tables existantes ; tout le reste réutilise les tables existantes (`reviews`, `learning_paths`, `lesson_discussions`, `quizzes`, `chunks`, `subject_books`, `subject_learners`, `learner_profiles`).

## Nouvelles tables

### CurriculumPack (packs curriculum versionnés)
- **Table**: `packs(id, pack_key, title, classe, examen, version, schema_version, min_app_version, sha256, source, statut, installed_at)`
- **Règle**: `pack_key` unique (`cm/bepc`, `cm/premiere-a`, `cm/terminale-c`…) ; `statut` ∈ {`squelette_à_valider`, `validé`} ; contenu JSON sous `data/curriculum/cm/` (titres/objectifs/durées/prérequis uniquement) ; progression utilisateur dans les tables existantes, jamais dans le JSON.
- **Lifecycle**: install (checksum) → upgrade in-memory (migrations `tutor/packs.py`) → jamais de mutation du JSON livré.

### AtomicNote (notes atomiques liées)
- **Table**: `atomic_notes(id, learner_id, subject_id, title, body_own_words, concept_ids JSON, source_refs JSON, created_at, updated_at)`
- **Table**: `atomic_note_links(from_id, to_id, rel, PRIMARY KEY(from_id, to_id))`, `rel` ∈ {`précise`, `contredit`, `mécanisme-de`, `exemple-de`} ; FK cascade les deux côtés.
- **Règle**: `title` = affirmation (pas un sujet), `body_own_words` non vide ; un plan = parcours de liens depuis une question.
- **Lifecycle**: create (depuis carnet ou leçon) → link → assemble-plan (lecture seule) → delete cascade les liens.

### SemesterPlan (planning semestre)
- **Table**: `semester_plans(id, learner_id, title, start_date, created_at)`
- **Table**: `plan_slots(plan_id, week_index, subject_id, minutes, kind)` `kind` ∈ {`révision`, `simulation`, `cours`} ; FK cascade.
- **Règle**: aucune semaine >150 % de la moyenne hebdomadaire ; recompaction plafonnée après absence (priorité notions fragiles).
- **Lifecycle**: generate (charge UE + dates) → adjust (déplacement manuel) → rollover (semaine écoulée → recompacte).

### ParentShare (consentement et partage parent)
- **Table**: `parent_shares(learner_id PRIMARY KEY, consent, share_token UNIQUE, revoked_at)` ; `consent` ∈ {`accordé`, `révoqué`}.
- **Règle**: sans `consent=accordé`, la route agrégée retourne 403 ; `share_token` régénérable ; révocation immédiate (token invalide).
- **Lifecycle**: grant (par l'apprenant) → view (lecture seule agrégée) → revoke → token mort.

## Extensions de tables existantes

### LearnerProfile (+2 colonnes, migration idempotente PRAGMA-table_info)
- `readability_json TEXT` : `{fontScale, lineHeight, letterSpacing, theme, dyslexia}` (défauts : 100 %, 1.0, normal, système, off).
- Streak hebdo : réutilise `current_streak/longest_streak` + nouvelle colonne `streak_freeze_json` (`{frozen_week}`) pour le joker 1 jour.

### Quiz/Exam (colonnes, pas de nouvelle table)
- `recall_written` : nouveau kind dans `_VALID_Q_KINDS` (validation applicative, pas de migration) ; `ExamSession` inchangée + `score_20 REAL` (barème pondéré) et `blueprint_key` (ex. `cm/terminale-c`).

## Relationships

- `CurriculumPack 1—* Subject` (via `pack_key`/`blueprint_key`, logique, pas de FK)
- `LearnerProfile 1—* AtomicNote` (via `learner_id`, cascade)
- `AtomicNote *—* AtomicNote` (via `atomic_note_links`, double cascade)
- `LearnerProfile 1—* SemesterPlan 1—* PlanSlot` (cascades)
- `LearnerProfile 1—1 ParentShare` (cascade ; token hors FK)
- `Subject 1—* ExamSession` (existant, +score_20 calculé)

## Validation Rules (from FR-008, FR-010, FR-011, FR-013)

- `AtomicNote.title` : non vide, ≤120 chars ; `body_own_words` : non vide ; `rel` dans l'enum → 400 sinon.
- `SemesterPlan` : charge UE >0, dates futures ; génération rejetée (400) si une semaine dépasse 150 % sans recompactage possible.
- `ParentShare` : `share_token` 32+ chars aléatoires ; 403 si révoqué/absent.
- Lisibilité : `fontScale` 100-200 %, `lineHeight` ∈ {1.0, 1.5} ; presets dyslexie conformes BDA.

## State Transitions

- `packs.statut` : `squelette_à_valider → validé` (relecture humaine OBC, jamais automatique).
- `ExamSession` : `active → verrouillée (temps écoulé) → corrigée (/20) → [interrompue]` (reprise avec temps restant recalculé).
- `ParentShare.consent` : `— → accordé ⇄ révoqué` (régénération token à chaque accord).
