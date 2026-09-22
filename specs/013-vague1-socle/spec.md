# Feature Specification: Vague 1 Socle Pédagogique (I1→I2→I6→I7)

**Feature Branch**: `013-vague1-socle`

**Created**: 2026-09-18

**Status**: Draft

**Input**: User description: "implemente le backen et l'ui" puis "fait plutot tout complet" — shortlist complète `docs/autreprojet-reanalyse.md` (I1–I12 + U1–U5) livrée en vagues incrémentales. Ce spec couvre la Vague 1 (socle pédagogique) : I1 curriculum-as-data + pièges classiques, I2 prévision d'oubli par notion, I6 prompts Markdown + gabarit YAML, I7 pedagogy goldens (après/avec I6).

## Clarifications

### Session 2026-09-18

- Q: Seuils d'urgence d'oubli (overdue/urgent/warning/ok) ? → A: Fixes simples — chute passée = overdue, chute dans ≤3 j = urgent, ≤7 j = warning, sinon ok.
- Q: Format des fichiers leçons (JSON vs Markdown+YAML) ? → A: JSON strict — validable en stdlib, cohérent avec les packs 012.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Leçons structurées avec pièges classiques (Priority: P1)

En tant qu'apprenant, je travaille des leçons décrites en fichiers (prérequis, concepts, exercices visibles/cachés, seuil de maîtrise) et je reçois les erreurs classiques de chaque notion avant de les commettre, avec l'exigence d'expliquer (pas seulement résoudre).

**Why this priority**: Les pièges classiques par notion sont la matière des annales BEPC/probatoire/bac ; les prérequis explicites évitent de proposer un exercice dont la base manque. Socle de tout le reste.

**Independent Test**: Charger un fichier leçon (prérequis + 1 exercice + 2 pièges), vérifier que le parcours exige le prérequis, que le diagnostic cite un piège, et qu'un exercice impose `explain_concept` — sans les autres stories.

**Acceptance Scenarios**:

1. **Given** un fichier leçon invalide, **When** le chargeur le lit, **Then** il est ignoré avec une ligne de log (jamais de crash) et les leçons valides restent disponibles.
2. **Given** une leçon avec prérequis manquant chez l'apprenant, **When** le parcours est généré, **Then** le prérequis est proposé d'abord, avec la taxonomie d'erreurs unifiée au diagnostic 5-catégories existant.
3. **Given** un exercice avec `explain_concept`, **When** l'apprenant résout juste mais n'explique pas, **Then** la maîtrise reste partielle (résout seul ≠ explique/transfère).

---

### User Story 2 - File de révision par urgence d'oubli (Priority: P1)

En tant qu'apprenant, je vois mes notions triées par urgence d'oubli (overdue d'abord, avec date prédite de chute sous le seuil) au lieu d'un coût global abstrait, et mon planning priorise les fragiles.

**Why this priority**: Transforme la stabilité FSRS existante en file actionnable (« 3 notions overdue avant le probatoire blanc ») — le cas d'usage examen central, en pur calcul local.

**Independent Test**: Avec 6 notions à stabilités variées, vérifier l'ordre overdue-first, les dates prédites et les niveaux ok/warning/urgent/overdue — via la formule FSRS existante uniquement, sans les autres stories.

**Acceptance Scenarios**:

1. **Given** 3 notions sous le seuil et 3 au-dessus, **When** j'ouvre la file, **Then** les overdue viennent d'abord avec leur date de chute prédite.
2. **Given** une révision notée, **When** la file est recalculée, **Then** les urgences reflètent la nouvelle stabilité sans second moteur FSRS.
3. **Given** la route existante `/adaptation/stability`, **When** elle est appelée, **Then** elle expose les urgences par notion (même contrat enrichi, pas de nouvelle route obligatoire).

---

### User Story 3 - Prompts pédagogiques éditables sans coder (Priority: P2)

En tant qu'enseignant francophone, j'ajuste le ton, les règles d'indices et les consignes du tuteur en éditant un fichier Markdown versionné (avec repli intégré si illisible), structuré par un gabarit de contexte (leçon, soumission, preuves, hint_level).

**Why this priority**: Franciser finement la pédagogie BEPC→université exige aujourd'hui un développeur (`prompts.py` en dur). Conditionne aussi les goldens (I7) qui testent le rendu de ces prompts.

**Independent Test**: Modifier le `.md`, recharger, vérifier que le ton change et qu'un fichier illisible retombe sur le prompt intégré sans crash — sans les autres stories.

**Acceptance Scenarios**:

1. **Given** un fichier prompt absent ou illisible, **When** le moteur démarre, **Then** le prompt intégré de secours est utilisé et l'incident est journalisé.
2. **Given** un gabarit avec `hint_level` et `recurring_mistakes`, **When** une évaluation est construite, **Then** ces champs alimentent le prompt (pas de régression sur les champs existants).

---

### User Story 4 - Goldens qui verrouillent la qualité (Priority: P2)

En tant que mainteneur, chaque changement de modèle local est validé par des soumissions d'élèves figées avec feedback attendu (`must_include`/`must_not_include`, hint-first), en asserts déterministes hors-ligne — jamais de solution complète balancée ni de test inventé.

**Why this priority**: Sans cela, chaque nouveau modèle ≤8 Go risque une régression pédagogique silencieuse invisible aux tests unitaires. Se construit après/avec I6 (prompts externalisés testés).

**Independent Test**: Ajouter une soumission golden qui exige l'absence de solution complète, changer de modèle mocké, vérifier que le test échoue si le feedback donne la solution — sans les autres stories.

**Acceptance Scenarios**:

1. **Given** un golden exigeant « ne doit PAS donner la solution », **When** le feedback la contient, **Then** le test échoue avec le fragment fautif cité.
2. **Given** les goldens existants, **When** les prompts I6 changent, **Then** la suite `tests/pedagogy/` (nom distinct du harness d'exécution) reste verte ou signale exactement la régression.

---

### Edge Cases

- Que se passe-t-il avec deux leçons en double (même id) ? Warning + première gagnante, jamais de doublon silencieux.
- Comment gérer une prévision d'oubli sans historique (jamais révisé) ? Urgence calculée sur la stabilité initiale, marquée « non calibrée », sans bloquer la file.
- Que se passe-t-il si le dossier de prompts est vide ? Repli intégré total, fonctionnement nominal.
- Comment éviter que les goldens ne deviennent flaky ? Asserts par sous-chaînes sur sorties mockées déterministes, aucun LLM-juge en CI.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système MUST charger les leçons depuis des fichiers JSON stricts (schéma `id/prérequis/concepts/exercice visible+hidden/mastery`, validés en stdlib) avec log-and-skip des invalides et warning des doublons.
- **FR-002**: Le système MUST unifier les pièges classiques avec la taxonomie du diagnostic 5-catégories existant (pas deux taxonomies parallèles).
- **FR-003**: Le système MUST exiger `explain_concept` pour la maîtrise complète (résoudre seul = partiel).
- **FR-004**: Le système MUST exposer par notion récupérabilité, jours-avant-seuil, urgence triés overdue-first — seuils fixes : chute passée = overdue, chute dans ≤3 j = urgent, ≤7 j = warning, sinon ok — en réutilisant strictement la formule FSRS existante.
- **FR-005**: Le système MUST lire le prompt système depuis un fichier Markdown éditable avec repli intégré si absent/illisible (jamais de crash au boot).
- **FR-006**: Le système MUST alimenter le prompt d'évaluation avec le gabarit (`hint_level`, `recurring_mistakes`, `explain_concept`) sans régression des champs existants.
- **FR-007**: Le système MUST valider chaque changement via des goldens (`must_include`/`must_not_include`, hint-first) en asserts déterministes hors-ligne sous `tests/pedagogy/`.
- **FR-008**: Le système MUST refuser un feedback contenant la solution complète quand le golden l'interdit, en citant le fragment fautif.

### Key Entities

- **Leçon fichier**: `id/title/prérequis/concepts/exercice{prompt, starter, visible, hidden}/mastery{pass_hidden + explain_concept}` + pièges classiques rédigés.
- **Prévision d'oubli**: Par notion — récupérabilité, stabilité, jours-avant-seuil, date prédite, urgence, tri overdue-first.
- **Prompt pack**: Fichier Markdown canonique + gabarit de contexte + FALLBACK intégré.
- **Golden pédagogique**: Soumission figée + feedback attendu (`must_include`/`must_not_include`) + rubrique hint-first.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un fichier leçon invalide est ignoré avec log en moins d'1 seconde sans impacter les leçons valides (10 fichiers dont 3 invalides).
- **SC-002**: Avec 6 notions de stabilités étalées, l'ordre overdue-first et les 4 niveaux d'urgence sont exacts à 100 % sur 20 tirages.
- **SC-003**: Un prompt Markdown modifié change le ton du tuteur au redémarrage suivant ; fichier supprimé → repli intégré sans erreur.
- **SC-004**: Un feedback donnant la solution complète fait échouer le golden correspondant avec le fragment cité, à 100 % sur 10 cas.
- **SC-005**: Aucune nouvelle dépendance d'exécution ; suite complète verte.

## Assumptions

- Feature 012 livrée (rappels, examens, notes, planning, XP, lisibilité) et suite verte comme socle.
- Taxonomie 5-catégories existante réutilisée telle quelle pour les pièges.
- Formule FSRS existante (`fsrs.py`) réutilisée sans second moteur.
- Contenus FR des leçons créés à part (les exercices EN Python-only ne sont pas importés).
- `tests/pedagogy/` distinct du harness d'exécution (nommage anti-collision acté en gate).
