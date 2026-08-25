# Data Model — 005-platform-ui-library

**Date** : 2026-08-25 | **Prérequis** : [research.md](./research.md)

Entités nouvelles ou étendues uniquement — les entités existantes
(`subjects`, `books`, `categories`, `book_categories`, `chunks`,
`embeddings`, `notions`, sessions) sont référencées pour leurs relations,
pas redéfinies.

## 1. Conversation (= session étendue) — ÉTENDU

Une conversation nommée et indépendante regroupant un fil de questions/réponses.

| Champ | Type | Notes |
|---|---|---|
| id | TEXT PK | identifiant de session existant |
| subject_id | TEXT | espace associé (nullable si espace supprimé) |
| **title** | TEXT | nom affichable ; défaut « Sans titre » ; éditable |
| **created_at** | REAL | horodatage de création |
| **updated_at** | REAL | mis à jour à chaque message |
| started_at / messages… | — | champs existants conservés tels quels |

Relations : 1 conversation → N messages (mécanisme existant) ;
1 conversation → N entrées `conversation_sources`.

Règles :
- Créer une conversation n'en supprime aucune autre (FR-007).
- Supprimer une conversation supprime ses messages et ses sources actives
  (cascade explicite, journalisée).
- Le renommage ne touche que `title`.

## 2. SourceSelection (par conversation) — NOUVEAU

Sous-ensemble courant de la bibliothèque utilisé par le RAG d'une conversation.

Table : `conversation_sources`
| Colonne | Type | Notes |
|---|---|---|
| conversation_id | TEXT PK,FK→sessions(id) ON DELETE CASCADE | |
| book_id | TEXT PK,FK→books(id) ON DELETE CASCADE | affinité TEXT alignée sur books.id |

Invariants :
- Un document référencé une seule fois par conversation (PK composée).
- La sélection référence des identifiants : renommer catégorie/livre ne
  l'affecte pas ; supprimer livre/catégorie nettoie en cascade.
- Aucune duplication de document ni d'embedding (référence pure).

## 3. Entités existantes référencées (non modifiées)

- **Subject (Espace/Domaine)** : racine de la hiérarchie bibliothèque ;
  rattachement des conversations.
- **Category (Catégorie/Groupe)** : many-to-many avec books
  (`book_categories`) ; sert de dossier hiérarchique ET de groupe
  sélectionnable en bloc.
- **Book (Document)** : titre, statut d'indexation (`indexing|ready|error`),
  chunks + embeddings ; état de sélection unique dérivé de
  `conversation_sources`.
- **Notion** : maîtrise/lacunes ; agrégée dans Apprentissage et Accueil.
- **Parcours** : hors modèle v1 (emplacement navigation réservé, FR-024).

## 4. États & transitions

### Document (indexation)
```
(indexing) ── succès ──▶ (ready)
     └──── échec ──────▶ (error)   # raison journalisée, ré-import possible
```

### Sélection multi-niveaux (dérivée, non stockée)
```
état conteneur = tout coché | partiel | vide
calculé depuis les états documents descendants ; cocher/décocher un
conteneur applique l'état à tous ses documents.
```

### Conversation
```
(création) → active ⇄ inactive (fermée mais rouvrable) → supprimée
suppression = messages + sources actives supprimés (cascade), confirmée.
```

## 5. Migrations

- `ALTER TABLE sessions ADD COLUMN title TEXT` +
  `ALTER TABLE sessions ADD COLUMN updated_at REAL` — garde
  PRAGMA-table_info existante (idempotent, style maison).
- `CREATE TABLE IF NOT EXISTS conversation_sources (...)`.
- Compatibilité : lignes existantes reçoivent `title=''` (affiché « Sans
  titre ») et `updated_at=started_at` au premier listage.
