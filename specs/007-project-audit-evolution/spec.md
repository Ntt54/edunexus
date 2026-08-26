# Feature 007 — Analyse et évolution du projet EduNexus

**Date** : 2026-08-26
**Statut** : Analyse (aucune modification de code)

---

## 1. Résumé de l'architecture actuelle

### 1.1 Vue d'ensemble

EduNexus est une application de tutorat intelligent locale, construite en Python 3.12 avec une architecture en 3 couches :

```
┌─────────────────────────────────────────────────────────┐
│  INTERFACE (web/server.py — FastAPI, 1894 LOC)          │
│  REST API (85+ routes) + WebSocket + SPA HTML           │
├─────────────────────────────────────────────────────────┤
│  MOTEUR TUTOR (tutor/ — UI-agnostic, ~7000 LOC)        │
│  service.py (façade) → store.py (SQLite) + models.py   │
│  assessment.py + progress.py + retrieval.py + etc.      │
├─────────────────────────────────────────────────────────┤
│  COUCHE FOURNISSEURS (providers/ — 5 ABC, ~1400 LOC)   │
│  Ollama (LLM+embed) | llama.cpp (embed+OCR)            │
│  OpenAI-compat | GGUF local                             │
├─────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE                                         │
│  client.py (httpx async) | config.py (JSON)            │
│  SQLite WAL | Embedding cache | Vector index numpy      │
└─────────────────────────────────────────────────────────┘
```

### 1.2 Stack technologique

| Composant | Technologie |
|-----------|------------|
| Langage | Python ≥ 3.11 (exécuté en 3.12) |
| Build | Hatchling (src-layout) |
| Web | FastAPI + uvicorn (127.0.0.1:9215) |
| LLM | Ollama (local) ou OpenAI-compatible ou llama-server |
| Embeddings | Ollama `/api/embed` ou GGUF via llama-server `/v1/embeddings` |
| OCR | Granite-Docling via llama-server (vision multimodale) |
| Base de données | SQLite WAL (`~/.config/ollama-tutor/tutor/library.db`) |
| Vectoriel | Numpy brute-force cosine similarity (en mémoire) |
| Frontend | SPA HTML unique (tutor.html, ~4500 lignes, vanilla JS) |
| Tests | pytest + pytest-asyncio, httpx MockTransport |

### 1.3 Composants clés

| Module | LOC | Rôle |
|--------|-----|------|
| `tutor/store.py` | 2077 | Persistance SQLite, CRUD complet pour tous les concepts |
| `tutor/service.py` | 1740 | Façade métier : orchestre ingestion, retrieval, quiz, chat |
| `tutor/models.py` | 838 | 20 dataclasses d'entités |
| `tutor/assessment.py` | 770 | Moteur de quiz, exercices, examens, notation LLM |
| `tutor/classifier.py` | 393 | Classification de domaine (règles + LLM) |
| `tutor/prompts.py` | 303 | Construction de prompts, niveaux, citations |
| `tutor/retrieval.py` | 279 | Retrieval sémantique, assemblage contexte |
| `client.py` | 279 | Client Ollama async (chat_stream, embed) |
| `tutor/extractors.py` | 221 | Extraction texte (PDF, EPUB, TXT, MD) + chunking |
| `tutor/progress.py` | 189 | Tracking maîtrise D7, détection de gaps |
| `tutor/review.py` | 149 | Répétition espacée [1,2,5,12,30] jours |
| `tutor/embeddings.py` | 115 | NumpyVectorIndex + cache embeddings |
| `tutor/voice.py` | 76 | Wrapper whisper.cpp (STT) |

### 1.4 Capacités fonctionnelles (10 vues)

| Vue | Statut | Description |
|-----|--------|-------------|
| Accueil | ✅ Fonctionnel | Dashboard avec cartes de navigation |
| Conversations | ✅ Fonctionnel | Chat RAG avec sources actives, voix, pédagogie |
| Bibliothèque | ✅ Fonctionnel | Import, arbre Domain▸Catégorie▸Documents, recherche sémantique |
| Apprentissage | ✅ Fonctionnel | Flashcards, révision espacée, préparation de cours |
| Entraînement | ✅ Fonctionnel | Exercices adaptatifs, indices progressifs |
| Quiz | ✅ Fonctionnel | Quiz + Examens chronométrés (5 types de questions) |
| Progression | ✅ Fonctionnel | Maîtrise par concept, gaps, parcours |
| Explorer | ✅ Fonctionnel | Localisation concepts, comparaison multi-livres, glossaire |
| Réglages | ✅ Fonctionnel | Configuration inférence, fournisseur, pédagogie |
| Parcours | ✅ Fonctionnel | Parcours d'apprentissage avec étapes |

### 1.5 Pipeline RAG complet

```
Document → Extraction texte → Chunking (1200 chars, overlap 200)
  → Embedding (Ollama/GGUF, cache SHA-256) → SQLite (chunks + embeddings)
  → Requête utilisateur → Embedding query → Cosine similarity (top-k, floor 0.25)
  → Assemblage contexte (6000 chars max) → Prompt avec citations → Streaming LLM
```

---

## 2. Problèmes détectés

### 2.1 RAG — Problèmes critiques

| # | Problème | Impact | Sévérité |
|---|----------|--------|----------|
| R1 | **Pas de reranking** — recherche pure cosine, aucun cross-encoder ni MMR | Les résultats pertinents noyés dans du bruit pour les grandes bibliothèques | Haute |
| R2 | **Chunking aveugle aux structures** — ignore paragraphes, titres, sections | Fragmente les concepts, perte de contexte inter-paragraphes | Haute |
| R3 | **Pas de recherche hybride** — sémantique OU lexical, jamais les deux | Requêtes précises (noms propres, termes techniques) mal servies | Haute |
| R4 | **Pas de validation des citations** — le LLM peut inventer des références | Perte de confiance de l'apprenant | Moyenne |
| R5 | **Métadonnées chapitre/page souvent NULL** — chunking ne les extrait pas | Citations `[Livre X]` sans chapitre/page = peu utiles | Moyenne |
| R6 | **Search brute-force O(n)** — pas d'index approximatif (FAISS/HNSW) | Dégradation avec grandes bibliothèques (milliers de chunks) | Moyenne |
| R7 | **Overlap fixe de 200 chars** — non adaptatif | Trop petit pour des textes denses, trop grand pour du code | Basse |

### 2.2 Documents — Problèmes

| # | Problème | Impact | Sévérité |
|---|----------|--------|----------|
| D1 | **4 formats seulement** (TXT, MD, PDF, EPUB) — pas de DOCX, PowerPoint, CSV | Impossibilité d'importer des supports courants | Haute |
| D2 | **Pas de détection de doublons au niveau chunk** | Bibliothèque gonfle inutilement | Basse |
| D3 | **Pas de gestion des documents supprimés/modifiés** | Bibliothèque se dégrade avec le temps | Moyenne |
| D4 | **OCR dépend de poppler (pdftoppm)** — pas toujours installé | Échec silencieux sur machines sans poppler | Moyenne |
| D5 | **Pas de traitement de gros documents** — tout est chargé en mémoire | Risque OOM sur des livres de 500+ pages | Moyenne |

### 2.3 Expérience apprenant — Lacunes

| # | Problème | Impact | Sévérité |
|---|----------|--------|----------|
| E1 | **Pas de résumé de chapitre** — l'apprenant ne peut pas obtenir un résumé ciblé | Perd du temps à lire des sections entières | Haute |
| E2 | **Pas de fiches de révision** — les flashcards existent mais pas de génération de fiches synthétiques | Pas de support de révision rapide | Haute |
| E3 | **Pas de détection proactive des notions mal comprises** — les gaps ne sont révélés que par les exercices | L'apprenant ne sait pas ce qu'il ne sait pas | Haute |
| E4 | **Pas de progression guidée** — les parcours sont manuels, pas auto-générés | L'apprenant doit organiser son propre parcours | Moyenne |
| E5 | **Pas d'historique des erreurs** détaillé — on sait qu'il a échoué, pas pourquoi | Difficulté à cibler les révisions | Moyenne |
| E6 | **Pas d'explication adaptée au niveau** en mode examen | Même dans les exercices, l'adaptation est basique | Basse |
| E7 | **Mode révision pas gamifié** — pas de streaks, badges, encourageant | Motivation à long terme difficile | Basse |

### 2.4 Architecture — Problèmes

| # | Problème | Impact | Sévérité |
|---|----------|--------|----------|
| A1 | **`server.py` fait 1894 LOC** — trop gros, mélange route et logique métier | Maintenance difficile, violations occasionnelles du principe I (UI-framework-free) | Moyenne |
| A2 | **Pas de streaming LLM pour llama-server** — `LLMProvider.generate()` est synchrone | Impossible d'utiliser llama-server pour le chat en mode stream | Haute |
| A3 | **Pas de `GGUFLLMProvider`** — la config `tutor_llm_gguf` existe sans consommateur | Ne peut pas faire tourner le LLM entièrement en local sans Ollama | Haute |
| A4 | **Embedding model fixe par sujet** — changer de modèle nécessite réindexer tous les livres | Friction pour l'expérimentation | Basse |
| A5 | **Pas de `--parallel N` pour llama-server embeddings** | Embeddings séquentiels, sous-utilisation du CPU | Moyenne |

### 2.5 IA Locale — Problèmes

| # | Problème | Impact | Sévérité |
|---|----------|--------|----------|
| L1 | **Pas de cache de modèle** — les modèles GGUF sont rechargés à chaque démarrage de llama-server | Temps de démarrage long | Moyenne |
| L2 | **Un seul llama-server à la fois** (max_servers=1) — OCR et embeddings ne peuvent pas tourner en parallèle | Rallonge l'import de documents avec OCR | Moyenne |
| L3 | **Pas de déchargement automatique des modèles** — le serveur tourne même inactif | Consomme de la RAM inutilement | Basse |
| L4 | **Pas de quantification adaptative** — pas de choix entre Q4, Q8, etc. selon les ressources | Sur machines très limitées, le modèle peut être trop gros | Basse |

---

## 3. Améliorations recommandées

### 3.1 RAG — Améliorations prioritaires

| # | Amélioration | Description | Impact |
|---|-------------|-------------|--------|
| AR1 | **Reranking sémantique** | Ajouter un cross-encoder léger (ex: `cross-encoder/ms-marco-MiniLM-L-6-v2` via sentence-transformers ou un modèle GGUF dédié) après la recherche cosinus. Réduit le bruit de 40-60%. | Élevé |
| AR2 | **Chunking structurel** | Respecter les paragraphes, titres, sections. Détecter les sauts de section et les utiliser comme frontières de chunk. Préserver les métadonnées de chapitre/page. | Élevé |
| AR3 | **Recherche hybride (BM25 + sémantique)** | Combiner la recherche lexicale (BM25/TF-IDF, déjà en place via `locate()`) avec la recherche sémantique. Score hybride par réfusion (RRF ou pondéré). | Élevé |
| AR4 | **Validation des citations post-génération** | Vérifier que les références citées par le LLM existent réellement dans les sources. Signaler les citations invalides. | Moyen |
| AR5 | **Extraction automatique de métadonnées** | Pendant le chunking, extraire les titres de section, numéros de page, numéros de chapitre. Préserver ces infos dans les chunks. | Moyen |

### 3.2 Expérience apprenant — Améliorations

| # | Amélioration | Description | Impact |
|---|-------------|-------------|--------|
| AE1 | **Résumé de chapitre/doc** | Fonction "Résumer" sur un livre/chapitre. Utilise RAG + LLM pour générer un résumé structuré. | Élevé |
| AE2 | **Fiches de révision auto-générées** | À partir d'un chapitre/sujet, générer une fiche synthétique (définitions, formules, points clés). Format imprimable. | Élevé |
| AE3 | **Diagnostic de connaissances** | Quiz de positionnement initial qui mappe les forces/faiblesses. Propose un parcours personnalisé. | Élevé |
| AE4 | **Parcours auto-générés** | À partir des gaps détectés, générer automatiquement un parcours d'apprentissage avec séquence optimale. | Moyen |
| AE5 | **Historique des erreurs détaillé** | Pour chaque mauvaise réponse : quoi, pourquoi, la bonne réponse, la source. Pattern d'erreur récurrent détecté. | Moyen |
| AE6 | **Mode révision gamifié** | Streaks quotidiens, badges de maîtrise, niveau d'apprenant global, défis hebdomadaires. | Basse |

### 3.3 Gestion des documents — Améliorations

| # | Amélioration | Description | Impact |
|---|-------------|-------------|--------|
| AD1 | **Support DOCX/PPTX** | Utiliser python-docx et python-pptx pour extraire le texte des documents Office. | Élevé |
| AD2 | **Gestion des documents modifiés/supprimés** | Détecter les fichiers supprimés du disque, marquer les livres comme "orphelins". Proposer réindexation. | Moyen |
| AD3 | **Traitements des gros documents** | Chunking lazy/streaming au lieu de tout charger en mémoire. Pagination de l'indexation. | Moyen |
| AD4 | **Détection automatique des catégories** | Améliorer la classification existante avec les domaines éducatifs (maths, info, sciences, langues) + sous-catégories. | Moyen |

### 3.4 Mode Épreuve — Améliorations

| # | Amélioration | Description | Impact |
|---|-------------|-------------|--------|
| EE1 | **Import multi-pages d'un examen** | Upload plusieurs images/PDF d'un même sujet. Le système les traite en séquence. | Élevé |
| EE2 | **Analyse automatique des questions** | Détecter chaque question dans le document, identifier les notions, proposer une résolution étape par étape. | Élevé |
| EE3 | **Résolution guidée** | Au lieu de donner la réponse, guider l'apprenant question par question avec des indices progressifs. | Moyen |
| EE4 | **Comparaison avec la correction** | Si l'apprenant a sa propre correction, comparer et expliquer les différences. | Basse |

### 3.5 IA Locale — Optimisations

| # | Optimisation | Description | Impact |
|---|-------------|-------------|--------|
| OL1 | **llama-server `--parallel N` pour embeddings** | Paralléliser les calculs d'embeddings. Config `max_parallel_embed` déjà en place. | Élevé |
| OL2 | **GGUFLLMProvider avec streaming** | Implémenter un fournisseur LLM local via llama-server `/v1/chat/completions` avec streaming SSE. | Élevé |
| OL3 | **Cache de modèle GGUF** | Garder le modèle chargé entre les requêtes, décharger après inactivité configurable. | Moyen |
| OL4 | **OCR sans poppler** | Utiliser PyMuPDF (fitz) pour le rasterization PDF au lieu de pdftoppm. Dépendance Python au lieu d'outil système. | Moyen |
| OL5 | **Multi-processus llama-server** | Permettre 2 serveurs simultanés (embeddings + OCR) avec un port chacun. | Basse |

---

## 4. Nouvelles fonctionnalités proposées

### F1 — Assistant de révision intelligent

| | |
|---|---|
| **Description** | Mode dédié qui analyse l'historique de l'apprenant (quiz, exercices, flashcards) et génère des sessions de révision ciblées. Suggère quoi réviser en priorité, propose des mini-quiz de rappel, et adapte la difficulté. |
| **Problème résolu** | L'apprenant ne sait pas quoi réviser ni quand. La répétition espacée est passive (flashcards fixes). |
| **Valeur** | Transforme la révision d'une tâche passive en un parcours actif et guidé. |
| **Difficulté** | Moyenne — les données de progression existent, il faut les exploiter. |
| **Impact perf.** | Faible — calculs légers + un LLM call par session. |
| **Priorité** | P1 |

### F2 — Générateur de fiches de révision

| | |
|---|---|
| **Description** | À partir d'un chapitre, d'un livre ou d'un sujet, générer une fiche de révision synthétique : définitions, formules, points clés, schémas conceptuels. Exportable en HTML imprimable. |
| **Problème résolu** | Pas de support de révision rapide. L'apprenant doit tout relire. |
| **Valeur** | Gain de temps massif pour la révision avant un exam. |
| **Difficulté** | Faible — prompt engineering + le contexte RAG existe déjà. |
| **Impact perf.** | Faible — un LLM call par génération. |
| **Priorité** | P1 |

### F3 — Diagnostic initial personnalisé

| | |
|---|---|
| **Description** | À la création d'un sujet, proposer un quiz de positionnement (10-15 questions adaptatives). Détermine le niveau initial, identifie les acquis et les lacunes. Génère un plan d'apprentissage personnalisé. |
| **Problème résolu** | L'apprenant commence sans knowing son niveau. Pas de point de départ clair. |
| **Valeur** | Personnalisation immédiate de l'expérience. |
| **Difficulté** | Moyenne — le moteur de quiz existe, il faut l'adapter en mode adaptatif. |
| **Impact perf.** | Faible — 10-15 questions = 10-15 LLM calls au démarrage. |
| **Priorité** | P1 |

### F4 — Résumé automatique de document

| | |
|---|---|
| **Description** | Bouton "Résumer" sur un livre ou chapitre. Utilise les chunks RAG pour construire un résumé hiérarchique (résumé global → sections → points clés). |
| **Problème résolu** | Impossibilité d'obtenir un aperçu rapide d'un document long. |
| **Valeur** | Économie de temps, aide à la décision (vaut-il la peine de lire en détail ?). |
| **Difficulté** | Faible — RAG + prompt de résumé. |
| **Impact perf.** | Moyen — peut nécessiter plusieurs LLM calls pour les gros documents. |
| **Priorité** | P1 |

### F5 — Mode Épreuve complet

| | |
|---|---|
| **Description** | Import multi-pages (images + PDF) → analyse OCR → détection des questions → résolution guidée ou complète avec citations. L'apprenant peut demander des explications supplémentaires par question. |
| **Problème résolu** | L'import d'examens fonctionne mais le mode résolution est basique. |
| **Valeur** | Transforme un examen papier en session d'apprentissage interactive. |
| **Difficulté** | Élevée — OCR + détection structure + résolution multi-étapes. |
| **Impact perf.** | Élevé — OCR est coûteux en CPU. |
| **Priorité** | P2 |

### F6 — Recherche hybride BM25 + sémantique

| | |
|---|---|
| **Description** | Combiner la recherche lexicale existante (`locate()`) avec la recherche sémantique (`retrieve()`) en un score hybride. Réfusion par Reciprocal Rank Fusion (RRF). |
| **Problème résolu** | Les termes techniques, noms propres, acronymes sont mal servis par la seule sémantique. |
| **Valeur** | Amélioration significative de la pertinence pour les requêtes précises. |
| **Difficulté** | Moyenne — BM25 est déjà implémenté dans `locate()`, il faut fusionner. |
| **Impact perf.** | Faible — les deux chemins existent, juste combiner les scores. |
| **Priorité** | P1 |

### F7 — Reranking post-recherche

| | |
|---|---|
| **Description** | Après la recherche cosinus, appliquer un reranker léger pour réordonner les résultats. Options : cross-encoder léger ou reranking basé sur LLM. |
| **Problème résolu** | Les résultats cosine brute contiennent du bruit. |
| **Valeur** | Améliore la qualité du contexte injecté dans le prompt, donc la qualité des réponses. |
| **Difficulté** | Moyenne — nécessite un modèle léger additionnel. |
| **Impact perf.** | Moyen — un modèle léger de reranking ajoute 10-50ms par requête. |
| **Priorité** | P2 |

### F8 — Support DOCX/PPTX

| | |
|---|---|
| **Description** | Ajouter le support des documents Microsoft Word et PowerPoint via python-docx et python-pptx. Extraction du texte, images, tableaux. |
| **Problème résolu** | 40%+ des documents pédagogiques sont en DOCX/PPTX. |
| **Valeur** | Élargit considérablement l'utilisabilité. |
| **Difficulté** | Faible-moyenne — bibliothèques bien documentées. |
| **Impact perf.** | Faible — extraction texte classique. |
| **Priorité** | P1 |

### F9 — Parcours auto-générés

| | |
|---|---|
| **Description** | À partir des gaps détectés + le diagnostic initial, générer automatiquement un parcours d'apprentissage ordonné. Séquence pédagogique basée sur les dépendances entre concepts. |
| **Problème résolu** | Les parcours sont entièrement manuels. |
| **Valeur** | L'apprenant n'a pas à organiser son propre apprentissage. |
| **Difficulté** | Élevée — nécessite un graphe de dépendances entre concepts. |
| **Impact perf.** | Faible — calcul une seule fois. |
| **Priorité** | P2 |

### F10 — GGUFLLMProvider (LLM local complet)

| | |
|---|---|
| **Description** | Implémenter un fournisseur LLM local via llama-server `/v1/chat/completions` avec streaming SSE. Permet de faire tourner l'application entièrement sans Ollama. |
| **Problème résolu** | La config `tutor_llm_gguf` existe sans consommateur. L'app nécessite Ollama pour l'inférence. |
| **Valeur** | Autonomie totale — pas besoin d'Ollama installé. |
| **Difficulté** | Moyenne — le pattern existe déjà dans `openai_compat.py`. |
| **Impact perf.** | Faible — c'est juste un nouveau fournisseur. |
| **Priorité** | P1 |

### F11 — Historique d'erreurs intelligent

| | |
|---|---|
| **Description** | Enregistrer chaque erreur (quiz, exercice) avec : la question, la réponse donnée, la bonne réponse, la source utilisée. Détecter les patterns d'erreurs récurrents. Proposer des sessions de révision ciblées. |
| **Problème résolu** | On sait que l'apprenant a échoué, mais pas pourquoi ni quoi réviser. |
| **Valeur** | Feedback actionnable et personnalisé. |
| **Difficulté** | Moyenne — les données existent dans `exercise_attempts`, il faut les analyser. |
| **Impact perf.** | Faible — calculs légers. |
| **Priorité** | P2 |

### F12 — Intégration de schémas/diagrammes

| | |
|---|---|
| **Description** | Extraire et préserver les images, schémas, diagrammes des documents PDF. Les associer aux chunks de texte pertinents. Les afficher dans les réponses du tuteur. |
| **Problème résolu** | Les documents contiennent des informations visuelles critiques (schémas, organigrammes, graphiques) qui sont perdues. |
| **Valeur** | Compréhension visuelle essentielle pour les sciences et l'ingénierie. |
| **Difficulté** | Élevée — extraction d'images, indexation, affichage. |
| **Impact perf.** | Élevé — stockage et traitement d'images. |
| **Priorité** | P3 |

### F13 — Mode collaboratif (sharing)

| | |
|---|---|
| **Description** | Partager des parcours, quiz, ou fiches de révision entre utilisateurs locaux. Export/import de configurations pédagogiques. |
| **Problème résolu** | Chaque utilisateur recrée tout de zéro. Pas de partage entre apprenants. |
| **Valeur** | Mutualisation des efforts pédagogiques. |
| **Difficulté** | Élevée — nécessite un format d'échange et une API de sync. |
| **Impact perf.** | Faible. |
| **Priorité** | P3 |

---

## 5. Priorisation

### P0 — Indispensable (corrections critiques)

Aucun — le projet fonctionne correctement. Pas de bug bloquant identifié.

### P1 — Fortement recommandé (impact important)

| # | Fonctionnalité | Effort estimé |
|---|---------------|---------------|
| AE1 | Résumé de chapitre/doc | 1-2 jours |
| AE2 | Fiches de révision auto-générées | 1-2 jours |
| AE3 | Diagnostic initial personnalisé | 2-3 jours |
| AR2 | Chunking structurel | 2-3 jours |
| AR3 | Recherche hybride BM25 + sémantique | 2-3 jours |
| AD1 | Support DOCX/PPTX | 1-2 jours |
| F10 | GGUFLLMProvider (LLM local) | 2-3 jours |
| OL1 | llama-server --parallel N (embeddings) | 1 jour |
| AR5 | Extraction métadonnées chapitre/page | 1-2 jours |
| AR4 | Validation citations | 1 jour |
| AE5 | Historique erreurs détaillé | 1-2 jours |

**Total estimé P1** : 15-23 jours

### P2 — Intéressant (utile mais non prioritaire)

| # | Fonctionnalité | Effort estimé |
|---|---------------|---------------|
| F7 | Reranking post-recherche | 2-3 jours |
| F9 | Parcours auto-générés | 3-5 jours |
| F5 | Mode Épreuve complet | 5-7 jours |
| AD2 | Gestion documents modifiés | 1-2 jours |
| AD3 | Gros documents (lazy loading) | 2-3 jours |
| OL4 | OCR sans poppler (PyMuPDF) | 1-2 jours |
| AE6 | Gamification | 3-5 jours |

**Total estimé P2** : 17-27 jours

### P3 — Expérimental (futures itérations)

| # | Fonctionnalité | Effort estimé |
|---|---------------|---------------|
| F12 | Schémas/diagrammes | 5-7 jours |
| F13 | Mode collaboratif | 5-10 jours |
| OL5 | Multi-processus llama-server | 2-3 jours |
| F11 | Historique erreurs intelligent | 2-3 jours |

**Total estimé P3** : 14-23 jours

---

## 6. Risques techniques

| Risque | Probabilité | Impact | Mitigation |
|--------|------------|--------|------------|
| Reranking ajoute latence perceptible | Moyenne | Moyen | Utiliser un modèle léger (< 100MB), mode optionnel |
| Chunking structurel casse la compatibilité existante | Faible | Élevé | Migration des chunks existants, re-indexation optionnelle |
| DOCX/PPTX introduit des dépendances lourdes | Faible | Faible | Optional extras (`pip install ollama-tutor[office]`) |
| GGUFLLMProvider en basse mémoire (< 4GB) | Moyenne | Moyen | Documenter les minimums, fallback automatique vers Ollama |
| Recherche hybride augmente la complexité du code RAG | Moyenne | Faible | Architecture modulaire, les deux chemins existent déjà |
| OCR multi-pages épuise le CPU | Élevé | Moyen | Limiter le nombre de pages, progressif, annulable |

---

## 7. Optimisations possibles

| Optimisation | Domaine | Gain attendu | Complexité |
|-------------|---------|-------------|-----------|
| Chunking par paragraphes (NLTK/sentences) | RAG | +30% pertinence | Faible |
| Embeddings batch parallèles (llama-server --parallel) | Performance | x2-x4 vitesse indexation | Faible |
| Cache SQLite WAL avec PRAGMA optimize | Stockage | -20% IO | Faible |
| Index numpy → FAISS/Annoy pour grandes bibliothèques | RAG | O(n) → O(log n) | Moyenne |
| Lazy loading des chunks (streaming) | Mémoire | -50% RAM sur gros docs | Moyenne |
| Prompt caching (system prompt identique entre requêtes) | LLM | -30% tokens | Faible |
| Chunk embedding incrémental (pas de ré-indexation complète) | Performance | -80% temps ré-indexation | Élevée |

---

## 8. Proposition d'évolution globale

### Phase 7 (Court terme, 2-3 semaines) — Fondations pédagogiques

1. **Résumé de document** (AE1) — Quick win, haute valeur
2. **Fiches de révision** (AE2) — Quick win, haute valeur
3. **Chunking structurel** (AR2) — Améliore tout le RAG
4. **Support DOCX/PPTX** (AD1) — Élargit l'utilisabilité
5. **Historique erreurs** (AE5) — Prépare la personnalisation

### Phase 8 (Moyen terme, 3-4 semaines) — Intelligence RAG

6. **Recherche hybride** (AR3) — Amélioration majeure de pertinence
7. **GGUFLLMProvider** (F10) — Autonomie locale complète
8. **llama-server parallel** (OL1) — Performance
9. **Diagnostic initial** (AE3) — Personnalisation
10. **Validation citations** (AR4) — Confiance

### Phase 9 (Long terme, 4-6 semaines) — Expérience avancée

11. **Reranking** (F7) — Qualité premium
12. **Parcours auto-générés** (F9) — Apprentissage adaptatif
13. **Mode Épreuve complet** (F5) — Feature signature
14. **Gamification** (AE6) — Engagement

### Phase 10 (Futur) — Innovation

15. **Schémas/diagrammes** (F12) — Compréhension visuelle
16. **Mode collaboratif** (F13) — Partage
17. **Multi-processus llama-server** (OL5) — Performance avancée
