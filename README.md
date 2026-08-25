# EduNexus

**Professeur IA local** — un tuteur pédagogique qui travaille sur **vos** documents
(cours, livres, PDF) : RAG sourcé, exercices adaptatifs, quiz corrigés, fiches de
révision et répétition espacée. Conçu pour les machines modestes (8–16 Go RAM).

![statut](https://img.shields.io/badge/tests-209_passing-brightgreen)

## Fonctionnalités

### Interface « NotebookLM » en 3 colonnes
- **Sources** — import PDF, regroupement par matière, statut d'indexation en direct,
  cases d'inclusion/exclusion du contexte, recherche sémantique
- **Séance de tutorat** — mode socratique (indices progressifs), citations cliquables
  vers la page exacte, dictée vocale (whisper), barre de maîtrise par notion,
  reprise de session
- **Studio** — exercices adaptatifs à difficulté choisie, quiz/examens corrigés,
  résumé audio de la notion (synthèse vocale navigateur), fiches mémo, historique

### Moteur IA à deux modes
| Mode | Embeddings | Génération | OCR |
|---|---|---|---|
| **Ollama** (défaut) | via serveur Ollama | via serveur Ollama | — |
| **GGUF local** | Granite Embedding (llama.cpp) | Granite Instruct (llama.cpp) | Granite-Docling |

- Le moteur GGUF charge **un seul modèle à la fois** (budget RAM garanti par test)
- Repli automatique vers Ollama si rien n'est configuré
- Dimensions d'embedding **auto-détectées** (jamais codées en dur), ré-indexation gérée

### Organisation pédagogique
- Matières, **catégories et corpus** (une source peut appartenir à plusieurs)
- **Classification automatique des livres par LLM** (par lots consécutifs),
  ajustable manuellement
- Mode examen avec documents temporaires (cycle de vie dédié, purge automatique)

### Robustesse
- Journalisation complète des erreurs backend **et** navigateur
  → `~/.config/ollama-tui/errors.log`
- Garde same-origin (WebSocket + requêtes mutantes), écoute loopback uniquement

## Installation

Prérequis : Python ≥ 3.11, [Ollama](https://ollama.com) en cours d'exécution.

```bash
cd projet_ollama_tutor
python3 -m venv venv
venv/bin/pip install -e ".[dev,web]"
```

## Utilisation

```bash
edunexus            # si installé globalement, ou :
venv/bin/python -m ollama_tutor.web.__main__
# ✓ EduNexus démarré
#   Interface web : http://127.0.0.1:9215/tutor
```

Options : `--port` (défaut **9215**), `--host` (loopback uniquement), `--url <serveur Ollama>`.

Benchmark de débit (Ollama requis) :

```bash
MODEL=gemma4:e2b ./benchmark.sh
```

## Configuration

`~/.config/ollama-tui/config.json`, section `"tutor"` :

```jsonc
{
  "tutor": {
    "enabled": true,
    "embedding_model": "ibm/granite-embedding:107m-multilingual-q8_0",
    "tutor_model": "gemma4:e2b",
    "socratic": true,
    "level": "intermediate",
    "top_k": 5,

    // Moteur 100 % local (optionnel — sinon repli Ollama automatique)
    "llama_bin": "/chemin/vers/llama-server",
    "embed_gguf": "granite-embedding-Q8_0.gguf",
    "docling_gguf": "granite-docling-258M-Q5_K_M.gguf",
    "docling_mmproj": "mmproj-granite-docling-f16.gguf",
    "llm_gguf": "granite-4.1-3b-instruct-Q4_K_M.gguf",

    // Voix (dictée)
    "whisper_binary": "/chemin/vers/whisper",
    "whisper_model": "base"
  }
}
```

Les modèles GGUF relatifs sont résolus depuis `llama_models_dir`.

## Tests

```bash
venv/bin/python -m pytest tests/ -q        # 209 tests, < 30 s, hors-ligne
```

Aucun test ne contacte de serveur réel : les flux Ollama sont simulés via
transport httpx injectable.

## Structure

```
src/ollama_tutor/
├── tutor/
│   ├── service.py          # orchestration (import async, ask, examens)
│   ├── store.py            # SQLite : livres, catégories, corpus, temp-docs
│   ├── retrieval.py        # recherche vectorielle sourcée
│   ├── assessment.py       # exercices / quiz / examens
│   ├── progress.py         # maîtrise, lacunes, répétition espacée
│   └── providers/          # interfaces + llama.cpp + adapters Ollama
├── web/
│   ├── server.py           # FastAPI (transport fin, zéro logique métier)
│   └── static/tutor.html   # interface autonome (CSS/JS inline)
├── client.py               # client Ollama streaming (transport injectable)
└── config.py               # configuration persistée
```

## Licence

MIT
