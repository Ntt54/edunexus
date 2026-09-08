# Feature 010 — Greffe des modules `autreprojet` sur la base EduNexus

**Branch**: `010-greffe-autreprojet` (à créer depuis `main`) | **Date**: 2026-09-07
**Statut**: Draft — plan seul (`tasks.md` hors périmètre de cette commande)

## Contexte

`autreprojet/` contient 4 projets tuteurs fonctionnels analysés :
- **OpenTutor-main** (le plus proche : FastAPI + jobs d'ingestion, `ProviderRegistry`, FSRS) ;
- **python-tutor-main** (minimaliste : 4 dép, sandbox `runner.py`/`safety.py`, prompts `.md`) ;
- **open-tutor-ai-CE-main** (riche mais lourd : ~120 dép, Chroma/Milvus — contre-exemple) ;
- **tutor-gpt-main** (SaaS cloud Next.js/Supabase/Stripe — contre-exemple, à éviter).

Décision utilisateur : **garder la base EduNexus** (`TutorService`, SQLite WAL, offline-first,
low-spec) et **récupérer tels quels** les modules mieux implémentés ailleurs, lot par lot.
Ordre : P0 sandbox → P0 registry LLM → P1 jobs ingestion → P1 contract test → P2 (14 greffes deepdive.md §5).
Synthèse des passages en profondeur : [deepdive.md](./deepdive.md) (backlog P2 §5).

## Clarifications

### Session 2026-09-07

- Q: Quels lots la feature 010 doit-elle couvrir — uniquement les 4 lots P0/P1, ou aussi le backlog P2 du deep dive ? → A: Tout, P2 complet inclus.
- Q: Quand la preuve d'exécution sandboxée doit-elle s'appliquer à grade_answer ? → A: Opt-in par appel (paramètre optionnel, défaut comportement actuel).
- Q: L'import de document doit-il devenir asynchrone ou garder le comportement synchrone actuel ? → A: Async + polling (job_id aussitôt, tâche de fond, endpoints de suivi).
- Q: Le contract test doit-il être en tolérance zéro ou autoriser une liste d'exceptions documentée ? → A: Zéro + allowlist motivée (échec par défaut, exceptions explicites dans le test).

## Lots

### P0-A — Sandbox Python (recommandé en premier)

Porter `python-tutor-main/backend/app/runner.py` (298 l.) + `safety.py` (268 l.) +
logique `grade()` (`exercises.py`) vers `src/ollama_tutor/tutor/` pour donner à
`grade_answer` une **preuve d'exécution réelle** (le hook FR-020 n'est qu'un stub).

### P0-B — Registre de providers LLM + embeddings

Porter `OpenTutor-main/apps/api/services/llm/router.py` (`ProviderRegistry`, fallback,
circuit breaker, `MockLLMClient`) + `services/embedding/registry.py`
(modes `auto`/`eager`/`skip`, `NoOpEmbeddingProvider`, `FallbackEmbeddingProvider`)
devant `client.py` httpx existant, sans changer son contrat.

### P1-A — Ingestion à jobs

Porter `OpenTutor-main` `services/ingestion/pipeline.py` (7 étapes),
`routers/upload_processing.py` (statuts), `models/ingestion.py` vers
`TutorService.import/index` + `LibraryStore` (table jobs, tâche de fond).

### P1-B — Contract test web

Répliquer `open-tutor-ai-CE-main/tests/test_contract_coverage.py` :
scanner les `fetch()` de `web/static/tutor.html` et les confronter aux routes de
`web/server.py` dans `tests/contract/`.

### P2 — Backlog deep dive (14 greffes, inclus — clarification 2026-09-07)

Détaillées dans [deepdive.md](./deepdive.md) §5 : FSRS réel, classification
0-LLM, `AppError`, diagnostic 5 catégories, mémoire compacte, blocs adaptatifs
simplifiés, `docs_refs.lookup`, feedback standard, HITL minimal, durcissement
uploads, KaTeX + ThinkBox, exports TSV/ICS, mini-diagnostic 5Q, CI robuste.
Mêmes NFR (I–VI, CPU-only) ; chaque item réversible et testé offline.

## Exigences fonctionnelles

- **FR-001** : `grade_answer` accepte en opt-in (paramètre `execution=None`
  par défaut) une preuve d'exécution sandboxée (stdout/stderr/exit
  code/timeout) sans changer son contrat de retour (`AttemptResult`,
  INVARIANT 3 : solution jamais révélée). Sans le paramètre, comportement
  actuel strictement inchangé.
- **FR-002** : le sandbox bloque avant exécution les imports/appels hostiles
  (réseau, subprocess, `os.system`, `eval`/`exec`, pickle…) et retourne des
  `safety_events` exploitables par le tuteur.
- **FR-003** : le code élève s'exécute avec timeout mural borné (défaut 5 s,
  plafond 30 s), env vide, cwd temporaire `0o700`, sortie tronquée, rlimits
  POSIX quand disponibles.
- **FR-004** : `ProviderRegistry` choisit un provider sain (primaire → fallback),
  avec variantes `large`/`small`/`fast`, et `MockLLMClient` pour les tests offline.
- **FR-005** : les embeddings supportent `auto` (rapide ou rien), `eager`
  (toujours), `skip` (vecteurs nuls, coût zéro) pour machines ≤ 8 Go.
- **FR-006** : tout import de document crée un job suivi
  (`pending → extracting → classifying → dispatching → embedding → completed|failed`)
  avec `progress_percent`, `phase_label`, `embedding_status`, `error_message`.
  L'import est asynchrone : retourne un `job_id` aussitôt, pipeline en tâche
  de fond, suivi via `GET /api/ingestion/jobs` et `GET /api/ingestion/jobs/{id}`.
- **FR-007** : le contract test échoue si un `fetch()` du front n'a pas de route
  correspondante (ou l'inverse pour les routes consommées par les 9 espaces),
  sauf exceptions inscrites dans une allowlist explicite et motivée du test
  (ex. `/api/log-error`, `/api/health`).

## Exigences non fonctionnelles

- **NFR-001 (Constitution I)** : `tutor/` n'importe ni `fastapi` ni `textual`
  (vérifié par `tests/contract/test_tutor_imports.py`).
- **NFR-002 (Constitution III)** : tests 100 % offline (`httpx.MockTransport`,
  `@pytest.mark.asyncio` explicite) ; tout bug corrigé reçoit un test de
  régression qui échoue avant le correctif.
- **NFR-003 (Constitution V)** : stdlib d'abord ; **zéro nouvelle dépendance
  d'exécution** (les 4 lots n'en exigent aucune : `ast`, `asyncio`,
  `subprocess`, `resource`, `sqlite3` + `httpx`/`numpy` déjà présents).
- **NFR-004** : parité de débit `ollama run` — relancer `./benchmark.sh`
  après toute touche à `client.py` (lot P0-B).
- **NFR-005** : `node --check` sur le script inline de `tutor.html` après
  toute modification UI ; suite complète `venv/bin/pytest tests/ -q` verte.
- **NFR-006 (Objectif CPU — NON NÉGOCIABLE)** : le projet tourne sur CPU seul
  (machine actuelle : `llama-server` GGUF + Ollama, cf. `gguf_llm.py` stdlib +
  httpx). Aucun lot n'ajoute de dépendance GPU ni `torch`/CUDA :
  `sentence-transformers` est explicitement exclu (ne pas porter
  `LocalEmbedding` d'OpenTutor) ; l'embedding local passe par les providers
  CPU existants (GGUF/Ollama) ou le mode `skip` (défaut sur machine sans clé
  API ni GPU).

## Hors périmètre (explicitement rejeté)

- Chroma / Milvus / Qdrant / pgvector par défaut, LangChain, 120-dép :
  incompatibles low-spec/offline.
- Cloud-only tutor-gpt : Supabase Auth/RLS, Stripe, PostHog, Sentry, Honcho.
- Split frontend Next.js/SvelteKit : `tutor.html` reste un fichier autonome
  (principe V) ; `web/server.py` reste transport fin.
- Knowledge-graph OpenTutor (loom/graph, `agent/` langgraph) : exclu — trop
  couplé, hors périmètre CPU-raisonnable. (Le FSRS réel fait partie du P2,
  voir Lots.)

## Critères d'acceptation

- **AC-001** : `venv/bin/pytest tests/ -q` verte après chaque lot.
- **AC-002** : aucune fonctionnalité existante supprimée ou dégradée (principe II).
- **AC-003** : chaque lot est réversible indépendamment (nouveaux modules +
  branchement fin, pas de réécriture).
- **AC-004** : `errors.log` reçoit toute exception backend via `_log_error`
  (principe VI).
