# EduNexus

**Professeur IA local** — un tuteur pédagogique basé sur RAG qui s'appuie sur vos
documents de cours (livres, PDF, notes). Interface web inspirée de NotebookLM,
optimisée pour les machines modestes (CPU).

![statut](https://img.shields.io/badge/tests-209%20pass%C3%A9s-brightgreen)

## Fonctionnalités

- **Bibliothèque de sources** — import PDF avec extraction hybride :
  couche texte directe, ou OCR **Granite-Docling** pour les pages scannées
  (via llama.cpp). Indexation en arrière-plan avec statut par livre.
- **Séance de tutorat** — chat avec mode socratique (indices progressifs),
  citations cliquables vers la source et la page exacte, transcription vocale,
  barre de maîtrise par notion, reprise de session.
  **Fonctionne aussi sans aucune source sélectionnée** (réponse sans contexte documentaire).
- **Studio** — exercices adaptatifs à difficulté choisie, quiz/examens à
  correction immédiate, résumé audio de la notion (synthèse vocale du navigateur),
  fiches mémoire, historique des notions (à revoir / maîtrisé).
- **Catégories & corpus** — classement manuel des livres, ou **classification
  automatique par LLM** (par lots consécutifs de 25 titres, ajustable manuellement).
- **Modèles au choix** — sélecteurs Embedding + LLM dans l'en-tête ; deux moteurs :
  - **Ollama** (par défaut) — rien à configurer si Ollama tourne ;
  - **GGUF local via llama.cpp** — Granite-Docling / Granite Embedding / Granite LLM,
    chargés **séquentiellement** (1 modèle à la fois = budget RAM maîtrisé).
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

Les modèles peuvent aussi se changer depuis l'en-tête de l'interface.

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

- **209 tests** hors-ligne (`pytest tests/ -q`) — flux mockés via `httpx.MockTransport`.
- Le serveur ne lie que `127.0.0.1` ; garde same-origin sur WebSocket et requêtes mutantes.
- Un seul `llama-server` à la fois : chargement/déchargement séquentiel
  (testé par `tests/unit/test_memory_ceiling.py`).

## Licence

MIT
