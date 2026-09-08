# Research — Phase 0 (010-greffe-autreprojet)

Recherche effectuée en lecture directe des sources (`autreprojet/` + base EduNexus).
Les deux explorateurs sous-agents étant en erreur réseau, leurs résultats partiels
(cartographie) ont été réconciliés puis le détail a été relu directement :
tous les `NEEDS CLARIFICATION` sont résolus ci-dessous.

## R-001 — Sandbox d'exécution Python

- **Decision** : porter `python-tutor-main/backend/app/runner.py` + `safety.py`
  vers `tutor/sandbox.py` + `tutor/safety.py`, exécution via
  `asyncio.create_subprocess_exec(sys.executable, "-I", "-B", script)`.
- **Rationale** : `assessment.py` ne corrige que via LLM (stub FR-020) ; le runner
  apporte la preuve d'exécution (stdout/stderr/exit/timeout) avec env vide
  (sans `PATH`), cwd `0o700` éphémère, sortie tronquée 32 Ko, code plafonné
  50 Ko, rlimits POSIX (CPU 5 s, AS 256 Mo, FSIZE 16 Mo, NPROC 64), timeout
  mural 5 s / plafond 30 s. Le scan AST bloque avant exécution.
- **Alternatives considered** :
  - Conteneurs/microVM/seccomp : rejeté — le source lui-même le documente
    hors-périmètre prototype ; coût et complexité incompatibles principe V.
  - `eval()`/`exec()` in-process : rejeté — aucune isolation (principe IV).

## R-002 — Scan statique de sécurité

- **Decision** : conserver la taxonomie source telle quelle —
  `BLOCKED_MODULES` (réseau, subprocess, multiprocessing, ctypes, pickle…),
  `WARN_MODULES` (os/pathlib/shutil… en strict seulement), `_DANGEROUS_CALLS`
  (`os.system`, `eval`, `exec`, `__import__`, `socket.socket`…), `open()` bloqué
  uniquement en strict (`TUTOR_STRICT_IMPORTS=1`).
- **Rationale** : défense en profondeur documentée honnêtement (le scanner ne
  prétend pas être exhaustif ; la vraie barrière est le subprocess). `open()`
  reste permis par défaut (exercices « lire un fichier ») car le tempdir +
  rlimits limitent les dégâts.
- **Alternatives considered** : allowlist d'imports stricts par défaut —
  rejeté (casserait les exercices légitimes `os`/`pathlib` pédagogiques).

## R-003 — Notation des exercices (visible + cachés)

- **Decision** : reprendre le schéma `exercises.py` : `Exercise`
  (id/title/section/concepts/prompt/starter/visible_tests/hidden_tests/references
  filtrées par allowlist `docs_refs.py`), `grade()` = concaténer soumission +
  tests → `run_python` → `GradeResult` par test.
- **Rationale** : le tuteur voit le vrai output ; tests cachés anti-triche ;
  références http filtrées contre l'hallucination d'URLs.
- **Alternatives considered** : notation 100 % LLM (actuel) — conservée en
  complément (verdict pédagogique), la preuve d'exécution s'y ajoute sans
  changer le contrat `AttemptResult`.

## R-004 — Registre de providers LLM

- **Decision** : adapter `OpenTutor router.py` (`ProviderRegistry` :
  `register`/`register_variant`/`get` avec chaîne primaire → fallback,
  `LLMClient` ABC + `is_healthy`, circuit breaker/cooldown de
  `circuit_breaker.py`, `MockLLMClient`) en `tutor/providers/registry.py`,
  en enveloppant `client.py` httpx existant (adaptateurs fins par backend :
  Ollama natif, OpenAI-compat, GGUF/`llama-server`).
- **Rationale** : fallback automatique + `ping_all` (observabilité) +
  client mock pour tests offline ; `client.py` garde son contrat
  (gate `benchmark.sh` inchangé).
- **Alternatives considered** : remplacement de `client.py` — rejeté
  (principe II + gate de parité throughput).

## R-005 — Modes d'embedding

- **Decision** : reprendre `embedding/registry.py` : `auto` (provider rapide
  API si dispo, sinon skip), `eager` (toujours calculer), `skip`
  (`NoOpEmbeddingProvider`, vecteurs nuls), `FallbackEmbeddingProvider`
  (chaîne avec log de fallback).
- **Rationale** : machines ≤ 8 Go — l'embedding local lent ne doit jamais
  bloquer l'import ; le BM25/cosinus existant (`retrieval.py`) couvre `skip`.
- **Alternatives considered** : embedding systématique — rejeté (principe V) ;
  Chroma/Milvus — rejeté (dépendances lourdes) ; `LocalEmbedding`
  (`services/embedding/local.py`, `SentenceTransformer("all-MiniLM-L6-v2")`
  + zero-pad 384→1536) — **explicitement exclu** : tire `sentence-transformers`
  ⇒ `torch` (Go de RAM, lent sur CPU). Vérifié : l'import est paresseux
  (dans `__init__`), mais l'instanciation exigerait torch — donc ne porter que
  `registry.py` (modes + fallback) + `base.py`, branchés sur les providers CPU
  existants (GGUF/Ollama) ; défaut `auto` ⇒ `skip` sur machine CPU sans clé API.

## R-006 — Jobs d'ingestion

- **Decision** : porter le modèle `models/ingestion.py` + statuts
  `upload_processing.py` + labels de phase `pipeline.py`
  (`uploaded → extracting → classifying → dispatching → embedding →
  completed|failed`, `progress_percent`, `phase_label`, `embedding_status`,
  `nodes_created`, `error_message`) vers une table `ingestion_jobs` +
  tâche de fond devant `TutorService.import/index`. Dedup contenu (xxhash →
  stdlib `hashlib` si besoin, principe V).
- **Rationale** : statut requêtable + reprise sur erreur (principe VI),
  ingestion non bloquante ; migration idempotente style existant.
- **Alternatives considered** : statut en mémoire — rejeté (perdu au redémarrage,
  pas d'observabilité) ; Celery/Redis — rejeté (infra lourde).

## R-007 — Contract test web

- **Decision** : répliquer `test_contract_coverage.py` : collecter les
  `fetch("…")` de `web/static/tutor.html`, résoudre les routes de
  `web/server.py` (y c. préfixe `/tutor`), échouer sur endpoint
  sans route / route orpheline consommée par aucun espace.
- **Rationale** : 9 espaces, ~85 routes, SPA monofichier — la dérive
  front/back est le risque n°1 ; test offline pur (regex + import routes).
- **Alternatives considered** : test Playwright E2E — rejeté (besoin d'un
  serveur + navigateur, non offline) ; OpenAPI généré — FastAPI le fournit
  déjà, le test le confronte au réel.

## R-008 — Ce qui est explicitement rejeté (avec motif)

| Candidat | Motif de rejet |
|---|---|
| Chroma/Milvus/Qdrant, LangChain, ColBERT | RAM + dépendances, incompatibles V/offline |
| SvelteKit/Next.js séparé | `tutor.html` autonome exigé (V) |
| Supabase/Stripe/PostHog/Honcho (tutor-gpt) | cloud-only, anti offline-first |
| FSRS complet + knowledge-graph | différé : `review.py`/`progress.py` actuels suffisent (II, YAGNI) |
| Crawl4AI/unstructured/rapidocr | lourds ; pypdf + Granite-Docling existants conservés |
| sentence-transformers/torch (+ faster-whisper) | Go de RAM, CPU lent ; embeddings via GGUF/Ollama CPU ou `skip` (V + objectif CPU) |
