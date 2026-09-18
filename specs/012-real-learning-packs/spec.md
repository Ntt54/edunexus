# Feature Specification: Real Learning Packs (Secondaire → Université)

**Feature Branch**: `012-real-learning-packs`

**Created**: 2026-09-18

**Status**: Draft

**Input**: User description: "analyse mon projet et dit moi ce qu'il manque reelement pour que une personne puisse reelement apprendre et aussi les ajout que l'on peut faire pour que meme une personne du secondaire puisse apprendre avec le logiciel" — un seul spec couvrant les 4 packs : (1) mémorisation active, (2) scolaire secondaire, (3) université, (4) motivation + accessibilité.

## Clarifications

### Session 2026-09-18

- Q: Faut-il livrer les 4 packs en une seule fois ou par phases, et quel périmètre pour la v1 ? → A: Tout en une fois — un seul chantier d'implémentation couvrant les 4 packs.
- Q: La vue d'encadrement de la v1 couvre qui ? → A: Parents seuls — suivi temps/maîtrise/erreurs/jalons pour les parents uniquement ; l'assignation par les professeurs est différée.
- Q: Quelles épreuves officielles le simulateur couvre-t-il en v1 ? → A: Système camerounais francophone — brevet (BEPC), probatoire (classe de 1ère) et baccalauréat.
- Q: Quel niveau d'accessibilité le mode lisibilité doit-il garantir en v1 ? → A: WCAG 2.2 AA.
- Q: D'où vient le référentiel des programmes camerounais ? → A: Hybride — squelette officiel (classes × matières × chapitres) embarqué dans l'app, compléments (annales, détails) importés par l'utilisateur.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mémorisation active qui fait réviser (Priority: P1)

En tant qu'apprenant, je suis rappelé de mes révisions dues, je m'évalue par rappel rédigé (pas seulement QCM) avec correction immédiate, et mes séances mélangent les types d'exercices pour que la mémoire tienne à l'examen, pas seulement en séance.

**Why this priority**: Rappel espacé + effet test = effets les plus larges et répliqués en sciences de l'apprentissage (Dunlosky 2013, Donoghue & Hattie 2021). Le moteur de répétition existe déjà mais ne déclenche rien : sans rappels ni rappel-rédigé, l'apprenant ne révise pas. Socle commun secondaire + université.

**Independent Test**: Créer des cartes sur un chapitre, constater une notification/rappel à l'échéance, répondre par écrit à 5 questions avec feedback immédiat, faire une séance mixant 3 types d'exercices — vérifiable sans les autres stories.

**Acceptance Scenarios**:

1. **Given** des révisions dues aujourd'hui, **When** j'ouvre l'application, **Then** je vois d'abord ce qui est dû (cartes, notions fragiles) avec un rappel explicite si j'ai manqué hier.
2. **Given** un quiz sur une notion, **When** je réponds par écrit (réponse rédigée/cachée), **Then** j'obtiens une correction immédiate disant quoi retravailler, pas seulement juste/faux.
3. **Given** une séance d'exercices, **When** elle contient 3 types de problèmes mélangés, **Then** chaque type apparaît entremêlé (jamais un bloc unique) et mes erreurs d'un type refont surface en fin de séance.
4. **Given** une notion rappelée correctement, **When** je la revois, **Then** l'intervalle s'allonge ; en cas d'oubli, il se resserre — sans que j'aie à gérer le calendrier.

---

### User Story 2 - Réussir le BEPC, le probatoire et le bac (Priority: P1)

En tant que collégien/lycéen (ou parent), j'étudie un programme aligné sur ma classe, je m'entraîne sur des annales taguées par compétence, je passe des épreuves blanches en conditions réelles (durée, barème /20), et mon parent suit ma maîtrise réelle — pas mon score de jeu.

**Why this priority**: Promesse centrale pour le secondaire : les examens camerounais sont strictement bornés aux programmes. Sans alignement officiel + simulation d'épreuve, l'app fait réviser « à côté ». Même priorité que US1 : c'est ce qui rend l'app utilisable par un secondaire.

**Independent Test**: Choisir « 3e — Mathématiques », voir uniquement le programme de 3e, lancer un brevet blanc chronométré avec copie notée sur 20, consulter la vue parent (temps, % maîtrise par matière, erreurs fréquentes) — sans les autres stories.

**Acceptance Scenarios**:

1. **Given** matière « Mathématiques » niveau « 3e », **When** je parcours les chapitres, **Then** seuls les chapitres au programme officiel apparaissent (contenu hors-programme signalé ou masqué par défaut).
2. **Given** un brevet blanc démarré, **When** le temps imparti s'écoule, **Then** la copie se verrouille, est corrigée avec barème sur 20 (pénalités d'orthographe comme au vrai barème le cas échéant), et indique les compétences ratées.
3. **Given** une erreur sur un exercice d'annales, **When** je termine la séance, **Then** un exercice de même compétence m'est proposé avec un correctif ciblé (pas une simple répétition).
4. **Given** un compte parent lié, **When** le parent ouvre le suivi hebdo, **Then** il voit temps d'étude, % de maîtrise par matière, erreurs fréquentes et prochain jalon (brevet/bac) — jamais seulement le score de jeu.

---

### User Story 3 - Travailler au niveau universitaire (Priority: P2)

En tant qu'étudiant, j'obtiens des réponses avec citations traçables vers mes sources exactes, je transforme mes cours en notes atomiques reliées pour préparer dissertations et dossiers, et je planifie mon semestre (charge par UE, partiels) sans bachotage de dernière minute.

**Why this priority**: L'université exige traçabilité des affirmations, structuration d'idées complexes et gestion d'une charge massive (30 ECTS ≈ 750-900 h). Sans cela l'app reste un outil de révision scolaire, pas un instrument d'études supérieures.

**Independent Test**: Poser une question sur un PDF de cours et vérifier chaque affirmation citée vers le paragraphe exact ; créer 5 notes atomiques liées et assembler un plan ; générer un planning de semestre depuis une charge par UE — sans les autres stories.

**Acceptance Scenarios**:

1. **Given** une question sur mes documents, **When** je reçois la réponse, **Then** chaque affirmation importante porte une citation vers le paragraphe/chapitre exact, vérifiable en un clic.
2. **Given** un chapitre étudié, **When** je crée des notes, **Then** chaque note exprime une idée en mes propres mots, liée à d'autres notes (précise/contredit/mécanisme-de), et je peux assembler un plan de dissertation depuis ces liens.
3. **Given** un semestre avec 5 UE et leurs dates de partiels, **When** je génère mon planning, **Then** la charge hebdomadaire par UE est répartie jusqu'aux partiels, avec créneaux de révision espacée et séances de simulation — sans semaine à plus de 150 % de la moyenne.

---

### User Story 4 - Motivation saine et accès pour tous (Priority: P3)

En tant que jeune apprenant (ou apprenant dyslexique), j'étudie par sessions courtes avec séries et récompenses proportionnées à la difficulté réelle, et je peux lire/écouter confortablement (police adaptée, fond doux, voix naturelle, surlignage) sans que la récompense ne prime sur l'apprentissage.

**Why this priority**: Sans motivation, l'app n'est pas ouverte ; sans accessibilité, 10-15 % des collégiens (dys/ADHD, PAP/PPS) sont exclus. P3 car cela amplifie les stories 1-3 mais ne les remplace pas — et une gamification mal conçue trivialise l'apprentissage (gardes anti-grinding requis).

**Independent Test**: Faire 3 sessions courtes, constater série/XP/coffres proportionnés à la difficulté, activer le mode lisibilité (police + fond + voix + surlignage mot-à-mot) — sans les autres stories.

**Acceptance Scenarios**:

1. **Given** 3 sessions de 5-15 minutes sur une semaine, **When** je réussis des exercices difficiles, **Then** mes récompenses (XP, série) reflètent la difficulté réelle — refaire en boucle des exercices faciles ne fait pas progresser la série ni les ligues.
2. **Given** le mode lisibilité activé, **When** je lis une leçon, **Then** police adaptée, fond doux non blanc pur, taille/interlignage augmentés, lecture vocale naturelle à vitesse réglable avec surlignage mot-à-mot.
3. **Given** un apprenant week-end uniquement, **When** il choisit l'option série hebdomadaire, **Then** sa régularité est reconnue sans pénaliser l'absence en semaine.

---

### Edge Cases

- Que se passe-t-il quand l'apprenant ignore les rappels 7 jours d'affilée ? Le plan se recompacte (priorité aux notions fragiles, charge plafonnée) au lieu d'empiler une dette impossible.
- Comment gérer un apprenant sans aucun document importé (secondaire sans manuel) ? Le programme officiel de la classe fournit le squelette (chapitres, compétences) et l'app enseigne depuis ses propres connaissances en le signalant.
- Que se passe-t-il si deux niveaux se chevauchent (ex. 3e vs 2nde, tronc commun vs spécialité) ? Le contenu est tagué par niveau exact ; le changement de niveau ne mélange jamais les listes.
- Comment le simulateur gère une coupure en pleine épreuve blanche ? La copie est récupérée avec le temps restant recalculé, marquée « interrompue », et peut être reprise ou rendue telle quelle.
- Que se passe-t-il si le parent et l'apprenant se contredisent (parent veut voir, ado refuse) ? Le partage du suivi est explicite et révocable par l'apprenant ; sans consentement, seule une vue agrégée anonymisée est visible.
- Comment éviter la récompense du bachotage (100 exercices faciles la veille) ? Les récompenses sont plafonnées par difficulté et décroissent en cas de répétition du même item facile.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système MUST rappeler les révisions dues (cartes, notions fragiles) à l'ouverture et après une absence, avec replanification plafonnée en cas de retard accumulé.
- **FR-002**: Le système MUST proposer des évaluations par rappel rédigé (réponse écrite/cachée) avec correction immédiate indiquant quoi retravailler, pas seulement juste/faux.
- **FR-003**: Le système MUST entremêler les types d'exercices au sein d'une séance et refaire surface en fin de séance aux items ratés.
- **FR-004**: Le système MUST ajuster les intervalles de révision selon la réussite (allongement) et l'oubli (resserrement), sans gestion manuelle du calendrier.
- **FR-005**: Le système MUST aligner les contenus scolaires sur les programmes officiels camerounais par classe et matière (6e→Terminale, probatoire en classe de 1ère), avec marquage ou masquage par défaut du hors-programme.
- **FR-006**: Le système MUST simuler les épreuves officielles (BEPC, probatoire, baccalauréat) en conditions réelles (durée imposée, verrouillage à la fin du temps, barème sur 20, reprise après coupure marquée « interrompue »).
- **FR-007**: Le système MUST proposer après chaque erreur d'annales un exercice de même compétence avec correctif ciblé.
- **FR-008**: Le système MUST fournir une vue parent (temps d'étude, % de maîtrise par matière, erreurs fréquentes, prochain jalon) basée sur la maîtrise réelle, avec partage explicite et révocable. L'assignation de chapitres et seuils par les professeurs est hors périmètre v1.
- **FR-009**: Le système MUST citer les sources exactes (paragraphe/chapitre vérifiable en un clic) pour les réponses (niveau réponse : sources attachées à la réponse entière, cliquables vers le paragraphe exact ; l'ancrage par phrase est hors périmètre v1).
- **FR-010**: Le système MUST permettre des notes atomiques (une idée, titre non vide ; contrôle de présence uniquement, sans détecteur de copier-coller) reliées entre elles (précise/contredit/mécanisme-de) et l'assemblage d'un plan depuis ces liens.
- **FR-011**: Le système MUST générer un planning de semestre depuis la charge par UE et les dates d'épreuves, répartissant révision espacée et simulations sans surcharge (>150 % de la moyenne hebdomadaire).
- **FR-012**: Le système MUST récompenser proportionnellement à la difficulté réelle (plafond anti-grinding, décroissance sur répétition facile) avec séries quotidiennes et option hebdomadaire.
- **FR-013**: Le système MUST offrir un mode lisibilité (police adaptée, fond doux, taille/interlignage, voix naturelle à vitesse réglable, surlignage mot-à-mot).
- **FR-014**: Le système MUST taguer chaque item d'évaluation par niveau cognitif (connaissance/compréhension/application/analyse/évaluation/création) et couvrir les niveaux exigés par l'examen visé.

### Key Entities

- **Rappel de révision**: Échéance issue de l'historique (cartes dues, notions fragiles) avec statut (à faire/en retard) et règle de replanification plafonnée.
- **Item d'évaluation**: Question/exercice avec type de rappel (QCM/reconnaissance vs réponse rédigée), compétence, niveau cognitif, difficulté, historique de réussite.
- **Programme scolaire**: Référentiel hybride — squelette officiel (classe × matière × chapitre, embarqué et versionné avec l'app) + compléments importés par l'utilisateur (annales, détails) ; le contenu hors-programme est marqué.
- **Épreuve blanche**: Copie chronométrée (durée officielle, verrouillage, reprise « interrompue ») corrigée sur 20 avec détail par compétence.
- **Note atomique**: Une idée en propres mots, reliée à d'autres notes, source traçable ; un ensemble de notes produit un plan.
- **Plan de semestre**: Répartition hebdomadaire de charge par UE jusqu'aux épreuves, avec créneaux de révision et simulations.
- **Suivi parent**: Vue agrégée (temps, maîtrise, erreurs, jalons) avec consentement explicite et révocable ; sans consentement, seule une vue agrégée anonymisée.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un apprenant revenant après 3 jours d'absence voit ses révisions dues en moins de 10 secondes à l'ouverture, avec un plan de rattrapage plafonné.
- **SC-002**: 90 % des quiz incluent au moins une question à rappel rédigé avec feedback correctif (mesuré sur 20 quiz générés couvrant 5 matières).
- **SC-003**: Un élève de 3e génère un brevet blanc de mathématiques complet (durée officielle, barème /20) en moins de 2 minutes de préparation.
- **SC-004**: Après 10 erreurs sur annales, 100 % déclenchent un exercice de même compétence avec correctif ciblé lors de la séance suivante.
- **SC-005**: Chaque réponse générée depuis des documents contient au moins une citation vérifiable en un clic pour 95 % des affirmations factuelles (échantillon de 20 réponses).
- **SC-006**: Un planning de semestre à 5 UE se génère en moins d'une minute sans semaine dépassant 150 % de la charge moyenne.
- **SC-007**: Un apprenant refaisant 20 fois le même exercice facile ne gagne pas plus de 10 % du XP d'une séance normale de même durée.
- **SC-008**: Le mode lisibilité est conforme WCAG 2.2 AA (contraste, navigation clavier, alternatives texte) et la lecture vocale suit le texte mot à mot sans décalage perceptible.

## Assumptions

- Système scolaire camerounais francophone (programmes officiels, BEPC, probatoire en classe de 1ère, baccalauréat) comme périmètre v1 ; autres systèmes hors périmètre.
- L'application reste 100 % locale et hors-ligne d'abord : les rappels sont in-app d'abord (pas de notifications push externes en v1).
- Le moteur de répétition espacée et les parcours existants sont réutilisés (évolution, pas reconstruction).
- Aucune nouvelle dépendance d'exécution lourde ; la voix de synthèse reste celle du navigateur en v1.
- La vue parent suppose un consentement explicite et révocable de l'apprenant.
- Les contenus sans document importé s'appuient sur les connaissances propres du modèle, signalées comme telles.
- Livraison en un seul chantier couvrant les 4 packs (clarification 2026-09-18) : le plan et les tâches organisent le travail, sans découpage en versions.
