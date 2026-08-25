# Feature Specification: Plateforme multi-vues & bibliothèque de connaissances

**Feature Branch**: `005-platform-ui-library`

**Created**: 2026-08-25

**Status**: Draft

**Input**: Faire évoluer l'interface EduNexus d'une page unique dense vers une
plateforme de tutorat multi-espaces (navigation claire, conversations
persistantes, tableau de bord), et transformer la liste de PDF en une
bibliothèque de connaissances hiérarchique avec groupes de sources et
sélection active par conversation — sans reconstruire l'existant ni perdre
une fonctionnalité.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Conversations persistantes indépendantes (Priority: P1)

En tant qu'apprenant, je crée une conversation nommée avec le tuteur, j'y pose
mes questions, puis je la quitte et y reviens plus tard : tout l'historique est
restauré. Créer une nouvelle conversation n'efface jamais les précédentes ; je
peux basculer de l'une à l'autre, les renommer et les retrouver dans une liste.

**Why this priority**: c'est le cœur quotidien de l'usage (discuter avec le
tuteur) et le plus gros manque actuel : aujourd'hui une nouvelle session
remplace la précédente.

**Independent Test**: créer deux conversations avec des questions différentes,
basculer entre elles, fermer et rouvrir l'application → chaque conversation
restitue exactement son propre historique et son titre.

**Acceptance Scenarios**:

1. **Given** une conversation active contenant un échange, **When** je crée
   une « nouvelle conversation », **Then** une conversation vierge s'ouvre et
   l'ancienne reste intacte et accessible dans la liste.
2. **Given** au moins deux conversations existantes, **When** je sélectionne
   une autre conversation dans la liste, **Then** son historique complet
   s'affiche et la suite de la discussion y est ajoutée.
3. **Given** une conversation nommée « Sans titre », **When** je la renomme
   « Apprendre Java », **Then** le nouveau nom apparaît partout (liste,
   en-tête) et persiste après rechargement.
4. **Given** une conversation liée à un espace d'apprentissage, **When**
   je la rouvre, **Then** son espace et ses sources actives associées sont
   restaurés tels quels.

---

### User Story 2 - Bibliothèque hiérarchique de connaissances (Priority: P1)

En tant qu'apprenant, je consulte ma bibliothèque comme une arborescence
« Domaine → Catégorie → Documents » (ex. Programmation → Java → 3 livres),
avec recherche, comptage des documents et état d'indexation visible. Je peux
ajouter un document, déplacer un livre vers une catégorie, créer/renommer/
supprimer catégories et domaines.

**Why this priority**: passe d'une liste plate de PDF à un gestionnaire de
connaissances exploitable dès quelques dizaines de documents ; conditionne les
stories de sélection.

**Independent Test**: importer 3 PDF, les ranger dans deux catégories
distinctes d'un même domaine → l'arborescence reflète la hiérarchie, la
recherche retrouve un document par son titre, les compteurs sont exacts.

**Acceptance Scenarios**:

1. **Given** une bibliothèque vide, **When** j'ajoute un premier document
   dans un nouveau domaine/catégorie, **Then** la hiérarchie se crée
   automatiquement et le document apparaît avec son statut d'indexation.
2. **Given** plusieurs documents répartis en catégories, **When** je tape
   trois lettres dans la recherche, **Then** seuls les documents
   correspondants restent visibles, leur chemin hiérarchique étant indiqué.
3. **Given** un document déjà classé, **When** je le déplace vers une autre
   catégorie, **Then** il disparaît de l'ancienne, apparaît dans la nouvelle,
   et son contenu/indexation est conservé (aucune ré-indexation forcée).
4. **Given** une catégorie contenant des documents, **When** je tente de la
   supprimer, **Then** le système demande confirmation et propose de
   déplacer ou conserver les documents (jamais de suppression silencieuse).

---

### User Story 3 - Sélection de sources par groupes ou individuellement (Priority: P2)

En tant qu'apprenant, je coche une catégorie entière (« ☑ Java ») pour inclure
tous ses documents au contexte, ou je coche uniquement certains livres. La
sélection fonctionne à tous les niveaux (domaine, catégorie, document) et un
document présent dans plusieurs catégories n'est compté qu'une fois.

**Why this priority**: rend le RAG praticable avec une grande bibliothèque ;
dépend de la hiérarchie (US2) mais pas des conversations.

**Independent Test**: cocher une catégorie de 4 livres → l'état « disponibles
pour le tuteur » passe de N à N+4 documents ; décocher un livre → N+3, sans
toucher aux autres.

**Acceptance Scenarios**:

1. **Given** une catégorie contenant 4 documents, **When** je coche la
   catégorie, **Then** ses 4 documents passent cochés et le compteur global
   augmente de 4.
2. **Given** une catégorie cochée dont un document est décoché
   individuellement, **When** je consulte l'état, **Then** la catégorie
   affiche un état partiel (indéterminé) distinct de « tout coché ».
3. **Given** un document appartenant à deux catégories, **When** il est
   coché via l'une, **Then** il apparaît coché également via l'autre (un seul
   état réel par document).

---

### User Story 4 - Sources actives propres à chaque conversation (Priority: P2)

En tant qu'apprenant, chaque conversation possède son propre ensemble de
sources actives, distinct de la bibliothèque. Depuis la conversation, un
bouton « Sources : … » ouvre un sélecteur (recherche + arborescence +
Annuler/Appliquer) qui montre immédiatement ce que le tuteur utilise. Modifier
les sources actives ne modifie jamais la bibliothèque.

**Why this priority**: sépare « tout ce que je possède » de « ce qui sert à
cette réponse » — le gain de clarté central de la refonte.

**Independent Test**: dans une conversation, appliquer comme source active
une seule catégorie → poser une question portant sur un document hors
sélection → le tuteur répond sans citer ce document ; la bibliothèque reste
inchangée.

**Acceptance Scenarios**:

1. **Given** une conversation sans sources actives, **When** je pose une
   question, **Then** le tuteur répond sans contexte documentaire et un
   indicateur « Aucune source active » est visible.
2. **Given** le sélecteur de sources ouvert, **When** j'applique une
   sélection, **Then** le bouton résumé se met à jour (ex. « Java · 4
   documents ») et seules ces sources alimentent les réponses suivantes.
3. **Given** des sources actives définies, **When** je supprime une catégorie
   entière de la bibliothèque, **Then** les documents concernés sortent des
   sources actives de façon cohérente, sans casser la conversation.

---

### User Story 5 - Navigation principale multi-espaces (Priority: P2)

En tant qu'apprenant, je navigue via une barre de navigation unique entre des
espaces dédiés : Accueil, Conversations, Bibliothèque, Apprentissage (notions),
Entraîner (exercices), Quiz/Examens, Progression, Explorer. Chaque espace
affiche un seul domaine fonctionnel à la fois ; aucune fonctionnalité actuelle
ne disparaît, elle change seulement d'emplacement.

**Why this priority**: résout la surcharge de l'écran unique ; nécessite que
les stories 1–4 existent pour avoir quelque chose à placer dans les espaces.

**Independent Test**: parcourir les 8 espaces → chaque fonctionnalité
actuelle reste accessible exactement une fois, l'espace actif est toujours
visible, et le retour à l'espace précédent restaure son état.

**Acceptance Scenarios**:

1. **Given** l'application ouverte, **When** je clique sur chaque entrée de
   navigation, **Then** un seul espace s'affiche à la fois et l'entrée active
   est mise en évidence.
2. **Given** un exercice en cours dans « Entraîner », **When** je vais dans
   « Bibliothèque » puis reviens dans « Entraîner », **Then** l'exercice est
   dans l'état où je l'avais laissé.
3. **Given** l'application sur écran étroit, **When** la largeur diminue,
   **Then** la navigation devient compacte (menu/onglets) sans perte
   d'accès aux espaces.

---

### User Story 6 - Tableau de bord synthétique (Priority: P3)

En tant qu'apprenant, la vue Accueil me donne en un coup d'œil : notions à
réviser aujourd'hui, notion en cours et progression, conversations récentes,
dernières sources ajoutées, prochaine évaluation — chacune cliquable vers
l'espace correspondant.

**Why this priority**: valeur forte mais non bloquante ; agrège des données
produites par les autres stories.

**Independent Test**: avec des données d'exemple (notions dues, 2
conversations, 1 source récente), le tableau de bord affiche chaque catégorie
d'information avec un lien fonctionnel vers l'espace concerné.

**Acceptance Scenarios**:

1. **Given** des révisions dues, **When** j'ouvre l'Accueil, **Then** une
   carte « À réviser » affiche le nombre et mène à la vue de révision.
2. **Given** aucune donnée (première utilisation), **When** j'ouvre
   l'Accueil, **Then** des cartes vides invitent à importer une source et
   démarrer une conversation (pas de zones cassées).

---

### User Story 7 - Classification proposée à l'import (Priority: P3)

En tant qu'apprenant, quand j'importe « Fondamentaux Java.pdf », le système
me propose un rangement (domaine, catégorie) déduit du titre et du contexte ;
je peux accepter en un clic ou modifier avant validation. Le classement
automatique par lots existant reste disponible pour toute la bibliothèque.

**Why this priority**: confort significatif à grande échelle, mais la saisie
manuelle reste possible en attendant.

**Independent Test**: importer un document au titre explicite → une
proposition de catégorie cohérente est affichée et acceptée en un clic ;
modifier la proposition fonctionne aussi.

**Acceptance Scenarios**:

1. **Given** un import dont le titre évoque une catégorie existante, **When**
   l'indexation démarre, **Then** une suggestion de rangement est proposée et
   applicable sans quitter la vue d'import.
2. **Given** une proposition erronée, **When** je choisis une autre
   catégorie, **Then** seul mon choix est appliqué.

---

### User Story 8 - Préparation des parcours d'apprentissage (Priority: P3)

En tant qu'apprenant, l'application réserve la place d'un futur espace
« Parcours » (Domaine → étapes ordonnées → ressources → évaluation) : la
navigation prévoit l'entrée, les entités de bibliothèque peuvent être
rattachées à un domaine, mais aucun comportement de parcours n'est implémenté
dans cette version.

**Why this priority**: garantie d'évolutivité demandée explicitement, sans
investissement immédiat.

**Independent Test**: inspecter la navigation et le modèle conceptuel →
l'entrée « Parcours » existe (état « à venir ») et aucune décision de
conception n'empêche d'y rattacher catégories et documents ultérieurement.

**Acceptance Scenarios**:

1. **Given** la navigation principale, **When** je consulte la liste des
   espaces, **Then** « Parcours » apparaît comme emplacement réservé non
   fonctionnel, clairement signalé.
2. **Given** la documentation du modèle de données, **When** un futur parcours
   rattache des documents à des étapes, **Then** aucune migration destructive
   des entités existantes n'est nécessaire.

---

### Edge Cases

- Que se passe-t-il lorsqu'un document appartient à plusieurs catégories dont
  une seule est cochée ? Il est considéré comme actif (un seul état réel par
  document, les catégories reflètent un état partiel).
- Que se passe-t-il lors de la suppression d'une catégorie utilisée comme
  source active ? Les documents en sortent proprement des sources actives ;
  la conversation reste fonctionnelle (mode sans contexte si vide).
- Comment le système réagit-il à deux imports simultanés du même fichier ?
  Un seul document est créé (déduplication existante conservée).
- Que se passe-t-il lorsque la recherche ne retourne aucun résultat ?
  Un état vide explicite avec suggestion d'élargir la recherche.
- Comment gérer une bibliothèque de plusieurs milliers de documents ?
  L'arborescence est dépliée niveau par niveau et la recherche passe avant
  le parcours manuel ; aucun chargement de la liste exhaustive brute.
- Que se passe-t-il si je renomme une catégorie pendant qu'elle sert de
  source active ? Les sources actives suivent le renommage (elles référencent
  la catégorie, pas son nom).
- Que se passe-t-il lorsqu'une conversation référence un espace supprimé ?
  La conversation reste ouvable, marquée « espace supprimé », sources actives
  vidées.

## Requirements *(mandatory)*

### Functional Requirements

**Navigation & espaces**

- **FR-001**: L'application MUST organiser les fonctionnalités en espaces
  accessibles depuis une navigation principale unique et persistante :
  Accueil, Conversations, Bibliothèque, Apprentissage, Entraîner,
  Quiz/Examens, Progression, Explorer.
- **FR-002**: Un seul espace fonctionnel MUST être affiché à la fois ;
  l'entrée active de navigation MUST rester visible en permanence.
- **FR-003**: Basculer d'un espace à l'autre MUST préserver l'état non
  terminé de chaque espace (exercice en cours, filtres actifs, position de
  lecture).
- **FR-004**: Toutes les fonctionnalités existantes (import, indexation,
  recherche, exercices, quiz, examens, révisions, glossaire, comparaison,
  carte des connaissances, dictée vocale, réglages pédagogiques) MUST rester
  accessibles, chacune depuis exactement un espace dédié.

**Conversations**

- **FR-005**: L'utilisateur MUST pouvoir créer, lister, ouvrir, renommer et
  supprimer des conversations nommées.
- **FR-006**: Chaque conversation MUST conserver intégralement son historique
  de messages, y compris après fermeture de l'application.
- **FR-007**: Créer une nouvelle conversation NE DOIT PAS altérer les
  conversations existantes.
- **FR-008**: Une conversation MUST pouvoir être associée à un espace
  d'apprentissage ; cet espace est restauré à la réouverture.
- **FR-009**: Chaque conversation MUST posséder son propre ensemble de
  sources actives, indépendant des autres conversations et de la
  bibliothèque.

**Bibliothèque**

- **FR-010**: La bibliothèque MUST présenter les documents selon une
  hiérarchie Domaine → Catégorie → Documents, construite sur les espaces et
  catégories existants.
- **FR-011**: L'utilisateur MUST pouvoir créer, renommer et supprimer
  domaines et catégories ; la suppression d'un conteneur non vide MUST
  demander confirmation et proposer le devenir des documents.
- **FR-012**: L'utilisateur MUST pouvoir déplacer un document d'une catégorie
  vers une autre sans ré-indexation ni perte de données.
- **FR-013**: La bibliothèque MUST offrir une recherche par titre retournant
  les résultats avec leur chemin hiérarchique.
- **FR-014**: Chaque nœud de la hiérarchie MUST afficher le nombre de
  documents qu'il contient (directement et/ou au total).
- **FR-015**: Chaque document MUST afficher son état d'indexation
  (en cours / prêt / erreur) mis à jour en temps réel.

**Sélection & sources actives**

- **FR-016**: La sélection MUST fonctionner à chaque niveau (domaine,
  catégorie, document) avec états tri-états pour les conteneurs
  (tout coché / partiel / vide).
- **FR-017**: Un document appartenant à plusieurs catégories MUST posséder un
  unique état de sélection, reflété dans toutes ses catégories parentes.
- **FR-018**: Chaque conversation MUST appliquer au RAG uniquement ses
  sources actives ; une conversation sans source active MUST rester
  utilisable (réponse sans contexte documentaire, indicateur visible).
- **FR-019**: La conversation MUST offrir un sélecteur de sources (recherche,
  arborescence, Annuler/Appliquer) avec bouton résumé visible en permanence
  (nom + nombre de documents actifs).
- **FR-020**: La modification des sources actives NE DOIT NI déplacer ni
  supprimer ni ré-indexer les documents de la bibliothèque.

**Classification & import**

- **FR-021**: L'import MUST permettre de choisir le domaine/catégorie de
  destination, avec proposition de classement automatique déduite du titre,
  acceptée ou modifiable en un clic.
- **FR-022**: La classification automatique par lots de toute la bibliothèque
  MUST rester disponible et ses résultats rester modifiables manuellement.

**Tableau de bord & évolutivité**

- **FR-023**: L'accueil MUST présenter synthétiquement : révisions dues,
  notion en cours et progression, conversations récentes, dernières sources,
  prochaine évaluation — chaque élément menant à son espace.
- **FR-024**: La navigation MUST réserver un emplacement « Parcours »
  (non fonctionnel, signalé « à venir ») et le modèle de données MUST
  permettre de rattacher ultérieurement catégories et documents à des étapes
  de parcours sans migration destructive.

### Key Entities *(include if feature involves data)*

- **Espace d'apprentissage** (existant) : domaine thématique de l'apprenant
  (ex. Réseaux) ; racine de la hiérarchie de bibliothèque et rattachement des
  conversations.
- **Catégorie / Groupe** (existant) : regroupement de documents au sein d'un
  espace ; sert à la fois de dossier hiérarchique et de groupe sélectionnable
  en bloc ; un document peut appartenir à plusieurs catégories.
- **Document / Livre** (existant) : source indexée (titre, état
  d'indexation, contenu découpé) ; appartient à un ou plusieurs groupes ;
  état de sélection unique.
- **Conversation** (nouveau) : échange nommé avec le tuteur ; attributs :
  titre, espace associé, horodatages, historique complet, ensemble de sources
  actives ; indépendante des autres conversations.
- **Sources actives** (nouveau, par conversation) : sous-ensemble courant de
  la bibliothèque utilisé pour répondre ; référencé (jamais dupliqué) ;
  suivi automatiquement si un document ou une catégorie disparaît.
- **Notion** (existant) : unité d'apprentissage avec état de maîtrise ;
  affichée dans l'espace Apprentissage et agrégée dans le tableau de bord.
- **Parcours** (futur, hors périmètre) : suite ordonnée d'étapes rattachant
  notions et ressources d'un domaine ; emplacement réservé uniquement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un document précis est retrouvé et ouvert parmi 500 en moins
  de 15 secondes, via recherche ou navigation hiérarchique.
- **SC-002**: Inclure ou exclure un groupe entier de sources prend une seule
  action et moins de 5 secondes, quel que soit le nombre de documents du
  groupe.
- **SC-003**: 100 % des conversations réouvrent avec leur historique et leur
  titre intacts après fermeture complète de l'application.
- **SC-004**: Zéro fonctionnalité existante disparaît : la liste de contrôle
  des fonctionnalités actuelles est intégralement revérifiée après refonte.
- **SC-005**: À tout moment, l'écran principal n'expose qu'un seul espace
  fonctionnel ; les autres sont atteignables en une action de navigation.
- **SC-006**: Le tableau de bord se lit en moins de 30 secondes et chaque
  carte mène à l'espace correspondant en un clic.
- **SC-007**: Pour au moins 8 imports sur 10 à titre explicite, la
  proposition de classement automatique est jugée acceptable telle quelle ou
  corrigée en une interaction.
- **SC-008**: Une bibliothèque de 1 000 documents reste fluide : le parcours
  hiérarchique et la recherche ne chargent jamais une liste exhaustive brute.

## Assumptions

- La hiérarchie retenue comporte deux niveaux de regroupement (Domaine =
  espace d'apprentissage existant ; Catégorie = groupe existant) — les
  modèles actuels couvrent ce besoin sans refonte du stockage ; un
  approfondissement (sous-catégories) pourra venir plus tard.
- Les « conversations » font évoluer le système de sessions/tuteurs existant
  (historique déjà persisté) plutôt que d'introduire un mécanisme parallèle.
- Application mono-utilisateur locale : pas de gestion de droits ni de
  partage dans cette version.
- Le support mobile reste un empilement vertical avec navigation compacte
  (comportement actuel conservé et adapté).
- Les parcours d'apprentissage sont hors périmètre fonctionnel de cette
  version : seuls l'emplacement de navigation et l'absence de verrouillage
  architectural sont livrés.
- La performance visée (SC-008) repose sur le chargement progressif de
  l'arborescence et la recherche, sans précharger la liste complète.
