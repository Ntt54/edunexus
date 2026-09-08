# Contract — Ingestion à jobs (P1-A)

## Table `ingestion_jobs` (migration idempotente `PRAGMA table_info`)

Colonnes : `id, source_type, original_filename, url, content_hash,
status, progress_percent, phase_label, embedding_status, nodes_created,
error_message, created_at, updated_at` (cf. `data-model.md` E-006).

## `TutorService` (ajouts, existant inchangé)

```python
def import_and_index(path, ...) -> Book       # EXISTANT INCHANGÉ (compat, principe II) : adossé aux jobs en interne
def import_job(path, ...) -> str              # NEW : entrée async, retourne un job_id aussitôt
def get_ingestion_job(job_id: str) -> dict    # NEW : snapshot sérialisable
def list_ingestion_jobs(limit=50) -> list[dict]  # NEW
```

- L'import reste synchrone côté appelant (retourne `job_id` aussitôt) ;
  le pipeline tourne en tâche de fond (`asyncio.Task`, pas de nouveau thread
  par défaut pour rester ≤ 8 Go).
- Transitions autorisées : avant uniquement dans l'ordre
  `uploaded → extracting → classifying → dispatching → embedding → completed`,
  `failed` depuis tout état (avec `error_message` + `_log_error`).
- `embedding_status` suit le mode P0-B (`skipped` si `skip`, `done` si vecteurs
  persistés, `failed` si tous providers HS — le job peut quand même finir
  `completed` grâce au BM25).
- Dedup : `content_hash` (sha256) — doublon exact ⇒ job `completed` immédiat
  avec `nodes_created = 0` + référence au livre existant.

## `web/server.py` (transport fin)

- `GET /api/ingestion/jobs` → liste ; `GET /api/ingestion/jobs/{id}` → détail.
  Validation `Origin`/`Host` existante conservée (principe IV).
- Affichage : `phase_label` + `progress_percent` dans l'espace Bibliothèque
  (retouche `tutor.html` + `node --check`).
