# EduNexus

**Professeur IA local** — un tuteur pédagogique basé sur RAG qui s'appuie sur vos
documents de cours (livres, PDF, notes). Interface web inspirée de NotebookLM,
optimisée pour les machines modestes (CPU).

![statut](https://img.shields.io/badge/tests-474%20pass%C3%A9s-brightgreen)

## Fonctionnalités

- **Navigation multi-espaces** — Accueil (tableau de bord), Conversations
  nommées persistantes, Bibliothèque, Apprentissage, Entraîner,
  Quiz/Examens, Progression, Explorer, Parcours, Réglages.
- **Bibliothèque de sources** — import PDF, EPUB, DOCX, PPTX avec extraction hybride :
  couche texte directe, ou OCR **Granite-Docling** pour les pages scannées
  (via llama.cpp). Indexation en arrière-plan avec statut par livre.
  Le mode file nocturne accepte plusieurs livres, les traite dans l’ordre de création
  avec un worker unique et reprend les lignes `pending` après redémarrage.
  Un fichier modifié au même chemin réutilise le livre existant ; les fragments inchangés
  bénéficient du cache d’embeddings au lieu de recalculer inutilement tous les vecteurs.
  Métadonnées de chapitre/page/section extraites automatiquement.
- **Séance de tutorat** — chat avec mode socratique (indices progressifs),
  citations cliquables vers la source et la page exacte, transcription vocale,
  barre de maîtrise par notion, reprise de session.
  **Fonctionne aussi sans aucune source sélectionnée** (réponse sans contexte documentaire).
- **Recherche hybride** — BM25 + similarité cosinus avec fusion RRF
  (Reciprocal Rank Fusion), reranking post-recherche optionnel (Jaccard lexical).
- **Résumé & fiches** — résumé hiérarchique de document, fiches de révision
  par matière avec définitions/formules/points clés, impression PDF.
- **Diagnostic initial** — quiz adaptatif de positionnement, rapport par notion
  (forces/faiblesses), parcours suggéré.
- **Parcours auto-générés** — ordonnancement pédagogique par LLM basé sur
  les lacunes du diagnostic, séquencement adaptatif des concepts.
- **Mode Épreuve** — import multi-fichiers (images/PDF), analyse OCR,
  extraction de questions numérotées, résolution RAG avec citations,
  indices progressifs (hint_level 1-3).
- **Studio** — exercices adaptatifs à difficulté choisie, quiz/examens à
  correction immédiate, résumé audio de la notion (synthèse vocale du navigateur),
  fiches mémoire, historique des notions (à revoir / maîtrisé).
- **Gamification** — système XP (quiz +20, exercice +15, streak +10),
  streak quotidien, badges débloqués, profil apprenant.
- **Validation citations** — vérification automatique des citations
  `[Livre X ...]` dans les réponses, warnings pour citations invalides.
- **Historique erreurs** — enregistrement détaillé des erreurs (quiz + exercices),
  consultation par matière/concept.
- **Catégories & corpus** — classement manuel des livres, ou **classification
  automatique par LLM** (par lots consécutifs de 25 titres, ajustable manuellement).
- **Modèles au choix** — sélecteurs Embedding + LLM dans l'en-tête ; trois moteurs :
  - **Ollama** (par défaut) — rien à configurer si Ollama tourne ;
  - **GGUF local via llama.cpp** — Granite-Docling / Granite Embedding / Granite LLM,
    chargés **séquentiellement** (1 modèle à la fois = budget RAM maîtrisé) ;
  - **OpenAI-compatible** — tout provider OpenAI-compatible (vLLM, LM Studio, etc.).
- **Embeddings bornés** — taille de lot réglable séparément de la concurrence ;
  le défaut CPU est batch 16 / concurrence 1, avec sémaphore effectif.
  La file nocturne évite de lancer plusieurs livres complets simultanément.
- **Automatisations locales** — planificateur horaire nocturne, limitation au secteur,
  durée maximale, reprise bornée des erreurs transitoires, sauvegarde SQLite,
  vérification d’intégrité et nettoyage des embeddings orphelins.
- **Préparation pédagogique différée** — optionnelle après l’indexation nocturne pour
  produire idempotemment concepts, flashcards, glossaire et relations avec le LLM local.
- **Journalisation** — toutes les erreurs (backend + navigateur) sont écrites dans
  `~/.config/ollama-tui/errors.log` avec traceback.

## Installation

Python ≥ 3.11 requis.

```bash
cd projet_ollama_tutor
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev,web]"
```

> Le moteur GGUF local nécessite en plus le binaire `llama-server`
> ([llama.cpp](https://github.com/ggml-org/llama.cpp)) et les modèles GGUF
> (Granite-Docling 258M, Granite Embedding R2, Granite 4.1 3B). Sans cela,
> EduNexus fonctionne entièrement via Ollama.

## Utilisation

L’interface web adopte une mise en page responsive orientée espace de travail : navigation latérale sur grand écran, navigation horizontale sur mobile, accueil hiérarchisé et contraste renforcé. Les identifiants DOM et les contrats WebSocket restent compatibles avec le backend existant.

## llama.cpp

Le mode GGUF local utilise un exécutable externe `llama-server` et des modèles `.gguf`. EduNexus ne télécharge pas automatiquement llama.cpp et ne nécessite pas de copier son dépôt dans le projet. Vous pouvez télécharger un binaire précompilé depuis les [releases officielles](https://github.com/ggml-org/llama.cpp/releases), ou compiler localement avec CMake. Le guide complet, incluant la configuration CPU du i5-7300U, se trouve dans [`docs/LLAMA_CPP.md`](docs/LLAMA_CPP.md).

```bash
edunexus        # ou: python -m ollama_tutor.web.__main__
```

```
✓ EduNexus démarré
  Interface web : http://127.0.0.1:9215/tutor
  (Ctrl+C pour arrêter)
```

Options : `--port` (défaut **9215**), `--host` (loopback uniquement),
`--url` (URL Ollama).

### Benchmark

```bash
MODEL=gemma4:e2b ./benchmark.sh     # Ollama live requis
```

Compare le débit (tok/s) CLI vs API EduNexus.

## Configuration

`~/.config/ollama-tui/config.json`, section `"tutor"` :

| Clé | Défaut | Rôle |
|---|---|---|
| `enabled` | `false` | active l'interface tuteur |
| `embedding_model` | `embeddinggemma` | modèle d'embeddings Ollama |
| `tutor_model` | `gemma4:e2b` | LLM du tuteur |
| `llama_bin` | `""` | chemin du binaire `llama-server` (active le moteur GGUF) |
| `embed_gguf` | `""` | GGUF Granite Embedding (dimensions auto-détectées) |
| `docling_gguf` / `docling_mmproj` | `""` | GGUF vision OCR |
| `llm_gguf` | `""` | GGUF LLM local |
| `ocr_text_threshold` / `ocr_dpi` / `pdftoppm_bin` | `32` / `150` / `pdftoppm` | ingestion hybride |
| `embed_batch_size` | `16` | fragments par requête d’embedding, borné 1–64 |
| `max_parallel_embed` | `1` | requêtes d’embedding simultanées, borné 1–8 ; commencer à 1 sur i5-7300U |
| `nightly_enabled` | `false` | active le planificateur local |
| `nightly_start_at` / `nightly_stop_at` | `23:00` / `07:00` | fenêtre horaire locale |
| `nightly_only_on_ac` | `true` | évite la charge nocturne sur batterie |
| `nightly_max_runtime_minutes` | `420` | durée maximale d’un cycle |
| `nightly_prepare_enabled` | `false` | pré-calcule concepts, flashcards et glossaire via le LLM local |

Les modèles peuvent aussi se changer depuis l'en-tête de l'interface.

Pour une indexation nocturne, importez les documents depuis la Bibliothèque : ils sont
placés en file par défaut. Les routes de contrôle sont `GET /api/tutor/index-queue`,
`POST /api/tutor/index-queue/start`, `POST /api/tutor/index-queue/stop` et
`POST /api/tutor/books/{id}/cancel`. Le planificateur se règle dans **Réglages** ou via
`PUT /api/tutor/settings`; son statut est disponible avec `GET /api/tutor/nightly`.
La maintenance manuelle utilise `POST /api/tutor/maintenance` et la reprise d’un échec
`POST /api/tutor/books/{id}/retry`. Le worker reste volontairement séquentiel par livre ;
gardez l’ordinateur alimenté et désactivez la suspension automatique pendant le traitement.

## Architecture

```
src/ollama_tutor/
├── tutor/            # cœur UI-agnostique (aucun import fastapi/textual)
│   ├── service.py    #   TutorService : import, indexation, ask, examens
│   ├── store.py      #   LibraryStore SQLite : livres, catégories, corpus
│   ├── providers/    #   interfaces + adapters : EmbeddingProvider,
│   │                 #   OCRProvider, DocumentParser, LlamaServerManager…
│   └── …             #   assessment, prompts, progress, retrieval, voice
├── web/              # FastAPI (transport fin) + tutor.html autonome
├── client.py         # client Ollama (transport injectable pour les tests)
└── config.py         # configuration persistée
```

- **474 tests** hors-ligne (`pytest tests/ -q`) — flux mockés via `httpx.MockTransport`.
- Le serveur ne lie que `127.0.0.1` ; garde same-origin sur WebSocket et requêtes mutantes.
- Un seul `llama-server` à la fois : chargement/déchargement séquentiel
  (testé par `tests/unit/test_memory_ceiling.py`).

## Licence

MIT
