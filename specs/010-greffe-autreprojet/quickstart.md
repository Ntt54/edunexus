# Quickstart — validation 010-greffe-autreprojet (Phase 1)

Guide de validation par lot. Prérequis : `./venv` à jour, depuis la racine.
Aucun démon Ollama requis (tout est mocké). Aucun code d'implémentation ici —
voir `contracts/` et `data-model.md`.

## 0. Gates globaux (après chaque lot)

```bash
venv/bin/pytest tests/ -q
```

Suite verte exigée (< 20 s). En cas d'échec : corriger + ajouter le test de
régression qui aurait échoué avant le correctif (Constitution III).

## 1. P0-A sandbox (recommandé en premier)

```bash
venv/bin/pytest tests/unit/test_safety.py tests/unit/test_sandbox.py -q
venv/bin/python -c "
import asyncio
from src.ollama_tutor.tutor.sandbox import run_python
r = asyncio.run(run_python('print(2+2)'))
print(r.stdout.strip(), r.exit_code, r.timed_out, r.blocked)
"
venv/bin/python -c "
import asyncio
from src.ollama_tutor.tutor.sandbox import run_python
r = asyncio.run(run_python('import socket'))
print(r.blocked, r.safety_events)
"
```

Attendu : `4 0 False False` puis `True` + événement `blocked_import: socket`.
Cas limites à vérifier : boucle infinie (timeout + suffixe `killed after`),
sortie > 32 Ko (`truncated=True`), `SyntaxError` (diagnostic retourné, non bloqué).

## 2. P0-B registry

```bash
venv/bin/pytest tests/unit/test_provider_registry.py -q
```

Attendu : fallback vers provider sain quand le primaire est KO ;
`LLMConfigurationError` sur registre vide ; `ping_all()` rapporte par provider ;
mode `skip` ⇒ vecteurs nuls sans appel réseau.
Contrôle CPU : `venv/bin/python -c "import sentence_transformers"` doit échouer
(dépendance absente) et le mode résolu sur cette machine doit être `skip` ou un
provider CPU local (GGUF/Ollama) — jamais de chargement torch.
Si `client.py` modifié : `./benchmark.sh` (gate parité `ollama run`).

## 3. P1-A jobs

```bash
venv/bin/pytest tests/integration/test_ingestion_jobs.py -q
```

Attendu : import ⇒ `job_id` immédiat ; phases monotones jusqu'à `completed` ;
doublon exact ⇒ `completed` avec `nodes_created=0` ; échec simulé ⇒ `failed` +
`error_message` + entrée `errors.log`. Vérifier la migration idempotente
(double application sans erreur).

## 4. P1-B contract test

```bash
venv/bin/pytest tests/contract/test_web_contract.py -q
```

Attendu : vert sur l'arbre actuel ; rouge si on ajoute un `fetch()` sans route
(le tester en ajoutant/supprimant un endpoint factice).
Si `tutor.html` touché : extraire le JS inline et `node --check`.
