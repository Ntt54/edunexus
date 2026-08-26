# Indexation nocturne locale d’EduNexus

## Objectif

Le mode nocturne permet d’importer plusieurs livres avant une période d’inactivité, puis de les indexer localement sans lancer une tâche complète par livre. Les livres sont conservés dans SQLite avec le statut `pending`, puis un **worker unique** les consomme dans l’ordre de leur `created_at`. Cette architecture privilégie la stabilité, la mémoire disponible et la reprise après interruption plutôt qu’un parallélisme agressif.

> Sur un Intel i5-7300U doté de 16 Go de RAM, le réglage de départ recommandé est **batch 16 / concurrence 1**. La concurrence 2 ne doit être essayée qu’après mesure sur les documents réels.

## Flux de traitement

L’import multipart depuis l’interface transmet `queue=true`. EduNexus copie d’abord le fichier dans le répertoire local des uploads, vérifie le chemin et enregistre le livre et son association au domaine. La réponse revient immédiatement avec `status: "pending"`; aucune extraction ni génération d’embeddings n’est exécutée par une tâche indépendante pour ce livre.

Le worker est ensuite démarré automatiquement par le premier import en file. Les imports suivants réutilisent le même worker lorsqu’il est encore actif. Le worker sélectionne toujours le premier livre `pending`, récupère son domaine, marque le livre `indexing`, extrait le texte, crée les fragments, calcule les embeddings par lots, écrit les chunks et termine par `indexed`. Les livres complets ne sont donc pas traités en parallèle.

Le cache d’embeddings est indexé par le hash du texte et le nom du modèle. Un changement de modèle ne mélange pas les vecteurs : les nouveaux appels utilisent une autre clé de cache et une réindexation est nécessaire pour rendre le nouveau modèle actif sur les chunks concernés.

Lorsqu’un fichier est modifié puis réimporté au même chemin, EduNexus conserve l’identifiant du livre, purge ses chunks obsolètes et le remet en `pending`. Les fragments identiques au contenu précédent peuvent cependant retrouver leur vecteur dans le cache hash+modèle : le découpage reste globalement recalculé, mais les requêtes d’embedding inutiles sont évitées.

## Batching et concurrence

La taille d’un lot et la concurrence sont deux paramètres indépendants. `embed_batch_size` définit combien de fragments sont envoyés dans une requête `/api/embed` ou à un provider GGUF. `max_parallel_embed` définit le nombre maximum de requêtes d’embeddings en vol. Les lots sont préparés dans l’ordre d’entrée et exécutés derrière un `asyncio.Semaphore`; la limite est donc effective même si plusieurs lots sont créés simultanément.

| Paramètre | Défaut | Bornes | Conseil i5-7300U |
|---|---:|---:|---|
| `embed_batch_size` | `16` | 1–64 | Commencer à 16 ; essayer 8 si la mémoire augmente ou 32 si le provider reste fluide. |
| `max_parallel_embed` | `1` | 1–8 | Garder 1 par défaut ; mesurer 2 uniquement si Ollama/llama.cpp et la RAM restent stables. |
| worker de livres | 1 | fixe | Ne pas augmenter : plusieurs livres complets en parallèle dégradent la réactivité et multiplient les besoins mémoire. |

Le paramètre historique `max_parallel_embed` reste accepté par la fonction Python `embed_texts` pour compatibilité. Lorsqu’il est fourni seul par un ancien appel, il conserve son ancienne interprétation. Les nouveaux appels du service utilisent explicitement `batch_size` et `max_concurrency`.

## Planification locale

Le planificateur local est désactivé par défaut pour éviter toute charge inattendue. Il peut être activé dans **Réglages** ou avec `PUT /api/tutor/settings`. Il vérifie la fenêtre `nightly_start_at` → `nightly_stop_at` toutes les 30 secondes, respecte `nightly_only_on_ac` lorsque Linux expose une alimentation secteur, et borne la durée avec `nightly_max_runtime_minutes`. Il ne dépend d’aucun service distant.

| Paramètre | Défaut | Recommandation |
|---|---:|---|
| `nightly_enabled` | `false` | Activer seulement après un premier essai manuel. |
| `nightly_start_at` / `nightly_stop_at` | `23:00` / `07:00` | Adapter à la période où le portable est inutilisé. |
| `nightly_only_on_ac` | `true` | Conserver `true` sur un portable. |
| `nightly_max_runtime_minutes` | `420` | Réduire si le poste doit redevenir disponible tôt. |
| `nightly_prepare_enabled` | `false` | Activer seulement si le pré-calcul pédagogique est souhaité. |

Le pré-calcul pédagogique réutilise `prepare_knowledge()` après l’indexation. Il génère idempotemment concepts, flashcards, glossaire et relations avec le LLM local. Cette opération consomme davantage de temps CPU et de génération que les embeddings ; elle est donc séparée et désactivée par défaut.

## Reprise, pause et annulation

Les états `pending`, `indexing`, `indexed` et `error` sont stockés dans la table SQLite `books`. Au démarrage d’une application, les lignes restées `indexing` sont considérées comme orphelines : leurs chunks partiels sont supprimés et elles repassent à `pending`. La reprise est ainsi déterministe et ne présente pas un index incomplet comme un livre prêt.

La pause demande au worker de s’arrêter coopérativement. Si un livre est en cours, son drapeau d’annulation est positionné ; le pipeline purge alors ses fragments partiels et le remet à `pending`. Un livre en attente peut être annulé par le même endpoint et reste reprenable si l’utilisateur relance la file. Une erreur d’extraction, d’Ollama ou de llama.cpp est enregistrée sur le livre et ne bloque pas les livres suivants. Les erreurs qui ressemblent à une panne transitoire de transport, de timeout, HTTP, serveur ou Ollama sont retentées automatiquement au plus trois fois. Une erreur persistante peut être relancée avec `POST /api/tutor/books/{id}/retry`.

## Maintenance et sauvegarde

`POST /api/tutor/maintenance` lance une vérification SQLite, compte puis supprime uniquement les embeddings orphelins, exécute `PRAGMA optimize` et crée par défaut une sauvegarde cohérente de `library.db` dans `~/.config/ollama-tui/tutor/backups/`. Le paramètre `vacuum=true` permet un compactage explicite, à réserver à une période où aucune indexation n’est active.

Cette maintenance ne supprime pas les documents source et conserve les anciens chunks dont la provenance d’embedding est inconnue (`NULL`) afin de rester compatible avec les bases historiques.

## API de contrôle

| Méthode | Route | Effet |
|---|---|---|
| `GET` | `/api/tutor/index-queue` | Retourne `running`, `paused`, le livre courant, le nombre en attente, les compteurs de succès/erreurs et l’heure de lancement. |
| `POST` | `/api/tutor/index-queue/start` | Démarre le worker unique. Le paramètre query `retry_errors=true` remet les livres en erreur dans la file. |
| `POST` | `/api/tutor/index-queue/stop` | Met la file en pause et attend l’arrêt coopératif du livre courant. |
| `POST` | `/api/tutor/books/{id}/cancel` | Annule un livre `pending` ou `indexing` et le rend reprenable en `pending`. |
| `GET` | `/api/tutor/index-status` | Conserve le contrat existant pour le polling léger de l’interface. |
| `GET` | `/api/tutor/nightly` | État du planificateur, de la fenêtre, du secteur et du pré-calcul. |
| `POST` | `/api/tutor/nightly/start` / `stop` | Active ou désactive immédiatement le planificateur. |
| `POST` | `/api/tutor/maintenance` | Vérification, nettoyage, optimisation et sauvegarde locale. |
| `POST` | `/api/tutor/books/{id}/retry` | Replace un livre en erreur dans la file. |

Exemple de consultation :

```json
{
  "running": true,
  "paused": false,
  "current_book_id": "book-…",
  "current_title": "Cours de réseaux",
  "pending_count": 3,
  "completed_count": 2,
  "error_count": 0
}
```

## Procédure recommandée

Importez les livres depuis l’onglet **Bibliothèque**. Ils sont placés automatiquement dans la file et le panneau indique le livre actif et le nombre restant. Avant de laisser la machine travailler, sélectionnez le modèle d’embeddings voulu, vérifiez que le fichier source est lisible et évitez de lancer simultanément un autre gros modèle GGUF.

Laissez le portable branché et désactivez temporairement la suspension automatique. Pour un premier essai, laissez `embed_batch_size=16` et `max_parallel_embed=1`. Mesurez ensuite le temps total, la température, la mémoire et la réactivité interactive. Si le système reste stable, testez la concurrence 2 sur un petit lot ; revenez à 1 dès qu’Ollama met les requêtes en attente, que la mémoire augmente fortement ou que le poste devient peu réactif.

## Fichiers principaux

| Fichier | Responsabilité |
|---|---|
| `src/ollama_tutor/tutor/service.py` | Worker unique, cycle start/stop, reprise, annulation et branchement des paramètres d’embeddings. |
| `src/ollama_tutor/tutor/embeddings.py` | Cache, préparation des lots, sémaphore et remise des vecteurs dans l’ordre initial. |
| `src/ollama_tutor/tutor/store.py` | Statuts SQLite, progression, purge et récupération des indexations interrompues. |
| `src/ollama_tutor/web/server.py` | Contrats HTTP et compatibilité avec l’ancien import background. |
| `src/ollama_tutor/web/static/tutor.html` | Panneau UI, import `queue=true`, démarrage et pause. |

## Limites connues

La file est persistante par les lignes de livres, mais ses compteurs `completed_count` et `error_count` sont des compteurs de session du processus ; ils ne constituent pas un historique analytique durable. La file ne fournit pas de planificateur horaire autonome : elle démarre lors d’un import en queue ou via l’endpoint `start`. Un planificateur système Linux tel que `systemd` ou `cron` peut appeler l’endpoint local si un démarrage à heure fixe devient nécessaire.

La suite automatisée reste hors ligne. La mesure de débit réelle dépend du modèle, du provider, de la longueur des livres et de la version d’Ollama ou de llama.cpp ; elle doit être réalisée sur la machine cible.

## Références

1. [Ollama — API embeddings](https://docs.ollama.com/api/embed) — format des entrées multiples et des réponses d’embeddings.
2. [Ollama — FAQ sur la concurrence](https://docs.ollama.com/faq) — effet de la concurrence sur les contextes et la mémoire.
3. [llama.cpp — serveur HTTP](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) — paramètres `--parallel`, batches et threads.
4. [Python — `asyncio.Semaphore`](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Semaphore) — primitive utilisée pour borner les requêtes concurrentes.
