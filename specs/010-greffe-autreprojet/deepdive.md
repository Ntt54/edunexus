# Deep dive — synthèse des 4 projets (2026-09-07)

Passages en profondeur read-only (3 explorateurs + lecture directe).
Références `chemin:lignes` vérifiées. Objectif : valider/compléter le plan 010,
**CPU-only** (pas de GPU, pas de torch/CUDA — NFR-006).

## 1. OpenTutor-main — le plus proche d'EduNexus

Backend FastAPI (`apps/api/main.py:124`), 35 routers via `services/router_registry.py:34`,
47 services, ~25 tables SQLite async WAL (`database.py:18`, `StaticPool`,
`WAL|SYNCHRONOUS=NORMAL|busy_timeout 5s`), Alembic. Frontend Next.js 16 (`:3001`).
Config `config.py:10` : `llm_provider=ollama`, `llama3.2:3b`, `embedding_mode=auto`,
`auth_enabled=False` mono-utilisateur.

- **LLM** : `services/llm/router.py:50 ProviderRegistry` (variants
  large/small/fast/standard/frontier, `ping_all:133`, health-monitor 30 s,
  `LLMConfigurationError` si vide) ; **Ollama passe par `OpenAIClient`
  (`{ollama_base_url}/v1`)** (`:306`) — même pattern que notre `GGUFLLMProvider`.
  `base_client.py:13` (`stream_chat/chat/extract/get_last_usage`),
  `circuit_breaker.py:22` (`COOLDOWN_STEPS=[5,10,20,60]`, seuil 3, reset 120 s),
  `mock_client.py:11`.
- **Ingestion 7 étapes** : `pipeline.py:127 run_ingestion_pipeline` (xxhash dedup
  + fallback sha256 `:148`, phases `_PHASE_LABELS:64`, catch-all → `failed:380`) ;
  `classification.py:18,28,37,45,66` (6 regex nom + heuristiques contenu sur
  3000 car **0 LLM** + MIME filetype→magic→mimetypes) ; `dispatch.py:18`
  (dedup `source_file`, route vers `_markdown_to_tree` PageIndex) ;
  `upload_processing.py:234` (`asyncio.gather(_do_embed, _do_auto_generate)`).
- **Jobs** : `models/ingestion.py:24` (`pending|uploaded|extracting|classifying|
  dispatching|embedding|completed|failed` + `embedding_status`), `WrongAnswer:135`
  (5 catégories d'erreur, `diagnosis∈fundamental_gap|trap|carelessness|mastered`).
- **Quiz/diagnostic** : `quiz_submission.py:121` (grade → `PracticeResult` →
  `classify_error` → `WrongAnswer` → mastery, 1 transaction) ;
  `diagnosis/classifier.py:40` (5 cats + confidence + evidence, JSON contraint),
  `derive.py:19` (version "clean"), `cat_pretest.py` (CAT adaptatif),
  `coding_grader.py` ; `models/practice.py:14` (`PracticeProblem` :
  8 types, `knowledge_points`, `difficulty_layer 1-3`,
  `problem_metadata{traps,core_concept,bloom}`).
- **Mémoire/adaptatif** : `memory/pipeline.py:44,57` + `pipeline_stages.py:33`
  (overlap mots ≥ 0.5 + cosine ≥ 0.85, decay 90 j), `generate_teaching_state:149`,
  `format_resumption_prompt:226` ; `block_decision/` (12 règles, `MAX_OPS 2`) ;
  `spaced_repetition/fsrs.py:36` (**FSRS-5/6, 21 params, stdlib seul**) ;
  `lector.py` (revue priorisée sémantique) ; `loom_*` (Graphusion, fusion > 0.85).
- **Recherche** : `search/fusion.py:26 hybrid_search` (RRF `1/(60+rank)` +
  signal + coverage), `strategies.py` (keyword LIKE + vector + tree),
  `rag_fusion.py:32` (variantes de requêtes) ; `CompatVector` (JSON-text, dim 1536).
- **Prompts** : `prompts/*.md` (`teaching.md` 22 l. + `{{include:…}}`/`{{var}}`,
  sans Jinja) — transposables tels quels vers `tutor/prompts.py`.
- **Deps** : `requirements-core.txt` ~35 pkgs **CPU-only ✅** (fastapi, sqlalchemy
  async, openai+anthropic clients, pypdf/docx/pptx/trafilatura, tiktoken, xxhash,
  thefuzz pur, langgraph⚠️) ; `requirements-full.txt` ❌ (playwright, crawl4ai,
  redis, mcp, pix2tex, pyBKT). `sentence-transformers` jamais en core ✅.

## 2. python-tutor-main — la référence CPU/minimaliste

`backend/app/main.py:465` (`create_app`, 8 routes, `StaticFiles` monté si
`TUTOR_SERVE_FRONTEND=1`), 4 deps (`fastapi, uvicorn[standard], httpx, pydantic`),
frontend vanilla 0 npm + PWA offline (`sw.js` bypass `/api/`), `gemma3:4b` (~2-3 Go).

- **Évaluation** : `POST /api/evaluate:311` — `run_python` → `docs_refs.lookup`
  → `_build_evaluation_prompt:220` (evidence packet code+exit+durée+stdout+refs)
  → LLM juge → `_classify_assessment:287` (`passed/needs_work/error`, fallback
  exit_code) + `_extract_next_step:297`. `POST /api/chat:401` augmente le system
  avec les docs (`_augment_chat_with_docs:364`) et ajoute les refs en frame
  finale du stream.
- **`grade()`** (`exercises.py:220-288`) : scan safety seul → programme =
  soumission + `_HARNESS_TEMPLATE` (sentinel `__TUTOR_HARNESS__`+JSON, robuste
  au print élève) → `run_python(skip_safety=True)` → split visible/hidden.
- **`docs_refs.py`** : 20 hosts allowlist, `_CURATED` ~50 tokens→URLs,
  `_verify_online` (HEAD parallèle, fallback 405→GET), `lookup` (max 4 refs,
  `online_ok` + note si réseau KO).
- **Config** : `config.py:43` (`OLLAMA_URL, TUTOR_MODEL, TUTOR_RUN_*,
  TUTOR_STRICT_IMPORTS, TUTOR_DOCS_*`), system prompt chargé depuis fence ``` du
  `.md` + `FALLBACK` si OSError ; `GET /api/config` expose l'introspection ;
  `health` en `degraded` +200 (jamais 503).
- **Infra** : `install.sh` (rebuild venv sensible au path, wheelhouse offline,
  opt-in Ollama), `run.sh` (probe `:11434`, watcher health, `--open-browser`),
  `smoke_*.sh`, `check_site.sh`, CI py 3.10/11/12 + `node --check` + shellcheck ;
  `evaluation.md` (golden `must_include/must_not_include`) ; ADR offline-first.
- **Faiblesses** : mono-Python, pas de RAG/SQLite/quiz/spaced-repetition/
  providers multiples ; frontend EN seul.

## 3. open-tutor-ai-CE-main — riche mais façade à 80 %

`main.py` → `gateway/http/app.py:105 create_app()` (24 routers sous `/api/v1`,
convention `repository/service/router`, SQLAlchemy **sync**), SocketIO
`/realtime` (`socket.py:25`, `SESSION_POOL`/`USAGE_POOL`, legacy `/ws` rejeté 404),
JWT + RBAC **binaire `is_admin`** (`roles/` et `permissions/` = 1 ligne !),
SvelteKit 2 servi par FastAPI (`SPAStaticFiles` fallback), i18n 3 langues,
**PWA inexistante** (`manifest.json` = `{}`).

- **Providers** : `ai/providers/service.py` (`verify_ollama GET /api/version`,
  `verify_openai`, `get_merged_models` + cache 60 s, `resolve_provider`) ;
  `ollama_native.py` (`pull/create/delete/upload`, allowlist
  huggingface.co|github.com) ; `proxy.py` (`proxy_json/proxy_stream`,
  connexion eager, 4xx avant 200, timeout 300 s).
- **RAG = stub** : `ai/retrieval/service.py` = config KV seule, `query/*` →
  `{results:[]}`, **0 import chroma/milvus/qdrant dans `ai/`** malgré les deps ;
  `EMBEDDING_MODEL` jamais consommé ; web-search désactivé en dur ;
  faster-whisper déclaré mais jamais appelé en local ; avatar = champ `avatar_id`.
- **Contract test** : `tests/test_contract_coverage.py:1-405` — scan
  `` fetch(`${BASE}/…`) `` dans `ui/src/lib/apis/**/*.ts` (+ `method:` sur
  8 lignes, défaut GET), `_BASE_URL_MAP`, `${chatId}→{chat_id}`,
  assert chaque (METHOD, path) ∈ `/openapi.json` + `FORBIDDEN_PATTERNS`
  anti-legacy + ~100 exclusions documentées (= dette assumée).
- **Poids** : 116 lignes, torch **toujours** installé (CPU forcé,
  `Dockerfile.backend:96`), image multi-Go ; `requirements-ci.txt:1` l'avoue
  (`Does NOT include heavy optional deps`). Seul léger pertinent : `rank-bm25`.
- **Idées légères** : `build_llm_body` (strip+hoist), `resolve_provider` + map
  TTL, `FeedbackForm {data,meta,snapshot}` + garde owner-ou-admin (HITL minimal),
  upload 64 Ko + 413 précoce + `require_owned`, `GET /api/config` feature-flags,
  workflow i18n + gate CI.

## 4. tutor-gpt-main (Bloom) — contre-exemple cloud confirmé

Next 15 + React 19, ~70 deps Node. Chat maison 3 couches : `StreamReader`
(compteur d'accolades) ← JSON concaténés ← `respond()` : LLM1 *thought*
(découpé sur `␁` en `thought/honchoQuery/pdfQuery`) → `Promise.all`
(Honcho + RAG PDF) → LLM2 *response* → sauvegarde. Mémoire = **100 % Honcho
distant** (sessions + métamessages typés + résumé glissant 11→5 en 6 phrases),
fenêtre 11, quota PDF 5 Mo. Supabase (RLS own + trigger `handle_new_user`),
Stripe webhook, Arcjet (rate-limit 8/min, fail-open), PostHog/Sentry/Langfuse.
Tests vitest **live** (collections Honcho réelles + leurres) — anti offline.
Viole I/III/IV/V. Récupérable sans cloud : patterns de prompts (découpage
Empath `pensée ␁ requête`, injection XML `<context>/<past_summary>`, nommage
`5 mots verbe d'action`), chunks typés + `ThinkBox`, **KaTeX**
(`remark-math/rehype-katex` → copiable dans `tutor.html`), posture socratique
(UNE question de clôture), disclaimer + rate-limit affichés.

## 5. Backlog P2 (nouvelles greffes, CPU-only, triées valeur/effort)

1. **FSRS réel** (`spaced_repetition/fsrs.py:36`, stdlib) → remplace SM-2 de
   `review.py` ; `LearningProgress` + `mastery_snapshot` comme modèle.
2. **Classification 0-LLM** (`ingestion/classification.py:37,45,66`) →
   `extractors.py`/`classifier.py` (regex+heuristiques avant LLM ; MIME via
   stdlib, pas `filetype`).
3. **`AppError`** (`libs/exceptions.py:20` + handlers) → `tutor/errors.py`.
4. **Diagnostic erreurs 5 cats** (`diagnosis/classifier.py:40` + `derive.py:21`,
   prompts + parsing JSON) → `QuizEngine/ExerciseEngine` + colonnes
   `WrongAnswer` (`models/ingestion.py:135`).
5. **Mémoire compacte** (`memory/pipeline.py:44,57`, `pipeline_stages.py:33`,
   `generate_teaching_state:149`) → version sync SQLite (notre BM25 existe).
6. **Blocs adaptatifs simplifiés** (`block_decision/`, 12→4 règles :
   deadline/forgetting/weak/prereq) → pilotage des 9 espaces.
7. **`docs_refs.lookup`** (`python-tutor`) → garde-fou citations du Retriever.
8. **Feedback standard** (`_build_evaluation_prompt`, `_classify_assessment`,
   `_extract_next_step`) → `passed/needs_work/error + next_step` dans
   `TutorService`.
9. **HITL minimal** (`FeedbackForm` + owner-ou-admin) → rating/commentaire.
10. **Durcissement uploads** (64 Ko-chunks, 413 précoce, `require_owned`).
11. **KaTeX** (`tutor-gpt`) → `tutor.html` (maths) ; **ThinkBox** (pensée pliable).
12. **Exports TSV/ICS stdlib** (idée `export.py`, sans genanki/icalendar).
13. **Mini-diagnostic 5Q** (`onboarding.py:82` + `cat_pretest.py`) → `diagnostic`.
14. **CI robuste** (`install.sh` rebuild, `smoke_*.sh`, golden `evaluation.md`).

❌ Confirmés à jeter : torch/transformers/colbert, Chroma/Milvus/Qdrant/pgvector,
faster-whisper local, unstructured/rapidocr, Playwright/crawl4ai, langgraph/agent,
Canvas, MCP/skills, Redis/scheduler, Supabase/Stripe/Honcho/PostHog/Sentry,
Next.js/SvelteKit séparés, `sentence-transformers` (déjà NFR-006).
