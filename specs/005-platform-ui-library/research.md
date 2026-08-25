# Research & Decisions — 005-platform-ui-library

**Date** : 2026-08-25 | **Statut** : complet (aucun NEEDS CLARIFICATION)

Format : Décision / Rationale / Alternatives considérées. Les faits
d'architecture cités sont vérifiés dans le code courant (`server.py` 59 routes
auditées, `store.py`, `service.py`, `tutor.html` ~3 050 lignes).

## 1. Routage multi-vues sans framework

**Décision** : routage par hash natif (`#/accueil`, `#/conversations`,
`#/bibliotheque`, `#/apprentissage`, `#/entrainer`, `#/quiz`, `#/progression`,
`#/explorer`) dans le fichier unique `tutor.html`. Chaque espace est une
section togglée ; l'entrée active de navigation est synchronisée au hash ;
`hashchange` gère retour/arrière du navigateur.

**Rationale** : zéro dépendance (constitution V) ; les sections restent montées
donc l'état par vue est préservé ; URLs partageables ; la connexion WebSocket
existante survit aux changements de vue.

**Alternatives** : framework SPA (rejeté — principe V, fichier autonome
exigé) ; pages serveur distinctes (rejeté — perdrait WS et état à chaque
navigation).

## 2. Conversations = sessions existantes étendues

**Décision** : réutiliser la table `sessions` (déjà persistée avec messages)
en ajoutant `title` éditable et `updated_at`. Une « conversation » est une
session nommée ; la liste des conversations liste TOUTES les sessions (pas
seulement la dernière par matière) ; ouvrir une conversation rejoue son
historique via le mécanisme `resume` existant.

**Rationale** : l'historique, la reprise et la persistance existent déjà
(`tutor_list_sessions/resume/close`, map JS `S.sessionIds`) — principe V :
étendre plutôt que dupliquer. Zéro migration destructive.

**Alternatives** : table `conversations` parallèle dupliquant l'historique
(rejeté — duplication de données et double mécanisme de reprise) ; stockage
JSON séparé (rejeté — fragmentation du stockage).

**Point de vérification en phase tasks** : confirmer que le schéma `sessions`
actuel permet plusieurs sessions par matière (sinon, lever la contrainte dans
la requête de liste).

## 3. Hiérarchie bibliothèque sans changement de schéma

**Décision** : Domaine = `subject` existant ; Catégorie/Groupe = catégories
existantes (many-to-many `book_categories`) ; arborescence calculée à la
volée (regroupement livres ↔ catégories). Pas de sous-catégories en v1.

**Rationale** : les modèles couvrent exactement la hiérarchie demandée
(spec, hypothèses) ; zéro migration ; la déduplication multi-catégories est
déjà garantie par l'état unique par document.

**Alternatives** : table d'arbre `nodes(parent_id)` (rejeté v1 — YAGNI,
migration inutile ; extensible plus tard si sous-catégories exigées).

## 4. Périmètre RAG par conversation

**Décision** : le frame `ask` gagne un champ optionnel `book_ids` (liste
d'identifiants de livres). `service.ask()` le transmet à la retrieval qui
filtre les passages par livre avant scoring. Les sources actives sont
persistées par conversation (`conversation_sources(conversation_id, book_id)`)
et renvoyées automatiquement si `book_ids` est absent mais qu'une conversation
est référencée.

**Rationale** : filtrer AVANT la recherche économise les appels d'embedding et
garantit zéro fuite de contexte hors sélection (exigence FR-018/SC) ; la
retrieval possède déjà l'identifiant de livre par passage.

**Alternatives** : filtrage côté client après recherche (rejeté — gaspillage
et fuite de contexte) ; filtre par catégories plutôt que livres (rejeté — la
sélection individuelle de documents est explicitement exigée ; les catégories
sont résolues en livres côté service).

## 5. Matrice de relocalisation (principe II)

| Fonctionnalité actuelle | Espace cible | Changement |
|---|---|---|
| Import PDF + statut d'indexation | Bibliothèque | déplacement |
| Recherche sémantique | Bibliothèque | déplacement |
| Liste/groupement par matière | Bibliothèque | enrichi (arborescence) |
| Catégories (CRUD, membership) | Bibliothèque | enrichi (arborescence) |
| Classification auto (lots) | Bibliothèque | inchangé |
| Chat ask/cancel, socratique, citations | Conversations | déplacement |
| Dictée vocale (whisper) | Conversations | déplacement |
| Reprendre session / liste sessions | Conversations | devient liste conversations |
| Réglages pédagogiques (socratic/niveau/think) | barre conversation | inchangé |
| Notions, maîtrise, lacunes, path, prepare | Apprentissage | déplacement |
| Révisions dues + grade | Apprentissage | déplacement |
| Exercices (générer/indice/répondre/solution) | Entraîner | déplacement |
| Quiz + examens (scope catégories) | Quiz/Examens | déplacement |
| Progression (route progress) | Progression | déplacement |
| Glossaire/expliquer, comparer, localiser, rank-books, carte | Explorer | déplacement |
| Modèles embedding/LLM + badge moteur | en-tête global | inchangé |
| Toasts, erreurs → errors.log | global | inchangé |

Aucune fonctionnalité sans destination : matrice exhaustive (principe II).

## 6. Tableau de bord par composition client

**Décision** : l'Accueil compose les endpoints existants (progress, gaps,
sessions, books/index-status) côté navigateur ; aucun endpoint agrégateur
nouveau en v1.

**Rationale** : toutes les données existent déjà ; éviter un endpoint N+1 tant
que la performance n'est pas constatée (principe V).

**Alternatives** : endpoint `/api/tutor/dashboard` agrégé (différé — à créer
si plusieurs allers-retours deviennent perceptibles).

## 7. Suppression/suppression-renommage avec sources actives

**Décision** : supprimer une catégorie retire ses documents des sélections
(`conversation_sources` nettoyé en cascade sur book_ids) et journalise
l'opération ; renommer catégorie/domaine n'exige rien (les sélections
référencent des identifiants, pas des noms).

**Rationale** : cohérence FR-020 (jamais de suppression silencieuse) et
principe VI.

**Alternatives** : bloquer la suppression (rejeté — trop restrictif) ;
conserver les documents orphelins comme actifs (rejeté — incohérent).
