# Plan de migration vers PostgreSQL + pgvector

## Contexte

Le système actuel stocke les embeddings dans SQLite sous forme de BLOB, puis construit un index cosine en mémoire (`NumpyVectorIndex`) à chaque requête. Pour améliorer les performances et la scalabilité, nous migrons vers PostgreSQL avec l'extension pgvector.

## Architecture cible

```
┌─────────────────────────────────────────┐
│           PostgreSQL 16 + pgvector       │
│  ┌──────────────┐  ┌─────────────────┐  │
│  │   chunks     │  │  chunks_vec     │  │
│  │   (texte +   │  │  (index HNSW    │  │
│  │    métadonnées)│  │   vectoriel)    │  │
│  └──────────────┘  └─────────────────┘  │
│                                         │
│  • Recherche hybride BM25 + vectorielle  │
│  • Index HNSW natif (ANN)                │
│  • Filtres SQL classiques                │
│  • ACID + transactions                   │
└─────────────────────────────────────────┘
```

## Modifications prévues

### 1. Dépendances Python

**Fichier : `pyproject.toml`**

Ajouter la dépendance :
```
psycopg[binary]>=3.2
```

Remarque : `asyncpg` n'est pas nécessaire car nous utiliserons des requêtes synchrones via `psycopg` dans un thread séparé (pattern existant dans `store.py`).

### 2. Configuration

**Fichier : `src/ollama_tutor/config.py`**

Ajouter les paramètres de connexion PostgreSQL :
```python
pgvector_enabled: bool = False
pgvector_dsn: str = "postgresql://postgres:postgres@localhost:5432/edunexus"
```

### 3. Schéma de base de données

**Fichier : `src/ollama_tutor/tutor/store.py` — nouvelle méthode `init_pgvector_schema()`**

```sql
-- Extension vectorielle
CREATE EXTENSION IF NOT EXISTS vector;

-- Table des chunks (migrée depuis SQLite)
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    text TEXT NOT NULL,
    chapter TEXT,
    page INTEGER,
    section TEXT,
    position REAL DEFAULT 0.0,
    difficulty TEXT,
    content_type TEXT DEFAULT 'prose',
    embedding vector(384),  -- dimension du modèle d'embedding
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index vectoriel HNSW pour la recherche ANN
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Index classique pour les filtres
CREATE INDEX IF NOT EXISTS idx_chunks_subject_id ON chunks(subject_id);
CREATE INDEX IF NOT EXISTS idx_chunks_book_id ON chunks(book_id);
CREATE INDEX IF NOT EXISTS idx_chunks_chapter ON chunks(chapter);
```

### 4. Migration des données SQLite → PostgreSQL

**Nouveau fichier : `src/ollama_tutor/tutor/migrate_to_pgvector.py`**

Script de migration :
1. Lire tous les chunks depuis SQLite
2. Convertir les BLOB d'embeddings en tableaux numpy
3. Insérer dans PostgreSQL via `psycopg`
4. Créer l'index HNSW
5. Rapport de migration (comptes, erreurs, durée)

### 5. Mise à jour de `LibraryStore`

**Fichier : `src/ollama_tutor/tutor/store.py`**

Ajouter un backend PostgreSQL optionnel :
- `__init__()` : détecter si PostgreSQL est activé
- `get_pg_connection()` : établir la connexion
- `get_indexed_chunks_pg()` : lire les chunks avec embeddings
- `add_chunks_pg()` : insérer de nouveaux chunks
- `update_chunks_embedding_pg()` : mettre à jour les embeddings
- `search_similar_pg()` : recherche cosine via pgvector
- `delete_book_chunks_pg()` : supprimer les chunks d'un livre

Le mode SQLite reste disponible en fallback.

### 6. Mise à jour du `Retriever`

**Fichier : `src/ollama_tutor/tutor/retrieval.py`**

Modifier `_index_for()` pour utiliser pgvector quand activé :
- Au lieu de charger tous les vecteurs en mémoire (`NumpyVectorIndex`)
- Exécuter une requête SQL avec `ORDER BY embedding <=> :query_vec LIMIT :k`
- Retourner les résultats directement depuis PostgreSQL

La recherche BM25 reste en Python pur (déjà hybride dans `retrieve_hybrid()`).

### 7. Nouveau `VectorIndex` pgvector

**Nouveau fichier : `src/ollama_tutor/tutor/pgvector_index.py`**

Implémenter le protocole `VectorIndex` avec pgvector :
```python
class PgVectorIndex:
    def __init__(self, dsn: str, subject_id: str, model: str):
        self.dsn = dsn
        self.subject_id = subject_id
        self.model = model

    def search(self, query_vector: list[float], k: int, floor: float = None):
        # SQL: SELECT id, 1 - (embedding <=> %s) AS score
        # WHERE embedding IS NOT NULL AND model = %s
        # ORDER BY embedding <=> %s LIMIT %s
        ...
```

### 8. Endpoint de santé

**Fichier : `src/ollama_tutor/web/server.py`**

Ajouter un endpoint pour vérifier l'état de pgvector :
```
GET /api/tutor/pgvector/status
```

### 9. Tests

**Nouveau fichier : `tests/unit/test_pgvector_migration.py`**

Tests unitaires :
- Connexion PostgreSQL
- Création du schéma
- Insertion et recherche de chunks
- Recherche hybride BM25 + pgvector
- Migration depuis SQLite

## Avantages attendus

| Aspect | Avant (SQLite + numpy) | Après (PostgreSQL + pgvector) |
|--------|------------------------|-------------------------------|
| **Mémoire** | Tous les vecteurs en RAM | Seuls les résultats en RAM |
| **Recherche** | O(n) brute-force | HNSW ANN, ~O(log n) |
| **Latence** | 50-200ms (selon taille) | 5-20ms (index HNSW) |
| **Scalabilité** | ~50k chunks max | 10M+ chunks sur un seul nœud |
| **Persistance** | Rebuild à chaque démarrage | Index persistant sur disque |
| **Hybride** | BM25 Python + numpy séparé | BM25 + pgvector unifiés |

## Impact sur le code existant

### Fichiers modifiés
- `pyproject.toml` — ajout dépendance `psycopg[binary]`
- `src/ollama_tutor/config.py` — paramètres PostgreSQL
- `src/ollama_tutor/tutor/store.py` — backend PostgreSQL optionnel
- `src/ollama_tutor/tutor/retrieval.py` — utilisation de pgvector
- `src/ollama_tutor/web/server.py` — endpoint santé

### Fichiers créés
- `src/ollama_tutor/tutor/pgvector_index.py` — index pgvector
- `src/ollama_tutor/tutor/migrate_to_pgvector.py` — script de migration
- `tests/unit/test_pgvector_migration.py` — tests

### Compatibilité
- SQLite reste le backend par défaut
- PostgreSQL est activé via `config.pgvector_enabled = True`
- Aucune rupture de l'API existante
- Les tests existants continuent de fonctionner

## Étapes d'installation

```bash
# 1. Installer PostgreSQL 16 + pgvector
sudo apt-get install postgresql-16 postgresql-contrib-16

# 2. Créer la base de données
sudo -u postgres psql -c "CREATE DATABASE edunexus;"
sudo -u postgres psql -d edunexus -c "CREATE EXTENSION vector;"

# 3. Installer la dépendance Python
pip install psycopg[binary]

# 4. Exécuter la migration
python -m src.ollama_tutor.tutor.migrate_to_pgvector

# 5. Activer dans la config
export EDUNEXUS_PGVECTOR_ENABLED=true
```

## Estimation des performances

Pour une bibliothèque de **10 000 chunks** (768 dimensions) :

| Opération | SQLite + numpy | PostgreSQL + pgvector |
|-----------|---------------|----------------------|
| Chargement initial | ~500ms (tout en RAM) | ~50ms (connexion) |
| Recherche top-10 | ~50ms | ~5ms |
| Mémoire RAM | ~23 MB | ~5 MB |
| Index rebuild | ~200ms à chaque fois | Jamais (persistant) |
