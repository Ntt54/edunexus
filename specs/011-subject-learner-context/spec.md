# Feature Specification: Subject & Learner Context Isolation

**Feature Branch**: `011-subject-learner-context`

**Created**: 2026-09-14

**Status**: Draft

**Input**: User description: "meme si je change la matiere sa ne change pas les donne pour allez sur cette nouvelle matiere [Image 1] ici ses python ses notez non classé (on ne peut toujour pas renommer ) et ici ses ma nouvelle matiere java [Image 2] sa a toujour les meme donne de python pour cela on devrais le faire sur [Image 3] pour gerer les element de l'apprenant avec creation et ajout de l'apprenant ."

## Clarifications

### Session 2026-09-14

- Q: L'isolation de la progression et des parcours doit-elle se faire par couple (matière × apprenant) ou seulement par matière ? (FR-006) → A: Par couple (matière × apprenant) — chaque apprenant a sa propre progression par matière
- Q: La matière par défaut "Non classé" (même renommée) doit-elle pouvoir être supprimée ? (Edge Cases / FR-002) → A: Oui, supprimable comme toute autre matière et renommable comme les autres, mais avec confirmation obligatoire
- Q: La liste des apprenants sur #/apprenants est-elle globale ou filtrée par matière ? (Domain & Data Model) → A: Filtrée par matière — chaque matière a sa propre liste d'apprenants, isolée
- Q: La bibliothèque "Mes sources" doit-elle aussi se filtrer par la matière active, comme Accueil et Mon parcours ? (Integration & UX) → A: Oui, filtrée par défaut par matière active avec toggle "Toutes" pour voir global

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Changer de matière filtre toutes les données (Priority: P1)

En tant qu'apprenant, je sélectionne une matière dans le sélecteur global (header) pour que tout le tableau de bord et mes parcours reflètent uniquement cette matière.

Capture Image 1 (Accueil, matière "Non classé" / Python) montre "Prochaine étape : Introduction à Python et premiers pas" et la pastille "Matière active : Non classé". Capture Image 2 (Mon parcours, matière "java" sélectionnée dans le header) montre encore "Parcours depuis livres — Non classé" et les mêmes étapes Python, alors que la matière active est "java". Le bug est que le changement de matière dans le header ne filtre pas les données affichées.

**Why this priority**: Sans isolation par matière, la fonctionnalité multi-matières est inutilisable : l'utilisateur crée "java" mais continue à voir le contenu Python, ce qui bloque toute progression séparée par discipline.

**Independent Test**: Créer deux matières (Non classé avec un parcours Python, java vide ou avec parcours Java). Sélectionner "Non classé" vérifier que Accueil/Mon parcours affichent le parcours Python ; sélectionner "java" vérifier que les mêmes vues affichent 0 parcours ou le parcours Java, sans trace des étapes Python.

**Acceptance Scenarios**:

1. **Given** deux matières "Non classé" (avec 1 parcours Python de 15 étapes) et "java" (vide), **When** je passe le sélecteur header de "Non classé" à "java", **Then** la page d'accueil affiche "Matière active : java", "Parcours : 0%" ou vide, et ne montre plus "Introduction à Python".
2. **Given** la matière "java" active, **When** j'ouvre "Mon parcours", **Then** la liste des parcours ne contient plus "Parcours depuis livres — Non classé" et la zone d'étapes ne liste pas les 15 étapes Python.
3. **Given** "java" active sans parcours, **When** je clique "Nouveau parcours", **Then** le parcours créé est rattaché à "java" et apparaît immédiatement dans la liste filtrée par "java".
4. **Given** changement de matière, **When** je recharge la page, **Then** la matière active persiste et les données restent filtrées (pas de retour silencieux à "Non classé").

---

### User Story 2 - Renommer la matière "Non classé" (Priority: P2)

En tant qu'utilisateur, je veux renommer la matière par défaut "Non classé" (ex. en "Python" ou "Informatique") pour que l'étiquette reflète le contenu réel et soit compréhensible.

Actuellement le bac "Non classé" ne propose aucun contrôle de renommage (Image 1). Les autres matières créées peuvent être renommées/supprimées mais pas celle-ci.

**Why this priority**: "Non classé" est un nom technique qui ne fait pas sens une fois des sources importées ; l'impossibilité de le renommer force l'utilisateur à garder une étiquette trompeuse sur tout son parcours principal.

**Independent Test**: Depuis la gestion des matières (header ou page sources), renommer "Non classé" en "Python" et vérifier que le nouveau nom apparaît dans le sélecteur, sur Accueil ("Matière active : Python"), dans "Mon parcours" et sur les cartes de parcours.

**Acceptance Scenarios**:

1. **Given** la matière "Non classé" existe, **When** j'active l'action Renommer et saisis "Python", **Then** le nom est mis à jour partout (sélecteur, badge Matière active, titres de parcours) sans créer de doublon ni perdre les rattachements livres/parcours.
2. **Given** tentative de renommage avec nom vide ou déjà pris ("java"), **When** je valide, **Then** un message d'erreur clair s'affiche et le nom n'est pas changé.
3. **Given** le renommage réussi, **When** je rafraîchis ou change de matière puis reviens, **Then** le nouveau nom persiste.

---

### User Story 3 - Gérer les apprenants sur la page Apprenants (Priority: P2)

En tant qu'utilisateur (Image 3 : page #/apprenants), je veux créer, lister, activer et supprimer des profils d'apprenants pour suivre la progression individuellement par matière.

Image 3 montre le formulaire "Nouvel apprenant" et la liste "1 apprenant(s) 0742a64a" avec bouton Activer, mais la pastille "Matière active" affiche encore "Non classé" alors que le header est sur "java" — incohérence de contexte. Le besoin est que la gestion des apprenants respecte le contexte global et que les actions de création/activation soient explicites.

**Why this priority**: Sans gestion fiable des apprenants, la progression, les révisions et les parcours ne peuvent pas être isolés par utilisateur, ce qui fausse les métriques.

**Independent Test**: Sur #/apprenants, créer un apprenant "Alice", vérifier qu'elle apparaît dans la liste, l'activer et vérifier que le header et le badge reflètent l'apprenant actif, puis supprimer ou désactiver avec confirmation.

**Acceptance Scenarios**:

1. **Given** sur #/apprenants avec header sur "java", **When** je crée "Alice", **Then** la liste passe à 2 apprenant(s), "Alice" apparaît, et le toast confirme la création sans changer la matière active.
2. **Given** deux apprenants, **When** j'active "Alice", **Then** le sélecteur d'apprenant du header passe à "Alice" et le badge "Apprenant actif" (si affiché) est cohérent.
3. **Given** un apprenant actif, **When** je tente de le supprimer, **Then** une confirmation est demandée ; si confirmée, l'apprenant disparaît et s'il était actif, l'activation bascule sur un apprenant restant ou "aucun".
4. **Given** page Apprenants ouverte, **When** je change la matière dans le header, **Then** la pastille "Matière active" de la page reflète immédiatement "java" (pas "Non classé").

---

### Edge Cases

- Que se passe-t-il quand l'utilisateur change de matière pendant un stream de génération de cours ? Le stream en cours doit soit se terminer sur l'ancien contexte, soit être annulé proprement, jamais mélanger les matières.
- Comment le système gère une matière sans aucun livre/parcours ? Accueil et Mon parcours affichent un état vide explicite (0 étapes, CTA "Créer un parcours") au lieu de réafficher le contenu d'une autre matière.
- Que se passe-t-il si le seul parcours d'une matière est supprimé ? La vue revient à l'état vide sans réafficher un parcours d'une autre matière.
- Comment gérer la suppression de la matière active (y compris "Non classé" renommée) ? Suppression avec confirmation obligatoire puis l'active bascule sur la première matière restante avec notification.
- Que se passe-t-il si deux matières ont le même nom après renommage insensible à la casse ? Le système refuse avec message "Nom déjà utilisé".
- Apprenant : création avec nom vide, doublon, caractères spéciaux — validation côté client et serveur avec message explicite.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système MUST filtrer tout contenu dépendant de la matière (parcours, étapes, prochaine étape de l'Accueil, statistiques Sources/Notions/Étape active) par l'identifiant complet de la matière active sélectionnée dans le header, et MUST réactualiser les vues Accueil et Mon parcours immédiatement après chaque changement sans afficher de données d'une autre matière. La bibliothèque "Mes sources" MUST être filtrée par défaut par matière active avec un toggle "Toutes" permettant d'afficher l'ensemble des sources.
- **FR-002**: Le système MUST permettre de renommer ET de supprimer la matière par défaut "Non classé" exactement comme toute autre matière (même UX, même menu), avec validation (nom non vide, unique insensible à la casse) et confirmation obligatoire avant suppression, et propagation du nouveau nom dans tous les affichages (sélecteur, badges, titres de parcours).
- **FR-003**: Le système MUST persister la matière active (et l'apprenant actif) entre les rechargements et les navigations, de sorte que le filtrage reste cohérent après refresh.
- **FR-004**: Le système MUST afficher sur la page Apprenants (#/apprenants) une pastille ou zone "Matière active" cohérente avec le sélecteur global du header (pas de valeur figée à "Non classé" quand "java" est actif).
- **FR-005**: Le système MUST permettre sur la page Apprenants de créer un apprenant pour la matière active (saisie nom + bouton "Créer un apprenant"), de lister uniquement les apprenants de la matière active avec date de création, d'activer un apprenant (devient l'apprenant du header pour cette matière), et de supprimer un apprenant avec confirmation — la liste est filtrée par matière (autres matières invisibles quand "java" est actif).
- **FR-006**: Le système MUST isoler la progression (parcours, étapes validées, exercices) par couple (matière × apprenant), de sorte que chaque apprenant dispose de sa propre progression par matière et que passer de "Non classé" à "java" ne réaffiche jamais les étapes Python pour le même apprenant.
- **FR-007**: Le système MUST afficher des états vides explicites quand une matière n'a aucun parcours/livre (ex. "Aucun parcours pour cette matière" avec CTA) plutôt que de réutiliser le contenu d'une autre matière.
- **FR-008**: Le système MUST valider côté serveur le nom de matière et d'apprenant (longueur, unicité) et renvoyer une erreur 400 avec message exploitable si invalide, sans créer d'entrée fantôme.
- **FR-009**: Le changement de matière ou d'apprenant MUST invalider les caches client (liste des parcours, prochaine étape) pour éviter l'affichage de données périmées pendant 1-2 secondes après le switch.

### Key Entities

- **Matière (Subject)**: Regroupe livres, parcours, notions et progression. Attributs : identifiant, nom (renommable, unique), apprenant propriétaire implicite via contexte, date de création. La matière "Non classé" est une matière comme les autres, simplement créée par défaut.
- **Apprenant (LearnerProfile)**: Identité d'apprentissage rattachée à une matière (isolée par matière). Attributs : identifiant, nom affiché, matière parent, date de création, actif ou non. Un seul apprenant actif par matière ; les parcours/progressions sont lus à travers le couple (matière, apprenant).
- **Parcours (LearningPath)**: Ensemble ordonné d'étapes générées depuis les livres d'une matière. Attributs : matière source, apprenant, liste d'étapes, pourcentage de progression. Doit être filtrable par matière et apprenant.
- **Étape / Notion (PathStep / Notion)**: Unité atomique du parcours, avec statut (À faire / Terminé), contenu de cours associé. Toujours rattachée à un parcours donc indirectement à une matière.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Changer de matière via le sélecteur met à jour Accueil et Mon parcours en moins de 2 secondes avec les données de la nouvelle matière, sans flash de l'ancien contenu (vérifiable manuellement sur les captures : passer de "Non classé" à "java" ne montre plus "Introduction à Python").
- **SC-002**: 100% des tentatives de renommage "Non classé" vers un nom valide réussissent et le nouveau nom est visible sur toutes les vues après refresh.
- **SC-003**: La page Apprenants affiche au moins 95% du temps la même matière active que le header (cohérence mesurée en naviguant 20 fois entre matières ; tolérance : 0 divergence).
- **SC-004**: Un nouvel utilisateur crée un apprenant en moins de 30 secondes depuis #/apprenants et le voit activé dans le header sans rechargement manuel.
- **SC-005**: Aucun signalement utilisateur de "mélange de données entre matières" après la livraison lors d'un test avec 2 matières et 2 apprenants (test d'acceptation : 10 switches successifs sans fuite).

## Assumptions

- L'API expose déjà les matières et apprenants via le sélecteur header ; c'est la couche d'affichage/filtrage Vue qui est incomplète, pas le stockage SQLite (qui possède déjà `subjects`, `learning_paths` avec `subject_id`).
- La matière "Non classé" peut être renommée sans migration lourde : son id reste technique, seul le champ `name` change.
- La page Apprenants est l'endroit canonique pour créer/activer un apprenant ; le sélecteur header reflète l'état mais ne crée pas d'apprenant.
- Le filtrage par matière prime sur les données globales : les statistiques (0%, Sources 8) doivent redevenir "par matière" si elles étaient globales.
- La persistance de la matière/apprenant actifs passe par le store préférences et/ou localStorage, cohérent avec l'existant (`preferences.ts`).
- Aucune nouvelle dépendance d'exécution n'est ajoutée (principe V Légèreté).
- Les endpoints existants doivent être réutilisés (principe II Préservation) ; ajouter un paramètre `subject_id`/`learner_id` si nécessaire mais sans dupliquer la logique.
