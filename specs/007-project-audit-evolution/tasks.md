# Tasks — Feature 007 : Analyse et évolution EduNexus

**Spec** : [spec.md](spec.md)
**Date** : 2026-08-26
**Total** : 95 tâches

---

## Phase 1 — Setup

- [ ] T001 Ajouter `python-docx` et `python-pptx` aux optional extras `[office]` dans `pyproject.toml`
- [ ] T002 Créer le module `src/ollama_tutor/tutor/document_formats.py` avec registre de formats (TEXT, PDF, EPUB, DOCX, PPTX) et factory `create_extractor(format)`
- [ ] T003 Mettre à jour `SUPPORTED_FORMATS` dans `src/ollama_tutor/tutor/extractors.py` pour inclure `.docx` et `.pptx`

---

## Phase 2 — Fondations (bloquantes pour les user stories)

- [ ] T004 Ajouter la table `error_history` dans `src/ollama_tutor/tutor/store.py` (champs: id, subject_id, concept_name, question_text, given_answer, correct_answer, source_refs, error_type, created_at)
- [ ] T005 Ajouter la table `document_metadata` dans `src/ollama_tutor/tutor/store.py` (champs: book_id, key, value — pour stocker chapitre, page, section par chunk)
- [ ] T006 Ajouter les colonnes `chapter`, `section`, `page` au modèle `Chunk` dans `src/ollama_tutor/tutor/models.py` si pas déjà présent
- [ ] T007 Ajouter la méthode `store_record_error()` dans `src/ollama_tutor/tutor/store.py` pour persister un historique d'erreurs
- [ ] T008 Ajouter la méthode `store_get_error_history(subject_id, concept_name, limit)` dans `src/ollama_tutor/tutor/store.py`
- [ ] T009 Ajouter la méthode `store_record_document_metadata(book_id, metadata_dict)` dans `src/ollama_tutor/tutor/store.py`

---

## Phase 3 — US1 : Résumé de document (AE1/F4, P1)

> **Objectif** : L'apprenant peut obtenir un résumé structuré de n'importe quel livre ou chapitre de sa bibliothèque.
> **Test indépendant** : POST `/api/tutor/books/{id}/summary` retourne un résumé structuré en markdown.

- [ ] T010 [US1] Créer le prompt `build_summary_prompt(chunks, book_title, chapter=None)` dans `src/ollama_tutor/tutor/prompts.py` — génère un résumé hiérarchique (résumé global → sections → points clés)
- [ ] T011 [US1] Ajouter la méthode `summarize_book(book_id, chapter=None)` dans `src/ollama_tutor/tutor/service.py` — récupère les chunks du livre, assemble le contexte, appelle le LLM avec le prompt de résumé, retourne le résumé structuré
- [ ] T012 [US1] Ajouter la route `POST /api/tutor/books/{book_id}/summary` dans `src/ollama_tutor/web/server.py` — accepte `chapter` optionnel en body
- [ ] T013 [US1] Ajouter le bouton "Résumer" et la fonction JS `summarizeBook(bookId)` dans `src/ollama_tutor/web/static/tutor.html` — affiche le résumé en markdown dans la vue Bibliothèque
- [ ] T014 [US1] Ajouter le test unitaire `test_build_summary_prompt` dans `tests/unit/test_prompts.py`

---

## Phase 4 — US2 : Fiches de révision (AE2/F2, P1)

> **Objectif** : L'apprenant peut générer une fiche de révision synthétique à partir d'un chapitre, livre ou sujet.
> **Test indépendant** : POST `/api/tutor/subjects/{id}/revision-sheet` retourne une fiche structurée.

- [ ] T015 [US2] Créer le prompt `build_revision_sheet_prompt(chunks, subject_name, level)` dans `src/ollama_tutor/tutor/prompts.py` — génère une fiche avec définitions, formules, points clés, schémas conceptuels
- [ ] T016 [US2] Ajouter la méthode `generate_revision_sheet(subject_id, book_id=None, chapter=None)` dans `src/ollama_tutor/tutor/service.py` — assemble les chunks pertinents, appelle le LLM, retourne la fiche
- [ ] T017 [US2] Ajouter la route `POST /api/tutor/subjects/{subject_id}/revision-sheet` dans `src/ollama_tutor/web/server.py` — accepte `book_id` et `chapter` optionnels
- [ ] T018 [US2] Ajouter la vue "Fiche de révision" dans la section Apprentissage de `src/ollama_tutor/web/static/tutor.html` — bouton "Générer fiche", affichage markdown, bouton "Imprimer" (CSS @media print)
- [ ] T019 [US2] Ajouter le test unitaire `test_build_revision_sheet_prompt` dans `tests/unit/test_prompts.py`

---

## Phase 5 — US3 : Diagnostic initial (AE3/F3, P1)

> **Objectif** : À la création d'un sujet, un quiz de positionnement détermine le niveau initial de l'apprenant.
> **Test indépendant** : POST `/api/tutor/subjects/{id}/diagnostic` crée un quiz adaptatif de 10-15 questions.

- [ ] T020 [US3] Créer le prompt `build_diagnostic_question_prompt(concept_name, difficulty_level)` dans `src/ollama_tutor/tutor/prompts.py` — génère une question adaptative dont la difficulté dépend du niveau précédent
- [ ] T021 [US3] Ajouter la méthode `start_diagnostic(subject_id)` dans `src/ollama_tutor/tutor/service.py` — initialise un quiz de positionnement, retourne la première question
- [ ] T022 [US3] Ajouter la méthode `submit_diagnostic_answer(session_id, answer)` dans `src/ollama_tutor/tutor/service.py` — note la réponse, calcule la difficulté de la suivante, retourne la prochaine question ou le résultat final
- [ ] T023 [US3] Ajouter la méthode `get_diagnostic_result(session_id)` dans `src/ollama_tutor/tutor/service.py` — retourne le rapport de positionnement (niveau par concept, forces, faiblesses, parcours suggéré)
- [ ] T024 [US3] Ajouter les routes `POST /api/tutor/subjects/{id}/diagnostic`, `POST /api/tutor/diagnostic/{session_id}/answer`, `GET /api/tutor/diagnostic/{session_id}/result` dans `src/ollama_tutor/web/server.py`
- [ ] T025 [US3] Ajouter le bouton "Diagnostic" dans la vue Accueil de `src/ollama_tutor/web/static/tutor.html` — flux interactif question par question, affichage du rapport final
- [ ] T026 [US3] Ajouter les tests unitaires `test_start_diagnostic`, `test_diagnostic_answer_flow`, `test_diagnostic_result` dans `tests/unit/test_assessment.py`

---

## Phase 6 — US4 : Chunking structurel (AR2, P1)

> **Objectif** : Le chunking respecte les paragraphes, titres et sections. Les métadonnées de chapitre/page sont préservées.
> **Test indépendant** : `chunk_text_structured()` produit des chunks avec des frontières de paragraphe et des métadonnées non-NULL.

- [ ] T027 [US4] Créer la fonction `chunk_text_structured(text, max_chars=1200, overlap=200, metadata=None)` dans `src/ollama_tutor/tutor/extractors.py` — détecte les sauts de paragraphe (`\n\n`), titres (`#`, `##`, etc.), et utilise ces frontières pour les chunks
- [ ] T028 [US4] Ajouter la détection de titres de section via regex (`^#{1,4}\s+.+`) dans `extractors.py` — préserve le titre comme métadonnée `section` du chunk
- [ ] T029 [US4] Ajouter l'extraction de numéro de page (si disponible dans le texte source) dans `extractors.py` — préserve comme métadonnée `page`
- [ ] T030 [US4] Modifier `import_and_index` dans `src/ollama_tutor/tutor/service.py` pour appeler `chunk_text_structured` au lieu de `chunk_text`
- [ ] T031 [US4] Ajouter la migration SQL pour les colonnes `chapter`, `section`, `page` sur la table `chunks` si nécessaire dans `src/ollama_tutor/tutor/store.py`
- [ ] T032 [US4] Ajouter les tests `test_chunk_text_structured_paragraphs`, `test_chunk_text_structured_headings`, `test_chunk_text_structured_metadata` dans `tests/unit/test_extractors.py`

---

## Phase 7 — US5 : Recherche hybride BM25 + sémantique (AR3/F6, P1)

> **Objectif** : Combiner la recherche lexicale et sémantique en un score hybride pour améliorer la pertinence.
> **Test indépendant** : `retrieve_hybrid()` retourne des résultats combinant les deux scores via RRF.

- [ ] T033 [US5] Créer la classe `BM25Index` dans `src/ollama_tutor/tutor/retrieval.py` — index BM25 léger (TF-IDF) construit à partir des chunks, avec méthode `search(query, k)` retournant `(chunk_id, score)` pairs
- [ ] T034 [US5] Ajouter la fonction `reciprocal_rank_fusion(ranked_lists, k=60)` dans `src/ollama_tutor/tutor/retrieval.py` — implémente RRF: `score(d) = Σ 1/(k + rank_i(d))`
- [ ] T035 [US5] Ajouter la méthode `retrieve_hybrid(subject_id, question, k, book_ids=None)` dans `src/ollama_tutor/tutor/retrieval.py` — exécute cosine search + BM25 search, fusionne par RRF, retourne les top-k résultats
- [ ] T036 [US5] Modifier `TutorService` dans `src/ollama_tutor/tutor/service.py` pour utiliser `retrieve_hybrid` au lieu de `retrieve` pour les requêtes de chat
- [ ] T037 [US5] Ajouter les tests `test_bm25_index_build`, `test_bm25_search`, `test_reciprocal_rank_fusion`, `test_retrieve_hybrid` dans `tests/unit/test_retrieval.py`

---

## Phase 8 — US6 : Support DOCX/PPTX (AD1/F8, P1)

> **Objectif** : Importer des documents Word et PowerPoint dans la bibliothèque.
> **Test indépendant** : `extract_text_from_docx()` et `extract_text_from_pptx()` retournent le texte extrait.

- [ ] T038 [US6] Créer la fonction `extract_docx(path)` dans `src/ollama_tutor/tutor/extractors.py` — utilise `python-docx` pour extraire le texte paragraphes par paragraphes
- [ ] T039 [US6] Créer la fonction `extract_pptx(path)` dans `src/ollama_tutor/tutor/extractors.py` — utilise `python-pptx` pour extraire le texte slide par slide
- [ ] T040 [US6] Ajouter `.docx` et `.pptx` au registre `SUPPORTED_FORMATS` dans `extractors.py`
- [ ] T041 [US6] Modifier `extract_text()` dans `extractors.py` pour dispatcher vers `extract_docx` / `extract_pptx` selon le format
- [ ] T042 [US6] Ajouter python-docx et python-pptx aux dépendances optionnelles `[office]` dans `pyproject.toml`
- [ ] T043 [US6] Ajouter le test `test_extract_docx` et `test_extract_pptx` dans `tests/unit/test_extractors.py` avec des fichiers de test miniatures

---

## Phase 9 — US7 : GGUFLLMProvider (F10/OL2, P1)

> **Objectif** : Fournisseur LLM local via llama-server avec streaming SSE, permettant de fonctionner sans Ollama.
> **Test indépendant** : `GGUFLLMProvider.chat_stream()` yields des `StreamEvent` identiques à `OllamaLLMProvider`.

- [ ] T044 [US7] Créer la classe `GGUFLLMProvider(LLMProvider)` dans `src/ollama_tutor/tutor/providers/gguf_llm.py` — wrapper de llama-server `/v1/chat/completions` avec streaming SSE
- [ ] T045 [US7] Implémenter `chat_stream(messages, model=None, options=None)` dans `gguf_llm.py` — parse le SSE stream et yield des `StreamEvent(thinking/content/done)` identiques au format Ollama
- [ ] T046 [US7] Implémenter `embed(texts)` dans `gguf_llm.py` — délègue à `GGUFEmbeddingProvider` existant
- [ ] T047 [US7] Ajouter la factory `create_gguf_llm_provider(config)` dans `src/ollama_tutor/tutor/providers/__init__.py`
- [ ] T048 [US7] Modifier `TutorService` dans `src/ollama_tutor/tutor/service.py` pour utiliser `GGUFLLMProvider` quand `config.llm_provider == "gguf"` et `config.tutor_llm_gguf` est défini
- [ ] T049 [US7] Ajouter le bouton "GGUF (local)" dans la section Fournisseur LLM des Réglages de `src/ollama_tutor/web/static/tutor.html`
- [ ] T050 [US7] Ajouter les tests `test_gguf_llm_provider_stream`, `test_gguf_llm_provider_embed` dans `tests/unit/test_providers.py`

---

## Phase 10 — US8 : llama-server parallel embeddings (OL1, P1)

> **Objectif** : Paralléliser les calculs d'embeddings via `--parallel N` sur llama-server.
> **Test indépendant** : Le serveur démarre avec `--parallel N` et accepte N requêtes simultanées.

- [ ] T051 [US8] Modifier `LlamaServerManager.start()` dans `src/ollama_tutor/tutor/providers/llama_server.py` pour passer `--parallel {config.tutor_max_parallel_embed}` au démarrage
- [ ] T052 [US8] Modifier `embed_texts()` dans `src/ollama_tutor/tutor/embeddings.py` pour envoyer les chunks en batches parallèles (asyncio.gather) quand `max_parallel_embed > 1`
- [ ] T053 [US8] Ajouter le test `test_parallel_embed_batches` dans `tests/unit/test_gguf_embedding.py`

---

## Phase 11 — US9 : Extraction métadonnées chapitre/page (AR5, P1)

> **Objectif** : Extraire automatiquement les métadonnées de chapitre, section et page pendant l'indexation.
> **Test indépendant** : Les chunks produits contiennent des métadonnées de chapitre/page non-NULL quand le document les fournit.

- [ ] T054 [US9] Modifier `extract_text()` dans `src/ollama_tutor/tutor/extractors.py` pour retourner un generator de `(text_chunk, metadata_dict)` au lieu de juste `text` — le metadata_dict contient `page`, `chapter`, `section`
- [ ] T055 [US9] Modifier `chunk_text()` (ou `chunk_text_structured`) dans `extractors.py` pour propager les métadonnées de page/chapitre dans chaque chunk produit
- [ ] T056 [US9] Modifier `import_and_index` dans `src/ollama_tutor/tutor/service.py` pour stocker les métadonnées dans la table `chunks` lors de l'indexation
- [ ] T057 [US9] Ajouter les tests `test_extract_metadata_pdf`, `test_extract_metadata_epub` dans `tests/unit/test_extractors.py`

---

## Phase 12 — US10 : Validation citations (AR4, P1)

> **Objectif** : Vérifier que les références citées par le LLM existent réellement dans les sources.
> **Test indépendant** : `validate_citations()` retourne les citations valides et invalides.

- [ ] T058 [US10] Créer la fonction `validate_citations(response_text, available_sources)` dans `src/ollama_tutor/tutor/retrieval.py` — extrait les citations `[Livre X ...]` du texte, vérifie leur existence dans `available_sources`, retourne `(valid, invalid)`
- [ ] T059 [US10] Modifier `_drive_tutor_ask()` dans `src/ollama_tutor/tutor/service.py` pour appeler `validate_citations()` après la génération et ajouter un warning si des citations invalides sont détectées
- [ ] T060 [US10] Ajouter le champ `citation_warnings` dans le frame `sources` envoyé au client WebSocket dans `src/ollama_tutor/web/server.py`
- [ ] T061 [US10] Ajouter les tests `test_validate_citations_valid`, `test_validate_citations_invalid`, `test_validate_citations_mixed` dans `tests/unit/test_retrieval.py`

---

## Phase 13 — US11 : Historique erreurs détaillé (AE5/F11, P1)

> **Objectif** : Enregistrer chaque erreur avec question, réponse donnée, bonne réponse, et source.
> **Test indépendant** : `store_record_error()` persiste correctement, `store_get_error_history()` retourne les erreurs.

- [ ] T062 [US11] Modifier `QuizEngine.submit_answers()` dans `src/ollama_tutor/tutor/assessment.py` pour appeler `store_record_error()` pour chaque réponse incorrecte
- [ ] T063 [US11] Modifier la notation d'exercices dans `src/ollama_tutor/tutor/assessment.py` pour enregistrer les erreurs détaillées via `store_record_error()`
- [ ] T064 [US11] Ajouter la méthode `get_error_history(subject_id, concept_name=None, limit=50)` dans `src/ollama_tutor/tutor/service.py`
- [ ] T065 [US11] Ajouter la route `GET /api/tutor/subjects/{id}/errors` dans `src/ollama_tutor/web/server.py` — retourne l'historique des erreurs
- [ ] T066 [US11] Ajouter une section "Erreurs récentes" dans la vue Progression de `src/ollama_tutor/web/static/tutor.html` — affiche les dernières erreurs avec la question, la réponse donnée et la bonne réponse
- [ ] T067 [US11] Ajouter les tests `test_record_error_quiz`, `test_record_error_exercise`, `test_get_error_history` dans `tests/unit/test_assessment.py`

---

## Phase 14 — US12 : Reranking post-recherche (AR1/F7, P2)

> **Objectif** : Réordonner les résultats de recherche cosinus avec un reranker léger.
> **Test indépendant** : `rerank_results()` retourne les résultats réordonnés avec des scores améliorés.

- [ ] T068 [US12] Créer la classe `SimpleReranker` dans `src/ollama_tutor/tutor/reranker.py` — reranking basé sur la similarité lexicale (Jaccard) + boost pour les termes de la requête présents dans le chunk
- [ ] T069 [US12] Ajouter la méthode `rerank(query, candidates, top_k)` dans `SimpleReranker` — score combiné = 0.7 * cosine + 0.3 * jaccard_lexical
- [ ] T070 [US12] Modifier `retrieve()` dans `src/ollama_tutor/tutor/retrieval.py` pour appliquer le reranking optionnel quand activé
- [ ] T071 [US12] Ajouter la config `tutor_reranking_enabled` (bool, défaut false) dans `src/ollama_tutor/config.py`
- [ ] T072 [US12] Ajouter le bouton "Reranking" dans les Réglages de `src/ollama_tutor/web/static/tutor.html`
- [ ] T073 [US12] Ajouter les tests `test_simple_reranker_scores`, `test_rerank_boosts_relevant` dans `tests/unit/test_reranker.py`

---

## Phase 15 — US13 : Parcours auto-générés (AE4/F9, P2)

> **Objectif** : Générer automatiquement un parcours d'apprentissage basé sur les gaps.
> **Test indépendant** : `auto_generate_path()` crée un `LearningPath` avec des étapes ordonnées.

- [ ] T074 [US13] Créer le prompt `build_learning_path_prompt(concepts, gaps, level)` dans `src/ollama_tutor/tutor/prompts.py` — demande au LLM d'ordonner les concepts par dépendance pédagogique
- [ ] T075 [US13] Ajouter la méthode `auto_generate_path(subject_id)` dans `src/ollama_tutor/tutor/service.py` — récupère les gaps via `get_gaps()`, appelle le LLM pour l'ordonnancement, crée le `LearningPath` avec les étapes
- [ ] T076 [US13] Ajouter la route `POST /api/tutor/subjects/{id}/auto-path` dans `src/ollama_tutor/web/server.py`
- [ ] T077 [US13] Ajouter le bouton "Générer mon parcours" dans la vue Parcours de `src/ollama_tutor/web/static/tutor.html`
- [ ] T078 [US13] Ajouter les tests `test_auto_generate_path_creates_steps`, `test_auto_generate_path_orders_by_gap` dans `tests/unit/test_learning_paths.py`

---

## Phase 16 — US14 : Mode Épreuve complet (EE1-4/F5, P2)

> **Objectif** : Import multi-pages d'un examen avec OCR, détection de questions, résolution guidée.
> **Test indépendant** : POST `/api/tutor/exam/import` analyse un PDF et retourne les questions détectées.

- [ ] T079 [US14] Créer la méthode `parse_exam_document(paths)` dans `src/ollama_tutor/tutor/service.py` — importe plusieurs images/PDF, applique OCR sur chaque page, retourne le texte brut concaténé
- [ ] T080 [US14] Créer le prompt `build_exam_analysis_prompt(exam_text)` dans `src/ollama_tutor/tutor/prompts.py` — analyse le texte OCR et extrait les questions numérotées avec les notions associées
- [ ] T081 [US14] Ajouter la méthode `analyze_exam(exam_text)` dans `src/ollama_tutor/tutor/service.py` — retourne une liste de questions avec leur numéro, énoncé, notions, et statut (résolue/pas encore)
- [ ] T082 [US14] Ajouter la méthode `resolve_exam_question(question_id, hint_level)` dans `src/ollama_tutor/tutor/service.py` — résout une question avec RAG + citation, ou donne un indice si hint_level > 0
- [ ] T083 [US14] Ajouter les routes `POST /api/tutor/exam/import`, `POST /api/tutor/exam/analyze`, `POST /api/tutor/exam/questions/{id}/resolve` dans `src/ollama_tutor/web/server.py`
- [ ] T084 [US14] Ajouter la vue "Épreuve" dans `src/ollama_tutor/web/static/tutor.html` — upload multi-fichiers, affichage des questions numérotées, bouton "Résoudre" et "Indice" par question
- [ ] T085 [US14] Ajouter les tests `test_parse_exam_document`, `test_analyze_exam_questions`, `test_resolve_exam_question` dans `tests/unit/test_assessment.py`

---

## Phase 17 — US15 : Gamification (AE6, P2)

> **Objectif** : Streaks quotidiens, badges de maîtrise, niveau d'apprenant.
> **Test indépendant** : Les streaks et badges sont calculés et stockés correctement.

- [ ] T086 [US15] Ajouter la table `learner_profile` dans `src/ollama_tutor/tutor/store.py` (champs: id, total_xp, current_streak, longest_streak, last_active_date, badges_json)
- [ ] T087 [US15] Ajouter les méthodes `update_streak()`, `add_xp(amount)`, `get_learner_profile()` dans `src/ollama_tutor/tutor/store.py`
- [ ] T088 [US15] Ajouter le calcul d'XP dans `TutorService` : quiz complété = +20 XP, exercice correct = +15 XP, streak jour = +10 XP bonus dans `src/ollama_tutor/tutor/service.py`
- [ ] T089 [US15] Ajouter la route `GET /api/tutor/profile` dans `src/ollama_tutor/web/server.py`
- [ ] T090 [US15] Ajouter la section "Profil apprenant" dans la vue Accueil de `src/ollama_tutor/web/static/tutor.html` — affiche streak, XP total, badges débloqués

---

## Phase 18 — Polish & Cross-cutting

- [ ] T091 Mettre à jour `AGENTS.md` avec les nouvelles features et commandes de test
- [ ] T092 Mettre à jour `specs/007-project-audit-evolution/spec.md` section 8 avec les numéros de tâches associés
- [ ] T093 Exécuter `python3 -m pytest tests/ -q` et vérifier que tous les tests passent
- [ ] T094 Commit final avec message descriptif couvrant toutes les features implémentées
- [ ] T095 Mettre à jour le README.md avec les nouvelles fonctionnalités

---

## Dépendances entre phases

```
Phase 1 (Setup) ──→ Phase 2 (Fondations) ──→ Toutes les phases suivantes

Phase 3 (US1 Résumé) ── indépendante
Phase 4 (US2 Fiches) ── indépendante
Phase 5 (US3 Diagnostic) ── indépendante
Phase 6 (US4 Chunking) ── indépendante (améliore le RAG pour toutes les phases)
Phase 7 (US5 Recherche hybride) ── dépend de Phase 2 (fondations store)
Phase 8 (US6 DOCX/PPTX) ── indépendante
Phase 9 (US7 GGUFLLM) ── indépendante
Phase 10 (US8 Parallel) ── indépendante
Phase 11 (US9 Métadonnées) ── dépend de Phase 6 (chunking structurel)
Phase 12 (US10 Citations) ── indépendante
Phase 13 (US11 Erreurs) ── dépend de Phase 2 (fondations store)
Phase 14 (US12 Reranking) ── dépend de Phase 7 (recherche hybride)
Phase 15 (US13 Parcours auto) ── indépendante
Phase 16 (US14 Épreuve) ── dépend de Phase 8 (DOCX support)
Phase 17 (US15 Gamification) ── indépendante
Phase 18 (Polish) ── dépend de toutes les phases précédentes
```

## Exécution parallèle possible

```
Batch 1 (simultané):
  Phase 3 (US1 Résumé)
  Phase 4 (US2 Fiches)
  Phase 5 (US3 Diagnostic)
  Phase 6 (US4 Chunking)
  Phase 8 (US6 DOCX/PPTX)
  Phase 9 (US7 GGUFLLM)
  Phase 10 (US8 Parallel)
  Phase 12 (US10 Citations)
  Phase 15 (US13 Parcours auto)
  Phase 17 (US15 Gamification)

Batch 2 (après Batch 1):
  Phase 7 (US5 Recherche hybride)
  Phase 11 (US9 Métadonnées — après US4 Chunking)
  Phase 13 (US11 Erreurs — après fondations)
  Phase 16 (US14 Épreuve — après US6 DOCX)

Batch 3 (après Batch 2):
  Phase 14 (US12 Reranking — après US5 Recherche hybride)

Batch 4 (final):
  Phase 18 (Polish)
```

## Stratégie d'implémentation

1. **MVP** : Phases 1-2 + US1 (Résumé) + US2 (Fiches) + US6 (DOCX/PPTX) — 4 features quick-win en ~1 semaine
2. **V2** : US4 (Chunking) + US5 (Recherche hybride) + US9 (Métadonnées) + US10 (Citations) — améliore tout le RAG
3. **V3** : US7 (GGUFLLM) + US8 (Parallel) — autonomie locale complète
4. **V4** : US3 (Diagnostic) + US11 (Erreurs) + US13 (Parcours auto) — personnalisation
5. **V5** : US12 (Reranking) + US14 (Épreuve) + US15 (Gamification) — expérience premium
