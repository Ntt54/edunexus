<!--
SYNC IMPACT REPORT
==================
Version : (aucune antérieure) → 1.0.0
Principes : création initiale — I. Cœur découplé, II. Préservation fonctionnelle,
            III. Tests hors-ligne d'abord, IV. Sécurité locale par défaut,
            V. Légèreté & simplicité, VI. Observabilité des erreurs
Sections ajoutées : Core Principles, Contraintes Techniques,
                    Workflow de Développement, Governance
Sections supprimées : —
TODOs différés : aucun
Intents non-gouvernance détectés dans l'entrée utilisateur :
  - Refonte progressive de l'interface EduNexus (navigation multi-vues,
    conversations persistantes, tableau de bord, préparation des parcours)
    → différé, voir section Next Actions du résumé (/speckit.specify)
-->

# Constitution EduNexus

Constitution gouvernante pour le projet **EduNexus** (tuteur IA local autonome).
Elle prime sur toute autre pratique de développement du dépôt.

## Core Principles

### I. Cœur métier découplé de l'interface (NON NÉGOCIABLE)

Toute logique métier (indexation, RAG, évaluations, progression, stockage,
providers IA) vit dans `src/ollama_tutor/tutor/` et ses modules associés
(`client.py`, `config.py`, `models.py`) et ne doit importer ni `fastapi`,
ni `textual`, ni aucune bibliothèque d'interface. Le serveur web
(`web/server.py`) est une couche de transport fine : il ne duplique aucune
logique de fenêtrage, de persistance ou d'appel modèle — il délègue aux
services. Les frontières sont vérifiées par
`tests/contract/test_tutor_imports.py`.

**Rationale** : le découplage permet de faire évoluer l'interface (refontes,
multi-vues, futures applications clientes) sans toucher au moteur, et
réciproquement.

### II. Préservation fonctionnelle et évolution progressive

Aucune fonctionnalité existante ne doit être supprimée ou dégradée sans
nécessité documentée. Les évolutions — notamment d'interface — se font par
réutilisation des endpoints, services et composants existants ; une refonte
ne part jamais d'une reconstruction complète. Toute nouvelle vue doit rester
compatible avec la logique déjà implémentée (backend, RAG, embeddings,
sources) et s'intégrer dans l'identité visuelle existante. L'architecture
doit rester ouverte aux extensions futures (ex. parcours d'apprentissage
structurés) sans les implémenter prématurément.

**Rationale** : chaque fonctionnalité a été validée par des tests et par un
usage réel ; la régression est plus coûteuse que l'accumulation.

### III. Tests hors-ligne d'abord

Toute correction ou fonctionnalité est couverte par des tests exécutables
hors-ligne : les flux Ollama/llama.cpp sont simulés via transport httpx
injectable (`httpx.MockTransport`), jamais contre un démon réel. Les tests
asynchrones portent un marqueur explicite `@pytest.mark.asyncio`. La suite
complète doit passer avant tout push ; tout bug corrigé reçoit un test de
régression qui aurait échoué avant le correctif. Les frontières d'architecture
(I) sont elles-mêmes testées par contrat.

**Rationale** : la suite (200+ tests, < 30 s) est le filet de sécurité qui
rend possibles les principes I et II ; dépendre d'un démon externe la rendrait
lente et non déterministe.

### IV. Sécurité locale par défaut

Le serveur ne lie que `127.0.0.1` (jamais d'écoute réseau étendue). Toute
montée WebSocket et toute requête HTTP mutante valide les en-têtes `Origin`
et `Host` contre une liste d'autorisation localhost ; les origines étrangères
sont rejetées (403 / fermeture 1008). Aucun secret n'est commité dans le
dépôt. Les données utilisateur restent hors du dépôt
(`~/.config/ollama-tui/`). Le budget mémoire est contractuel : un seul
`llama-server` actif à la fois, arrêté proprement à l'extinction.

**Rationale** : EduNexus traite des documents personnels ; la surface
d'attaque doit rester minimale par construction, pas par configuration.

### V. Légèreté et simplicité (YAGNI)

Priorité à la bibliothèque standard ; aucune nouvelle dépendance d'exécution
sans justification écrite (cas d'usage + alternative rejetée). L'interface
reste un fichier autonome (CSS/JS inline, zéro asset externe, zéro framework),
cohérente avec le design papier/encre existant. La complexité doit être
justifiée à la revue ; « simple et suffisant » prime sur « extensible mais
prématuré ». Les dimensions d'embedding sont auto-détectées, jamais codées en
dur.

**Rationale** : cible matérielle CPU/modestes (8–16 Go RAM) ; chaque
dépendance ou abstraction non utilisée coûte en RAM, en maintenance et en
surface de panne.

### VI. Observabilité des erreurs

Toute exception backend est journalisée avec traceback dans
`~/.config/ollama-tui/errors.log` (helper `_log_error`, qui ne doit jamais
provoquer d'échec secondaire). Toute erreur côté navigateur est rapportée via
`POST /api/log-error` et apparaît dans le même journal. Aucune erreur ne doit
être silencieuse : l'utilisateur voit un message clair, le développeur trouve
la cause dans le journal. Les statuts visibles (indexation, progression)
suivent le même principe : l'état réel est toujours affiché.

**Rationale** : le diagnostic à distance repose entièrement sur ce journal ;
il a fait ses preuves (cause racine d'un import mort identifiée en une
commande).

## Contraintes Techniques

- Python ≥ 3.11 ; dépendances d'exécution : `httpx`, `numpy`, `pypdf`,
  `Pillow`, `python-multipart` ; extras : `fastapi`/`uvicorn[standard]`
  (web), `pytest`/`pytest-asyncio`/`jsonschema` (dev).
- Moteur GGUF : binaire `llama-server` (llama.cpp) piloté en sous-processus
  exclusif (`LlamaServerManager`, `max_servers=1`) ; démarrage paresseux —
  aucun processus ni connexion à la construction des providers.
- Ingestion hybride PDF : couche texte via pypdf, pages scannées via OCR
  Granite-Docling (rasterisation `pdftoppm`) ; indexation en arrière-plan
  avec statut par livre (`indexing|ready|error`).
- Stockage : SQLite dans le répertoire de configuration
  (`~/.config/ollama-tui/`) ; migrations idempotentes suivant le style
  PRAGMA-table_info existant ; vecteurs NumPy float32.
- Port par défaut **9215** ; point d'entrée console `edunexus`.
- Embeddings : dimensions auto-détectées au premier appel, persistées ;
  changement de modèle ⇒ ré-indexation.

## Workflow de Développement

1. Toute fonctionnalité suit le flux Spec Kit : `/speckit.specify` → plan →
   tasks → implémentation, avec artefacts dans `specs/<feature-id>/`.
2. Avant toute modification importante : analyser l'existant, identifier les
   composants réutilisables, privilégier la solution la plus simple
   (principe II).
3. Gates obligatoires : suite complète verte (`pytest tests/ -q`),
   `node --check` sur le script inline de `tutor.html` après toute
   modification UI, vérification croisée des endpoints référencés.
4. Après modification de `client.py` ou des chemins de rendu : relancer
   `./benchmark.sh` (parité de débit vs CLI = gate de régression).
5. Commits atomiques à message descriptif (`feat:`, `fix:`, `docs:`) ;
   revue de conformité à cette constitution sur chaque changement.

## Governance

- Cette constitution prime sur toute autre pratique, convention ou document
  du dépôt en cas de conflit.
- Amendement : toute modification passe par ce document, avec bump de version
  sémantique (MAJOR = rupture de principe ; MINOR = nouveau principe ou
  extension matérielle ; PATCH = clarification), date de dernière
  modification et rapport d'impact en tête de fichier.
- Conformité : chaque revue de changement vérifie les principes I–VI ; toute
  complexité ajoutée doit être justifiée (principe V).
- Le guide d'exécution courant pour les agents reste `AGENTS.md` ; en cas de
  divergence, cette constitution gagne.

**Version**: 1.0.0 | **Ratified**: 2026-08-25 | **Last Amended**: 2026-08-25
