# Fonctionnalités EduNexus — Détail complet

**Professeur IA local** basé sur RAG, optimisé pour machines modestes (CPU).
Interface web autonome (`tutor.html`) accessible via `http://127.0.0.1:9215/tutor`.

---

## Table des matières

1. [Navigation multi-espaces](#1-navigation-multi-espaces)
2. [Bibliothèque de sources](#2-bibliothèque-de-sources)
3. [Import & indexation](#3-import--indexation)
4. [Séance de tutorat (chat RAG)](#4-séance-de-tutorat-chat-rag)
5. [Fiches de révision](#5-fiches-de-révision)
6. [Résumé de document](#6-résumé-de-document)
7. [Diagnostic initial](#7-diagnostic-initial)
8. [Parcours d'apprentissage](#8-parcours-dapprentissage)
9. [Exercices & Studio](#9-exercices--studio)
10. [Quiz & Examens](#10-quiz--examens)
11. [Mode Épreuve](#11-mode-épreuve)
12. [Validation citations](#12-validation-citations)
13. [Reranking post-recherche](#13-reranking-post-recherche)
14. [Historique des erreurs](#14-historique-des-erreurs)
15. [Gamification](#15-gamification)
16. [Configuration multi-fournisseurs](#16-configuration-multi-fournisseurs)
17. [Recherche hybride BM25 + sémantique](#17-recherche-hybride-bm25--sémantique)
18. [Extraction métadonnées](#18-extraction-métadonnées)
19. [Embeddings bornés et file nocturne](#19-embeddings-bornés-et-file-nocturne)
20. [Automatisations locales](#20-automatisations-locales)
21. [Sécurité & architecture](#21-sécurité--architecture)

---

## 1. Navigation multi-espaces

**User Story** : F005 — 9 espaces de navigation distincts dans l'interface web.

### Espaces

| Espace | Route | Description |
|--------|-------|-------------|
| **Accueil** | `/tutor` | Tableau de bord : sujets récents, actions rapides, profil apprenant |
| **Conversations** | onglet | Sessions nommées persistantes, historique navigable |
| **Bibliothèque** | onglet | Gestion des livres sources (import, suppression, statut d'indexation) |
| **Apprentissage** | onglet | Fiches de révision, parcours, préparation connaissances |
| **Entraîner** | onglet | Exercices adaptatifs à difficulté choisie |
| **Quiz/Examens** | onglet | Quiz à correction immédiate, examens programmés |
| **Progression** | onglet | Barres de maîtrise, historique des erreurs, gaps identifiés |
| **Explorer** | onglet | Carte de connaissances, glossaire, relations entre concepts |
| **Réglages** | onglet | Fournisseurs LLM/embedding, paramètres GGUF, restart serveur |

### Implémentation

- **Fichier unique** : `src/ollama_tutor/web/static/tutor.html` — SPA autonome (pas de framework JS)
- **Navigation** : `showSpace(name)` gère l'affichage par `data-space` attributes
- **Persistance** : les onglets conservent leur état lors des changements d'espace
- **Commit** : Feature 005 (36/36 tâches)

---

## 2. Bibliothèque de sources

**User Story** : F005 — Gestion hiérarchique des documents sources.

### Fonctionnalités

- Import de fichiers PDF, EPUB, DOCX, PPTX, TXT, MD
- Vue hiérarchique : Domaines > Matières > Livres
- Statut par livre : `indexed`, `error`, `cancelled`
- Suppression individuelle avec cascade (chunks, embeddings)
- Catégorisation manuelle et **classification automatique par LLM**

### Implémentation

- **Modèles** : `Book`, `Subject`, `Category` dans `models.py`
- **Stockage** : tables SQLite `books`, `subjects`, `categories`, `chunks`, `embeddings` dans `store.py`
- **Classification** : `classifier.py` — hybride règles + LLM (lots de 25 titres)
- **Routes** : `GET /api/tutor/books`, `DELETE /api/tutor/book/{id}`, `POST /api/tutor/import`
- **File nocturne** : `GET /api/tutor/index-queue`, `POST /api/tutor/index-queue/start`,
  `POST /api/tutor/index-queue/stop`, `POST /api/tutor/books/{id}/cancel`.
- **UI** : `renderLibrary()` dans `tutor.html` avec comptages temps réel et panneau de file

---

## 3. Import & indexation

**User Story** : F005/F006/F007 — Import multi-format avec extraction hybride.

### Formats supportés

| Format | Extracteur | Métadonnées |
|--------|-----------|-------------|
| **PDF** | pypdf | Page (numéro) |
| **EPUB** | ebooklib + BeautifulSoup | Section (titre HTML h1-h6) |
| **DOCX** | python-docx | — |
| **PPTX** | python-pptx | — |
| **TXT/MD** | lecture directe | — |

### Pipeline d'indexation

```
Fichier → extract_text() → (texte, metadata) tuples
       → chunk_text_structured() → chunks avec frontières de paragraphes
       → embed_texts() → vectors via Ollama/GGUF/OpenAI-compat
       → add_chunks() → SQLite avec chapter/section/page
```

### Implémentation

- **Extracteurs** : `extractors.py` — `extract_text()` retourne `Iterable[tuple[str, dict]]` (US9)
- **Chunking** : `chunk_text_structured(text, max_chars=1200, overlap=200)` — détection paragraphes, fusion titres, sous-découpe phrases (US4)
- **Indexation** : `_run_index()` dans `service.py` ; la file nocturne utilise un worker
  asyncio unique, ordonné par `created_at`, et réutilise les lignes `pending` de SQLite.
  Les lignes `indexing` orphelines sont remises en `pending` au démarrage.
- **Métadonnées** : colonnes `chapter`, `section`, `page` dans la table `chunks` (US9)
- **Table document_metadata** : `store.py` — paires clé/valeur par livre

---

## 4. Séance de tutorat (chat RAG)

**User Story** : F005 — Chat interactif avec mode socratique.

### Fonctionnalités

- Mode socratique : indices progressifs au lieu de réponses directes
- Citations cliquables vers la source et la page exacte
- Transcription vocale (Web Speech API)
- Barre de maîtrise par notion
- Reprise de session
- Fonctionne **sans source sélectionnée** (réponse sans contexte documentaire)

### Implémentation

- **Service** : `TutorService.ask()` et `_ask_iter()` — streaming SSE avec events `thinking/content/done`
- **Retrieval** : `retrieve_hybrid()` — BM25 + cosine + RRF (US5)
- **Citations** : `validate_citations()` — vérification 3-tier (exacte → livre → substring) (US10)
- **Route** : WebSocket chat dans `server.py`
- **UI** : `renderChat()` avec markdown render, citation click handlers

---

## 5. Fiches de révision

**User Story** : US2 — Génération de fiches par matière.

### Fonctionnalités

- Fiche avec définitions, formules, points clés, schémas conceptuels
- Niveau adaptatif (débutant/intermédiaire/avancé)
- Impression PDF via CSS `@media print`
- Basée sur les chunks de la matière sélectionnée

### Implémentation

- **Prompt** : `build_revision_sheet_prompt(chunks, subject_name, level)` dans `prompts.py`
- **Service** : `generate_revision_sheet(subject_id, book_id=None, chapter=None)` dans `service.py`
- **Route** : `POST /api/tutor/subjects/{id}/revision-sheet`
- **Tests** : `test_revision_sheet.py` — 3 tests
- **Commit** : `5a43b46`

---

## 6. Résumé de document

**User Story** : US1 — Résumé hiérarchique d'un livre.

### Fonctionnalités

- Résumé global → sections → points clés
- Optionnel : résumé par chapitre
- Format markdown structuré

### Implémentation

- **Prompt** : `build_summary_prompt(chunks, book_title, chapter=None)` dans `prompts.py`
- **Service** : `summarize_book(book_id, chapter=None)` dans `service.py`
- **Route** : `POST /api/tutor/books/{id}/summary`
- **Tests** : `test_summary.py` — 3 tests
- **Commit** : `5a43b46`

---

## 7. Diagnostic initial

**User Story** : US3 — Quiz adaptatif de positionnement.

### Fonctionnalités

- Questions adaptatives dont la difficulté dépend du niveau précédent
- Rapport final : niveau par concept, forces, faiblesses
- Parcours suggéré basé sur les lacunes identifiées

### Implémentation

- **Prompt** : `build_diagnostic_question_prompt(concept_name, difficulty_level)` dans `prompts.py`
- **Service** :
  - `start_diagnostic(subject_id)` — initialise le quiz, retourne la première question
  - `submit_diagnostic_answer(session_id, answer)` — note, calcule la difficulté suivante
  - `get_diagnostic_result(session_id)` — rapport de positionnement
- **Routes** :
  - `POST /api/tutor/subjects/{id}/diagnostic`
  - `POST /api/tutor/diagnostic/{session_id}/answer`
  - `GET /api/tutor/diagnostic/{session_id}/result`
- **Tests** : `test_diagnostic.py` — 12 tests
- **Commit** : `52a8b0c`

---

## 8. Parcours d'apprentissage

**User Story** : US13 — Ordonnancement pédagogique par LLM.

### Fonctionnalités

- Parcours auto-généré basé sur les lacunes du diagnostic
- Ordonnancement par dépendance pédagogique (le LLM décide de l'ordre)
- Étapes séquentielles avec description
- Parcours personnalisé par gap identifié

### Implémentation

- **Prompt** : `build_learning_path_prompt(concepts, gaps, level)` dans `prompts.py`
- **Service** : `auto_generate_path(subject_id)` avec `_parse_learning_path_response()` (JSON parsing robuste, fallback)
- **Modèles** : `LearningPath`, `PathStep` dans `models.py`
- **Route** : `POST /api/tutor/subjects/{id}/auto-path`
- **Tests** : `test_auto_path.py` — 24 tests (prompt, parser, intégration)
- **Commit** : `0665efc`

---

## 9. Exercices & Studio

**User Story** : F005 — Exercices adaptatifs avec correction.

### Fonctionnalités

- Exercices à difficulté choisie (facile/moyen/difficile)
- Correction immédiate avec explication
- Demande de solution complète
- Analyse de code
- Barre de maîtrise par notion
- Historique des notions (à revoir / maîtrisé)
- Résumé audio (synthèse vocale du navigateur)

### Implémentation

- **Service** : `generate_exercise()`, `grade_answer()`, `request_solution()`, `analyze_code()` dans `service.py`
- **Évaluation** : `ExerciseEngine` dans `assessment.py`
- **Routes** : `POST /api/tutor/exercises`, `POST /api/tutor/answers`, `POST /api/tutor/solution`
- **UI** : onglet Entraîner avec difficulté sélectionnable

---

## 10. Quiz & Examens

**User Story** : F005 — Quiz à correction immédiate.

### Fonctionnalités

- Quiz générés à partir des concepts d'une matière
- Correction instantanée avec feedback
- Questions à choix multiples, vrai/faux, ouvertes
- Score final et analyse par concept

### Implémentation

- **Service** : `create_quiz()`, `submit_answers()`, `get_quiz()` dans `service.py`
- **Évaluation** : `QuizEngine` dans `assessment.py`
- **Routes** :
  - `POST /api/tutor/subjects/{id}/quizzes`
  - `POST /api/tutor/quizzes/{id}/submit`
  - `GET /api/tutor/quizzes/{id}`

---

## 11. Mode Épreuve

**User Story** : US14 — Résolution d'épreuves importées.

### Fonctionnalités

- Import multi-fichiers (images/PDF de sujets d'examen)
- Analyse OCR (stub pour images, extraction directe pour PDF)
- Extraction de questions numérotées avec concepts associés
- Résolution RAG avec citations pour chaque question
- Indices progressifs (hint_level 1-3)

### Implémentation

- **Prompts** :
  - `build_exam_analysis_prompt(exam_text)` — extraction de questions
  - `build_exam_resolve_prompt(...)` — résolution ou indice
- **Service** :
  - `parse_exam_document(paths)` — import concaténé
  - `analyze_exam(exam_text)` — extraction questions via LLM
  - `resolve_exam_question(question_id, hint_level)` — résolution RAG
- **Routes** :
  - `POST /api/tutor/exam/import`
  - `POST /api/tutor/exam/analyze`
  - `POST /api/tutor/exam/questions/{id}/resolve`
- **Tests** : `test_exam_mode.py` — 16 tests
- **Commit** : `0665efc`

---

## 12. Validation citations

**User Story** : US10 — Vérification automatique des citations.

### Fonctionnalités

- Extraction des patterns `[Livre X ...]`, `[Book X X]`, markdown bold
- Vérification 3-tier : exacte → livre seul → substring partiel
- Warnings envoyés au client pour citations invalides
- Réponse jamais rejetée (warning non-bloquant)

### Implémentation

- **Fonction** : `validate_citations(response_text, available_sources)` dans `retrieval.py`
  - `_CITATION_RE` : regex patterns multi-formats
  - `_normalise_citation()` : nettoyage et comparaison
  - `_source_key()` / `_book_only_key()` : clés de matching
- **Intégration** : `_ask_iter()` dans `service.py` — accumulation du texte streamé, appel post-génération
- **WebSocket** : frame `citation_warnings` dans `server.py`
- **Tests** : `test_citation_validation.py` — 27 tests
- **Commit** : `0665efc`

---

## 13. Reranking post-recherche

**User Story** : US12 — Amélioration de la précision après retrieval.

### Fonctionnalités

- Reranking optionnel (désactivé par défaut)
- Score combiné : 0.7 × similarité cosinus + 0.3 × Jaccard lexical
- Boost pour les termes de la requête présents dans le chunk
- Fonctionne sur les résultats BM25, cosinus, et hybrides

### Implémentation

- **Classe** : `SimpleReranker` dans `reranker.py` (nouveau fichier)
  - `_tokenize(text)` : extraction tokens (stopwords fr/en filtrés)
  - `_jaccard_similarity(set_a, set_b)` : intersection/union
  - `score(query, candidate)` : score combiné
  - `rerank(query, candidates, top_k)` : reordering + limit
- **Intégration** : `Retriever.retrieve()` et `retrieve_hybrid()` dans `retrieval.py`
- **Config** : `tutor_reranking_enabled` (bool, défaut False) dans `config.py`
- **Tests** : `test_reranker.py` — 14 tests
- **Commit** : `0665efc`

---

## 14. Historique des erreurs

**User Story** : US11 — Suivi détaillé des erreurs.

### Fonctionnalités

- Enregistrement automatique des erreurs (quiz + exercices)
- Détails : question, réponse donnée, bonne réponse, type d'erreur
- Consultation par matière et par concept
- Persistance dans SQLite

### Implémentation

- **Table** : `error_history` dans `store.py` (champs : subject_id, concept_name, question_text, given_answer, correct_answer, source_refs, error_type, created_at)
- **Enregistrement** :
  - `QuizEngine.submit_answers()` — erreurs quiz dans `assessment.py`
  - `TutorService.grade_answer()` — erreurs exercices dans `service.py`
- **Service** : `get_error_history(subject_id, concept_name=None, limit=50)` dans `service.py`
- **Route** : `GET /api/tutor/subjects/{id}/errors`
- **Tests** : `test_error_history.py` — 5 tests
- **Commit** : `0665efc`

---

## 15. Gamification

**User Story** : US15 — Motivation par XP et streaks.

### Fonctionnalités

| Action | XP |
|--------|----|
| Quiz complété | +20 |
| Exercice correct | +15 |
| Streak quotidien (jour 2+) | +10 bonus |

- Streak quotidien : incrémentation si connexion consécutive
- Reset si jour manqué
- Suivi du plus long streak
- Badges débloqués (structure prête)
- Profil apprenant centralisé

### Implémentation

- **Table** : `learner_profile` dans `store.py` (id, total_xp, current_streak, longest_streak, last_active_date, badges_json)
- **Méthodes store** :
  - `add_xp(amount)` : ajout XP + retour total
  - `update_streak()` : logique jour par jour
  - `get_learner_profile()` : profil complet avec désérialisation badges
- **Intégration** : `submit_answers()` (quiz) et `grade_answer()` (exercices) dans `service.py`
- **Route** : `GET /api/tutor/profile`
- **Tests** : `test_gamification.py` — 10 tests
- **Commit** : `0665efc`

---

## 16. Configuration multi-fournisseurs

**User Story** : B1 — Trois moteurs d'embedding/LLM.

### Fournisseurs

| Fournisseur | Config | Usage |
|-------------|--------|-------|
| **Ollama** | défaut | Aucune config requise si Ollama tourne |
| **GGUF local** | `llama_bin`, `embed_gguf`, `llm_gguf` | llama-server (granite models) |
| **OpenAI-compatible** | `openai_api_base`, `openai_api_key` | vLLM, LM Studio, etc. |

### Implémentation

- **Interfaces** : `EmbeddingProvider`, `LLMProvider` dans `providers/`
- **Adapteurs** :
  - `OllamaEmbeddingProvider`, `OllamaLLMProvider` — `providers/ollama_adapter.py`
  - `GGUFEmbeddingProvider` — `providers/gguf_embedding.py` (llama-server /v1/embeddings)
  - `GGUFLLMProvider` — `providers/gguf_llm.py` (llama-server /v1/chat/completions SSE)
  - `OpenAICompatProvider` — `providers/openai_compat.py`
- **Manager** : `LlamaServerManager` — démarrage/arrêt séquentiel de llama-server
- **Config** : properties `tutor_llm_provider`, `tutor_embedding_provider` dans `config.py`
- **Réglages UI** : onglet Réglages avec sélecteurs Provider + GGUF local
- **Route restart** : `POST /api/tutor/restart`

---

## 17. Recherche hybride BM25 + sémantique

**User Story** : US5 — Retrieval précis et robuste.

### Fonctionnalités

- BM25 (TF-IDF léger) pour correspondance lexicale
- Similarité cosinus pour correspondance sémantique
- Fusion par **Reciprocal Rank Fusion** (RRF) : `score(d) = Σ 1/(k + rank_i(d))`
- Filtrage par matière et livre actif (active sources)
- Support du champ `top_k` configurable

### Implémentation

- **Classe** : `BM25Index` dans `retrieval.py` — index TF-IDF avec pondération log-normale
- **Fusion** : `reciprocal_rank_fusion(ranked_lists, k=60)` dans `retrieval.py`
- **Retrieval** : `retrieve_hybrid(subject_id, question, k, book_ids=None)` — exécute cosine + BM25, fusionne, retourne top-k
- **Intégration** : `Retriever` dans `service.py` — utilisé pour le chat RAG
- **Tests** : `test_bm25.py` — 18 tests
- **Commit** : `dbb8bbc`

---

## 18. Extraction métadonnées

**User Story** : US9 — Métadonnées de chapitre/page/section par chunk.

### Fonctionnalités

- PDF : numéro de page par chunk
- EPUB : section (titre HTML h1-h6) par chunk
- DOCX/PPTX : pas de métadonnées spécifiques (structure = texte brut)
- Les métadonnées sont stockées dans les colonnes `chapter`, `section`, `page` de la table `chunks`

### Implémentation

- **Extracteurs** : `extract_text()` retourne `Iterable[tuple[str, dict]]` au lieu de `str`
  - `_extract_pdf()` : yield par page avec `{"page": page_num}`
  - `_extract_epub()` : yield par section avec `{"section": heading}` via `_HeadingExtractor` (HTML parser)
- **Propagation** : `_run_index()` dans `service.py` — injecte markers de page/section, utilise `chunk_text_structured()`
- **Stockage** : `add_chunks()` dans `store.py` — accepte `list[dict]` avec clés chapter/section/page
- **Tests** : `test_metadata_extraction.py` — 7 tests
- **Commit** : `552e343`

---

## 19. Embeddings bornés et file nocturne

**User Story** : US8 — Indexation locale de plusieurs livres sans parallélisme agressif.

### Fonctionnalités

- `embed_batch_size` contrôle le nombre de fragments par requête (défaut 16, borné 1–64).
- `max_parallel_embed` contrôle séparément le nombre de requêtes simultanées ; un
  `asyncio.Semaphore` impose effectivement cette limite (défaut 1 sur CPU modeste).
- Le worker nocturne traite un seul livre à la fois, dans l’ordre `created_at`.
- Les livres restent persistés en `pending`, sont reprenables après redémarrage et
  peuvent être mis en pause ou annulés sans conserver de fragments partiels.

### Implémentation

- **Embeddings** : `embed_texts()` et `_embed_with_provider()` dans `embeddings.py`/
  `service.py`, avec cache par hash+modèle, lots ordonnés et sémaphore.
- **Worker** : `TutorService.start_index_queue()` / `stop_index_queue()` dans `service.py`.
- **SQLite** : `recover_interrupted_indexing()` et `update_index_progress()` dans `store.py`.
- **API** : endpoints `/api/tutor/index-queue*` et `/api/tutor/books/{id}/cancel`.
- **Tests** : batching, cache, limite de concurrence, imports multiples et reprise.

---

## 20. Automatisations locales

### Réindexation différentielle

Lorsqu’un fichier est réimporté au même chemin, EduNexus compare son fingerprint au livre existant. Si le contenu est inchangé, l’import reste un no-op. Si le fichier a changé, le même identifiant de livre est conservé, les chunks précédents sont purgés et le livre repasse dans la file `pending`. Les fragments inchangés peuvent néanmoins réutiliser le cache d’embeddings par hash et modèle.

### Planificateur local

Le planificateur asyncio reste dans le processus EduNexus et vérifie périodiquement la fenêtre horaire locale. Il peut être limité au secteur sous Linux, possède une durée maximale et ne démarre qu’un cycle par fenêtre. Il n’utilise aucun service cloud ni dépendance système obligatoire.

| Réglage | Défaut | Fonction |
|---|---:|---|
| `nightly_enabled` | `false` | Active le planificateur. |
| `nightly_start_at` / `nightly_stop_at` | `23:00` / `07:00` | Définit la fenêtre locale, y compris les fenêtres traversant minuit. |
| `nightly_only_on_ac` | `true` | Évite de charger la machine sur batterie lorsque l’information système est disponible. |
| `nightly_max_runtime_minutes` | `420` | Borne la durée d’un cycle. |
| `nightly_prepare_enabled` | `false` | Lance ensuite `prepare_knowledge()` pour concepts, flashcards, glossaire et relations. |

### Reprise et maintenance

Les erreurs transitoires contenant des indices de transport, timeout, HTTP, serveur ou Ollama sont retentées au plus trois fois avec un délai croissant. Les erreurs persistantes restent visibles en `error` et peuvent être relancées manuellement via `POST /api/tutor/books/{id}/retry`.

La maintenance vérifie `PRAGMA integrity_check`, compte les embeddings orphelins, supprime uniquement ceux qui ne sont plus référencés, exécute `PRAGMA optimize` et peut réaliser un `VACUUM`. Une sauvegarde cohérente de `library.db` est créée avec l’API native SQLite avant l’optimisation.

### Routes

| Route | Usage |
|---|---|
| `GET /api/tutor/nightly` | État du planificateur, fenêtre, secteur, dernière exécution et préparation pédagogique. |
| `POST /api/tutor/nightly/start` / `stop` | Active ou désactive le planificateur immédiatement. |
| `POST /api/tutor/maintenance` | Maintenance et sauvegarde locales ; `vacuum` est optionnel. |
| `POST /api/tutor/books/{id}/retry` | Replace un livre en erreur dans la file. |

---

## 21. Sécurité & architecture

### Constitution v1.0.0

6 principes fondamentaux codifiés dans `.specify/memory/constitution.md` :

| Principe | Règle |
|----------|-------|
| **I. Cœur découplé** | `tutor/` ne jamais importer textual/fastapi |
| **II. Préservation** | Aucune perte de données utilisateur |
| **III. Tests hors-ligne** | Tous les tests offline (httpx.MockTransport) |
| **IV. Sécurité locale** | Loopback uniquement, same-origin enforcement |
| **V. Légèreté** | Pas de nouvelles dépendances runtime lourdes |
| **VI. Observabilité** | Erreurs loguées dans `~/.config/ollama-tui/errors.log` |

### Mesures de sécurité

- Le serveur ne lie que `127.0.0.1` (loopback)
- Same-origin sur WebSocket et requêtes mutantes
- Un seul `llama-server` à la fois (chargement séquentiel)
- Tool-output truncation avant injection dans le contexte modèle
- Path jail pour les opérations fichiers
- Aucune donnée envoyée à des services externes

### Test suite

- **377 tests** hors-ligne
- Mock via `httpx.MockTransport` dans `tests/conftest.py`
- Aucun appel réseau réel dans les tests
- Unit tests + contract tests + integration tests
- Commande : `pytest tests/unit/ -q` (~19s)

---

## Historique des commits (Feature 007 MVP)

| Commit | Description | Tests |
|--------|-------------|-------|
| `909ec2a` | Phase 1-2: office extras + store foundations | — |
| `5a43b46` | US1 Résumé + US2 Fiches de révision | 6 |
| `52a8b0c` | US3 Diagnostic + US4 Chunking structurel | 24 |
| `dbb8bbc` | US5 Recherche hybride BM25 + RRF | 18 |
| `d391028` | US7 GGUFLLMProvider | 6 |
| `552e343` | US8 Parallel embeddings + US9 Extraction métadonnées | 15 |
| `0665efc` | US10-US15: citations, erreurs, reranking, auto-parcours, épreuve, gamification | 96 |
| `9d9fbd9` | Phase 18: docs polish (AGENTS.md + README.md) | — |
| **Total** | **95/95 tâches complètes** | **377** |

---

*Document généré automatiquement à partir du code source.*
*Dernière mise à jour : 26 août 2026*
