# Research: Subject & Learner Context Isolation

**Feature**: 011-subject-learner-context | **Date**: 2026-09-14

## Decision Summary

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Vue filtering strategy | Filtrer côté client via store `preferences` + recomputation des getters `activeSubjectId`/`activeLearnerId`, invalider caches `learningPaths`/`dashboardStats` au changement | Existe déjà `preferences.ts` avec `activeSubject` et `activeLearner`; évite nouvelle dép, reste dans principe V |
| Backend scoping | Ajouter query params `?subject_id=` et `?learner_id=` (ou header `X-Learner-Id`) sur `GET /api/tutor/learning-paths`, `/stats`, `/discussions`; `learners_list` supporte `?subject_id=` en filtrant sur présence de données (learning_paths/discussions avec couple) | Réutilise indexes existants `idx_*_learner`, `FOREIGN KEY(subject_id)` — pas de migration lourde |
| Learners per matière (Q3=B) | Implémenter `GET /api/tutor/learners?subject_id=` qui retourne apprenants ayant au moins un parcours/discussion pour (subject_id, learner_id) ; pas de nouvelle colonne `learners.subject_id` | Éviter migration inverse (learners sont parents de subjects via `subjects.learner_id` côté 008); le filtrage par existence de couple (matière × apprenant) correspond à la sémantique "couple" de FR-006 |
| Non classé renommable/supprimable (Q2) | Autoriser `PATCH /api/tutor/subjects/{id}` et `DELETE` pour "Non classé" comme autres, avec validation unicité insensible à la casse + confirmation UI modale | Contraintes vérifiées: `UNIQUE(subject_id,name)` et `subjects.learner_id` nullable; pas de garde-fou nécessaire |
| Bibliothèque filtrée (Q4=C) | `GET /api/tutor/books?subject_id=` filtré par défaut; UI ajoute toggle "Toutes" qui appelle sans param | Réutilise `book_corpora`/`chunks.subject_id`; conforme à YAGNI |
| Sources toggle persistence | Stocker `showAllSources: boolean` dans `preferences.ts` (localStorage) | Léger, pas de backend |
| Confirmation suppression | Modale réutilisant `ConfirmDialog` existant (déjà pour exercices) | Cohérence visuelle, pas de lib externe |
| Tests | Mock offline `httpx.MockTransport` pour API params, plus tests Vue unitaires sur getters `filteredPaths` | Conforme principe III |

## Alternatives considered

- **Nouvelle colonne `learners.subject_id`**: rejetée — brise le modèle 008 où `subjects.learner_id` est le parent (family), migration coûteuse + perte de cohabitation familiale.
- **Nouveau store Pinia séparé `subjectStore`**: rejeté — sur-ingénierie; le store `preferences` suffit et est déjà réactif.
- **Filtrage uniquement backend sans invalidation cache**: rejeté — UI afficherait 1-2s de données périmées (violation FR-009).

## Validation

- Vérifié `store.py:subjects.learner_id` + index `idx_subjects_learner`, `learner_profiles` global, `learning_paths.subject_id` + `learner_id`.
- Vérifié `web/server.py:GET /learners` actuel sans filtre subject -> extension compatible.
- Vérifié `web/vue/client/src/stores/preferences.ts` persiste déjà `activeSubjectId`/`activeLearnerId` en `localStorage`.
- Vérifié contrats 010 `web-contract-test.md` allowlist — ajout de query param ne casse pas.
