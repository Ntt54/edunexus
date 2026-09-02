# Feature Specification: Leçon cliquable — discussion centrée sur la notion

**Feature Branch**: `009-lecon-discussion-centree`

**Created**: 2026-08-31

**Status**: Draft

**Input**: User description: "j'aimerais expliquer ma vision de mon logiciel donc il y'a la creation de parcour avec les trois methode implementer .maintenant chaque lecons est cliquable maintenant qui ouvre une discution centrer sur cette notion par exemple j'ai une lecon PAR EXEMPLE sur les variable je puisse avoir une discution centraliser sur cette lecon maintenant soit on peut cliquer sur un boutton pour generer un cour sur les variable ou sur un autre boutton faire une synthese afin que l'utilisateur puisse se rememorer le contenu de cette lecon fair des execice pour tester cette notion apres ce test on considere que le cour est terminer .si il des question pour plus de clarification tu les pose"

## Clarifications

### Session 2026-08-31

- Q: Les 3 méthodes de création de parcours existantes doivent-elles rester inchangées ? → A: Oui, hypothèse — la feature ajoute une couche "leçon cliquable" au-dessus des parcours existants sans modifier leur génération.
- Q: Persistance de la discussion centrée (Q1) → A: Option B — nouvelle entité dédiée `LessonDiscussion` isolée (`lesson_discussions` + `lesson_messages`), liée par `path_step_id`/`notion_id`.
- Q: Règle de passage à "cours terminé" (Q2) → A: Combinaison B+C — seuil 60 % (3/5 minimum) pour validation automatique → `completed` ; si < 60 % → reste `in_progress` avec possibilité de Refaire les exercices ; bouton manuel "Marquer comme terminé quand même" toujours disponible après feedback (logique retenue comme meilleur compromis pédagogique + contrôle utilisateur).
- Q: Distinction Cours vs Synthèse (Q3) → A: Option A — Cours = génération RAG complète (800–1200 mots, définitions+exemples) ; Synthèse = résumé du cours généré (150–250 mots, points clés), donc séquentielle et persistante.
- Q: Isolation par apprenant pour LessonDiscussion (clarify 2026-08-31) → A: Oui — `LessonDiscussion` isolée par `learner_id` (Option A), filtrage par apprenant actif pour cohérence avec le foyer multi-utilisateurs 008.
- Q: Emplacement UI de la discussion (clarify 2026-08-31) → A: Vue dédiée plein écran `/lecon/:id` avec bouton retour vers le parcours (Option A).
- Q: Synthèse sans cours préalable (clarify 2026-08-31) → A: Option C — synthèse générable directement depuis les sources, même sans cours préalable (indépendante).
- Q: Régénération des exercices après échec (clarify 2026-08-31) → A: Option A — nouveaux exercices régénérés à chaque tentative (3–5 nouveaux, évite l'apprentissage par cœur).
- Q: Passage à `in_progress` (clarify 2026-08-31) → A: Option A — dès l'ouverture de la discussion, la leçon passe de `not_started` à `in_progress`.
- Q: Arborescence binaires/modèles (amendement 2026-08-31) → A: `vendor/llama.cpp/<version>/` pour chaque version de llama.cpp (b10632, …) + `models/gguf/` centralisé pour tous les GGUF ; dossiers créés et git-ignorés.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ouvrir une discussion centrée depuis une leçon du parcours (Priority: P1)

L'apprenant consulte son parcours (généré par l'une des 3 méthodes existantes). Chaque leçon/étape du parcours est cliquable. Cliquer sur une leçon (ex. "Les variables") ouvre une **discussion centrée sur cette notion** : l'historique, le contexte RAG et les actions portent uniquement sur cette leçon, pas sur l'ensemble du parcours. L'utilisateur peut poser des questions libres sur la notion et obtenir des réponses ancrées dans les sources de la matière.

**Why this priority**: C'est le cœur de la vision — transformer le parcours d'une liste statique en point d'entrée vers un apprentissage contextualisé par notion. Sans ça, les autres actions n'ont pas de cadre.

**Independent Test**: Générer un parcours contenant "Variables", cliquer sur la leçon "Variables", vérifier qu'une discussion s'ouvre avec contexte limité à cette notion (sources filtrées, titre de la notion visible, historique isolé). Poser une question "c'est quoi une variable ?" et vérifier que la réponse est ancrée et ne dérive pas vers une autre leçon.

**Acceptance Scenarios**:

1. **Given** un parcours généré avec plusieurs leçons, **When** l'utilisateur clique sur "Variables", **Then** une discussion centrée s'ouvre avec l'identifiant de la notion/leçon visible et un historique vide ou isolé de cette leçon.
2. **Given** une discussion centrée sur "Variables" ouverte, **When** l'utilisateur envoie "explique les variables", **Then** la réponse est générée en RAG à partir des sources liées à cette notion (pas l'ensemble des livres) et cite ses sources.
3. **Given** deux leçons "Variables" et "Boucles", **When** l'utilisateur ouvre successivement leur discussion, **Then** les historiques sont séparés (pas de mélange de contexte).

---

### User Story 2 - Générer un cours ou une synthèse à la demande pour la notion (Priority: P1)

Depuis la discussion centrée sur une notion, deux boutons sont disponibles : **"Générer le cours"** (ex. cours complet sur les variables) et **"Faire une synthèse"** (rappel condensé pour remémoration). "Générer le cours" produit un contenu pédagogique structuré (définitions, exemples, cas d'usage) ancré dans les sources. "Synthèse" produit un résumé concis du contenu déjà vu/ingéré pour cette leçon, afin de raviver la mémoire sans réapprendre tout. Les deux contenus sont affichés dans la discussion et restent consultables.

**Why this priority**: Ce sont les deux actions explicites demandées par l'utilisateur pour la phase d'acquisition/rappel avant l'évaluation.

**Independent Test**: Dans la discussion "Variables", cliquer "Générer le cours" → vérifier qu'un contenu structuré apparaît avec sources. Cliquer "Faire une synthèse" → vérifier qu'un résumé plus court apparaît, distinct du cours, toujours dans la même discussion.

**Acceptance Scenarios**:

1. **Given** discussion centrée sur "Variables", **When** l'utilisateur clique "Générer le cours", **Then** un contenu de type `lesson_course` est généré (RAG, niveau = profil de la matière), affiché dans la discussion et lié à la leçon.
2. **Given** discussion centrée sur "Variables", **When** l'utilisateur clique "Faire une synthèse", **Then** un contenu de type `lesson_summary` est généré (condensé, points clés, exemples brefs) et affiché, sans écraser le cours s'il existe.
3. **Given** cours et synthèse générés, **When** l'utilisateur rouvre la leçon plus tard, **Then** les deux contenus restent consultables dans l'historique de la discussion.

---

### User Story 3 - Faire des exercices sur la notion et clôturer la leçon (Priority: P1)

Toujours depuis la discussion centrée, un bouton **"Faire des exercices"** (ou "Tester cette notion") lance une série d'exercices ciblés sur la notion. Après le test, la leçon est considérée comme **terminée**. L'exercice est évalué (score/feedback) et l'état de la leçon passe à terminé, ce qui met à jour la progression du parcours.

**Why this priority**: C'est le mécanisme de validation demandé — l'exercice est la porte de sortie qui fait avancer le parcours.

**Independent Test**: Dans "Variables", générer le cours puis cliquer "Faire des exercices", répondre aux questions, vérifier qu'un score/feedback s'affiche et que l'étape "Variables" passe à l'état `completed` dans le parcours (visible dans la liste du parcours).

**Acceptance Scenarios**:

1. **Given** discussion "Variables" avec cours affiché, **When** l'utilisateur clique "Faire des exercices", **Then** 3–5 exercices ciblés sur "Variables" sont générés (types adaptés au profil : QCM, code à trous, etc.) et présentés séquentiellement.
2. **Given** exercices en cours, **When** l'utilisateur soumet ses réponses, **Then** le système évalue, affiche le score et un feedback par question (correction ancrée dans les sources).
3. **Given** exercices évalués avec score ≥ 60 % (≥ 3/5), **When** l'évaluation est terminée, **Then** l'état de la leçon passe automatiquement à `completed` et la progression du parcours est mise à jour ; si score < 60 %, la leçon reste `in_progress` avec proposition de refaire, et un bouton "Marquer comme terminé quand même" permet une validation manuelle après avoir vu le feedback.

---

### User Story 4 - Naviguer et retrouver l'état d'avancement par leçon (Priority: P2)

L'utilisateur voit dans le parcours l'état de chaque leçon (non commencée / en cours / terminée) et peut à tout moment rouvrir une leçon terminée pour revoir cours/synthèse/discussion sans réévaluer.

**Why this priority**: Donne de la visibilité sur la progression notion par notion ; évite de perdre le travail déjà fait.

**Independent Test**: Après avoir terminé "Variables" et laissé "Boucles" non commencée, vérifier que le parcours affiche deux états distincts et que rouvrir "Variables" restaure son historique.

**Acceptance Scenarios**:

1. **Given** parcours avec 3 leçons dont 1 terminée, **When** l'utilisateur affiche le parcours, **Then** chaque leçon montre son statut (badge/couleur) et la progression globale.
2. **Given** leçon terminée, **When** l'utilisateur la rouvre, **Then** cours, synthèse, exercices et discussion restent consultables, sans obligation de refaire le test.

---

### Edge Cases

- Que se passe-t-il si l'utilisateur clique sur une leçon dont les sources sont vides ou non indexées ? → Message explicite + proposition d'importer des sources.
- Que se passe-t-il si la génération de cours/synthèse/exercices échoue (LLM indisponible) ? → Erreur visible + log `errors.log` (constitution VI), bouton "Réessayer", pas d'état incohérent.
- Comment gérer une leçon cliquée alors qu'une génération est déjà en cours ? → Désactiver les boutons + indicateur "génération en cours", 1 run à la fois (invariant existant `/ws/tutor`).
- Que se passe-t-il si l'utilisateur abandonne les exercices en cours ? → État `in_progress` conservé, reprise possible, pas de passage à `completed`.
- Leçon supprimée/réordonnée dans le parcours pendant qu'une discussion est ouverte ? → Discussion reste liée à l'ID de notion, pas à la position ; si la leçon est supprimée, discussion en lecture seule avec message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système MUST rendre chaque leçon/étape d'un parcours existant cliquable et ouvrir une discussion centrée sur la notion associée (filtrage du contexte à cette notion).
- **FR-002**: La discussion centrée MUST être isolée par leçon (historique, RAG, génération) — deux leçons différentes ne partagent pas le même contexte.
- **FR-003**: La discussion centrée MUST permettre une conversation libre (question → réponse RAG) limitée à la notion courante, avec citations de sources.
- **FR-004**: Depuis la discussion centrée, le système MUST fournir un bouton "Générer le cours" qui produit un contenu pédagogique structuré pour la notion (définition, exemples, cas d'usage) via RAG et l'affiche dans la discussion.
- **FR-005**: Depuis la discussion centrée, le système MUST fournir un bouton "Faire une synthèse" qui produit un résumé condensé (points clés) de la notion — directement depuis les sources si aucun cours n'existe, sinon depuis le cours généré — distinct du cours complet (clarify 2026-08-31: indépendante).
- **FR-006**: Depuis la discussion centrée, le système MUST fournir un bouton "Faire des exercices" / "Tester cette notion" qui génère 3–5 exercices ciblés sur la notion (types adaptés au `PedagogicalTemplate` de la matière si disponible) ; chaque tentative "Refaire" MUST régénérer 3–5 nouveaux exercices (clarify 2026-08-31).
- **FR-007**: Le système MUST évaluer les exercices soumis, afficher score et feedback par question, et persister le résultat lié à la leçon/discussion.
- **FR-008**: Après évaluation des exercices, le système MUST appliquer la règle : si score ≥ 60 % (≥ 3/5) → passage automatique à `completed` ; si score < 60 % → la leçon reste `in_progress` avec possibilité de relancer les exercices ; dans tous les cas après feedback, un bouton "Marquer comme terminé quand même" permet une validation manuelle, et la progression du parcours est mise à jour.
- **FR-009**: Le système MUST afficher l'état de chaque leçon dans le parcours (non commencée / en cours / terminée) et la progression globale ; l'ouverture d'une discussion fait passer la leçon de `not_started` à `in_progress` (clarify 2026-08-31).
- **FR-010**: Cours, synthèse, exercices et historique de discussion MUST rester consultables après clôture (re-ouverture en lecture + possibilité de relancer).
- **FR-011**: Les contenus générés (cours, synthèse) et les exercices MUST conserver leur provenance : sources utilisées (livre/chapitre/page/extrait), niveau de confiance, et distinction visuelle extrait/généré (exigence transversale existante).
- **FR-012**: Le système MUST journaliser toute erreur de génération/évaluation (LLM, RAG, scoring) dans `errors.log` et afficher un message clair côté UI (constitution VI).
- **FR-013**: Le système MUST respecter le principe I (logique dans `tutor/`, transport fin dans `web/server.py`, pas d'import `fastapi`/`textual` dans le cœur) et les tests hors-ligne (constitution III).

- **FR-014**: Discussion centrée sur la notion MUST être une nouvelle entité dédiée `LessonDiscussion` (tables `lesson_discussions` + `lesson_messages`), isolée par `path_step_id`/`notion_id`, `subject_id` et `learner_id` (filtrage par apprenant actif, clarify 2026-08-31), avec historique et contenus propres (choix Q1:B).
- **FR-015**: "Générer le cours" MUST produire un contenu RAG complet (800–1200 mots, définitions + exemples + cas d'usage, ancré dans les sources, persistant) ; "Faire une synthèse" MUST produire un résumé condensé (150–250 mots, points clés, persistant) — depuis le cours s'il existe, sinon directement depuis les sources (clarify 2026-08-31: synthèse indépendante, Q3:A adapté en C) — les deux sont stockés comme `GeneratedLessonContent` avec `kind` distinct.

### Key Entities

- **LearningPath / PathStep (existant, étendu)**: Parcours et étape/leçon. Ajout : `status` par leçon (`not_started` | `in_progress` | `completed`), lien vers discussion centrée.
- **LessonDiscussion** *(nouveau, Q1:B + clarify learner)*: Discussion centrée sur une notion. Attributs : `id`, `path_step_id` / `notion_id`, `subject_id`, `learner_id`, `messages[]`, `generated_contents[]` (cours/synthèse), `exercise_attempts[]`, `status`. Filtrage obligatoire par `learner_id` actif.
- **GeneratedLessonContent**: Contenu produit à la demande. Attributs : `id`, `lesson_discussion_id`, `kind` (`lesson_course` | `lesson_summary`), `content` (markdown), `sources[]` (SourceReference), `confidence`, `created_at`. Le `lesson_summary` est dérivé du cours s'il existe, sinon des sources directes (clarify 2026-08-31).
- **LessonExerciseAttempt**: Tentative d'exercices pour une leçon. Attributs : `id`, `lesson_discussion_id`, `questions[]`, `answers[]`, `score`, `feedback[]`, `passed` (bool, `true` si ≥ 60 %), `created_at`. Le passage à `completed` suit FR-008 (auto si ≥ 60 % sinon manuel).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un apprenant peut, depuis un parcours existant, ouvrir une leçon, générer un cours, obtenir une synthèse et terminer la leçon par des exercices en moins de 5 minutes (hors temps de lecture) sur machine CPU locale.
- **SC-002**: 90 % des réponses en discussion centrée citent au moins une source liée à la notion (pas de hallucination hors-notion) lors d'un test sur 20 questions couvrant 4 notions distinctes.
- **SC-003**: 95 % des clics sur une leçon ouvrent la discussion centrée en moins de 500 ms (hors génération LLM), et l'historique de la leçon est restauré correctement.
- **SC-004**: Le taux de complétion d'une leçon (exercices soumis → statut `completed`) est traçable : le parcours affiche la progression notion par notion sans régression (re-ouverture ne réinitialise pas l'état).
- **SC-005**: Aucune erreur de génération n'est silencieuse : 100 % des échecs LLM/RAG affichent un message UI et une entrée dans `errors.log`.

## Assumptions

- Les 3 méthodes de création de parcours existantes restent inchangées ; la feature ajoute une interaction au-dessus du `PathStep` sans modifier `GraphBuilder`/`PathBuilder`.
- Le filtrage RAG par notion peut s'appuyer sur `concept_id` / `title` du `CompetencyNode` et sur `get_indexed_chunks` filtré par mots-clés de la notion + `SourceReference` existante ; pas besoin d'un nouvel index vectoriel par leçon en V1.
- La discussion centrée réutilise le pipeline `TutorService.ask` / `/ws/tutor` existant avec un paramètre de scope (ex. `notion_id` ou `lesson_id`), sans dupliquer la logique de streaming.
- Types d'exercices : réutilisation du moteur existant (QCM, code à trous, etc.) ; nombre par défaut 3–5, paramétrable.
- Persistance : SQLite dans `~/.config/ollama-tui/`, migrations idempotentes (style existant).
- UI : extension de `tutor.html` (inline CSS/JS, zéro dépendance externe) — vue dédiée plein écran `/lecon/:id` avec bouton retour (clarify 2026-08-31).
- Binaires & modèles (amendement 2026-08-31) : `vendor/llama.cpp/<version>/llama-server` (ex. `b10632`) versionné côte-à-côte, et `models/gguf/*.gguf` centralisé ; config `tutor.llama_bin` pointe vers le binaire versionné, `tutor.llama_models_dir` vers `models/gguf` (contient `embed_gguf` et `docling_gguf`). Les deux dossiers sont git-ignorés, démarrage paresseux, un seul `llama-server` actif (Constitution IV).

## Decisions

### Décision 1 — Persistance de la discussion centrée (Q1:B)

Nouvelle entité `LessonDiscussion` isolée retenue. Justification : isolation stricte par leçon, historique propre, évolution sans impacter `TutoringSession` existante.

### Décision 2 — Règle de passage à "cours terminé" (Q2:B+C hybride)

Logique retenue : **seuil 60 % + validation manuelle en filet**. Si score ≥ 60 % (3/5) → `completed` automatique. Si < 60 % → reste `in_progress`, bouton "Refaire les exercices" + bouton "Marquer comme terminé quand même" disponible après feedback. L'utilisateur garde le contrôle final sans être bloqué, tout en gardant un signal pédagogique. Tentatives illimitées en V1.

### Décision 3 — Distinction Cours vs Synthèse (Q3:A)

Cours = RAG complet 800–1200 mots (définitions+exemples) ; Synthèse = résumé du cours généré 150–250 mots, persistante. Justification : synthèse cohérente avec le cours affiché, deux `GeneratedLessonContent` distincts.

