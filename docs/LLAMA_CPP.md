# Utiliser llama.cpp avec EduNexus

## Réponse courte

Il n’est **pas nécessaire de copier llama.cpp dans le dossier du projet EduNexus**. EduNexus attend simplement le chemin d’un exécutable externe nommé `llama-server` et les chemins de vos modèles au format GGUF.

Une installation propre peut donc ressembler à ceci :

```text
~/tools/llama.cpp/                 code source facultatif
~/tools/llama.cpp/build/bin/llama-server
~/models/granite-embedding.gguf
~/models/granite-llm-q4.gguf
~/models/granite-docling.gguf
~/work_edunexus/projet_ollama_tutor/
```

Le chemin important à fournir à EduNexus est celui de `llama-server`, pas celui du dépôt Git complet.

## Ce que fait llama.cpp

llama.cpp est un moteur d’inférence C/C++ qui charge un modèle GGUF et exécute localement ses calculs. `llama-server` ajoute une API HTTP autour de ce moteur. EduNexus démarre ce processus quand le mode GGUF est sélectionné, lui fournit un modèle, attend son endpoint `/health`, puis envoie des requêtes HTTP pour générer du texte ou calculer des embeddings.

Dans EduNexus, `LlamaServerManager` limite par défaut le nombre de serveurs actifs à un seul. Cette contrainte est volontaire sur une machine de 16 Go : elle évite de garder simultanément plusieurs gros modèles GGUF en mémoire. Le gestionnaire attribue également un port libre, surveille le démarrage et arrête proprement le processus.

Le modèle GGUF et le binaire `llama-server` sont deux choses différentes : le binaire est le moteur ; le fichier GGUF contient les poids du modèle. Il faut les installer séparément.

## Méthode recommandée : binaire précompilé

Pour une première installation, téléchargez une release officielle depuis [la page Releases de llama.cpp](https://github.com/ggml-org/llama.cpp/releases). Choisissez une archive Linux x86_64 CPU correspondant à votre système. Évitez les archives CUDA, Metal ou ROCm puisque votre i5-7300U n’a pas de GPU compatible pour ces backends.

Après téléchargement, décompressez l’archive dans un dossier personnel :

```bash
mkdir -p "$HOME/tools/llama.cpp"
unzip llama-*.zip -d "$HOME/tools/llama.cpp"
find "$HOME/tools/llama.cpp" -type f -name llama-server -print
```

Rendez le fichier exécutable si nécessaire :

```bash
chmod +x "$HOME/tools/llama.cpp/**/llama-server"
```

Le motif `**` n’est pas développé par tous les shells sans l’option `globstar`. Utilisez donc le chemin exact retourné par `find`, par exemple :

```bash
chmod +x "$HOME/tools/llama.cpp/build/bin/llama-server"
"$HOME/tools/llama.cpp/build/bin/llama-server" --version
```

Si la commande affiche une version, le moteur est prêt. Il n’est pas nécessaire d’ajouter le binaire au `PATH` si vous renseignez son chemin absolu dans EduNexus.

## Méthode alternative : compilation CPU locale

La compilation est utile si aucune release précompilée ne convient à votre distribution, ou si vous voulez un binaire construit localement. Le guide officiel de llama.cpp indique cette procédure CPU avec CMake :

```bash
sudo apt update
sudo apt install -y git cmake build-essential
mkdir -p "$HOME/tools"
cd "$HOME/tools"
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j2
```

Sur un i5-7300U, `-j2` évite de saturer inutilement les deux cœurs pendant la compilation. Le binaire devrait être disponible ici :

```bash
find "$HOME/tools/llama.cpp" -type f -name llama-server -print
```

Testez-le :

```bash
"$HOME/tools/llama.cpp/build/bin/llama-server" --version
```

Le projet officiel décrit également la construction CPU dans [son guide de build](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md). Les paramètres du serveur sont décrits dans [la documentation de llama-server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Installer les modèles GGUF

Téléchargez uniquement des modèles GGUF compatibles avec l’architecture souhaitée. Pour votre machine, privilégiez les quantifications Q4 ou Q5. Le fichier d’embedding Granite 107M est déjà petit ; conservez Q8 si la qualité de recherche est importante. Pour le modèle génératif, Q4 est plus réaliste sur 16 Go de RAM.

Placez les fichiers hors du dépôt :

```bash
mkdir -p "$HOME/models"
# Exemple : copier ou déplacer ici les fichiers .gguf téléchargés
ls -lh "$HOME/models"
```

Ne lancez jamais un fichier téléchargé comme un exécutable. Seul `llama-server` doit être exécuté ; les fichiers `.gguf` sont passés comme arguments au moteur.

## Configurer EduNexus

Dans l’interface, ouvrez **Réglages** et sélectionnez le moteur GGUF/local si cette option est exposée par votre version. Sinon, modifiez le fichier de configuration indiqué par le README, généralement :

```text
~/.config/ollama-tui/config.json
```

La partie pertinente ressemble à ceci :

```json
{
  "tutor": {
    "enabled": true,
    "llama_bin": "/home/VOTRE_UTILISATEUR/tools/llama.cpp/build/bin/llama-server",
    "embed_gguf": "/home/VOTRE_UTILISATEUR/models/granite-embedding-107m-q8.gguf",
    "llm_gguf": "/home/VOTRE_UTILISATEUR/models/granite-llm-q4.gguf"
  }
}
```

Les noms exacts des clés doivent rester ceux acceptés par votre version d’EduNexus. Le projet contient déjà `llama_bin`, `embed_gguf`, `docling_gguf`, `docling_mmproj` et `llm_gguf` dans sa configuration.

Après modification, redémarrez EduNexus. Dans l’interface, vérifiez le moteur et lancez une question simple. Si le serveur ne démarre pas, exécutez manuellement :

```bash
"$HOME/tools/llama.cpp/build/bin/llama-server" \
  -m "$HOME/models/granite-llm-q4.gguf" \
  --host 127.0.0.1 \
  --port 8080 \
  --threads 3 \
  --threads-batch 3 \
  --ctx-size 4096 \
  --parallel 1
```

Puis vérifiez son état :

```bash
curl http://127.0.0.1:8080/health
```

Arrêtez le serveur manuel avec `Ctrl+C` avant de relancer EduNexus ; sinon deux processus peuvent se disputer le port ou la mémoire.

## Réglages conseillés pour votre i5-7300U

| Paramètre | Valeur de départ | Raisonnement |
|---|---:|---|
| `--threads` | `3` | Compromis entre débit et réactivité sur 2 cœurs / 4 threads. Testez ensuite 2 et 4. |
| `--threads-batch` | `3` | Suffisant pour le prompt processing sans saturer le portable. |
| `--ctx-size` | `4096` | Réduit le cache mémoire par rapport à 8192. |
| `--parallel` | `1` | Une seule session interactive ; le parallélisme sert surtout à plusieurs requêtes simultanées. |
| `--flash-attn` | `auto` | Laissez llama.cpp choisir ; sur CPU ancien le gain n’est pas garanti. |

Pour le modèle génératif, commencez avec Gemma 4 E2B en Q4 si vous voulez privilégier la qualité, ou Qwen3 1.7B Q4 si vous voulez privilégier la vitesse. Pour l’embedding, Granite 107M Q8 est suffisamment léger.

## Llama.cpp ou Ollama ?

Ollama est le chemin le plus simple : il gère le téléchargement, le catalogue des modèles, le chargement et l’API. llama.cpp donne davantage de contrôle sur les fichiers GGUF, les threads, les batches et la coexistence avec d’autres processus, mais demande de gérer vous-même le binaire et les modèles.

Il ne faut pas faire tourner simultanément le même modèle dans Ollama et llama.cpp en pensant accélérer EduNexus. Choisissez un moteur par session. Sur votre machine, commencez par Ollama avec les corrections de latence déjà appliquées ; testez ensuite llama.cpp si vous avez besoin d’un contrôle plus fin ou si Ollama recharge trop souvent les modèles.

## Dépannage

| Symptôme | Vérification |
|---|---|
| `llama-server: command not found` | Renseignez le chemin absolu vers le binaire dans `llama_bin`. |
| Le serveur démarre puis s’arrête | Vérifiez le chemin du `.gguf`, les permissions et les logs du processus. |
| La machine devient inutilisable | Passez de `--threads 4` à `--threads 2` ou `3`, réduisez `--ctx-size` et utilisez une quantification Q4. |
| La première réponse est très lente | C’est probablement le chargement du modèle ; laissez le processus actif ou utilisez le `keep_alive` d’Ollama. |
| Erreur de modèle non compatible | Téléchargez un GGUF prévu pour l’architecture et la version du moteur utilisées. |
| Ollama et llama.cpp semblent se remplacer | Vérifiez les processus et choisissez un seul moteur actif pour le même rôle. |

## Références

- [Dépôt officiel llama.cpp](https://github.com/ggml-org/llama.cpp)
- [Releases llama.cpp](https://github.com/ggml-org/llama.cpp/releases)
- [Compilation locale](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
- [Serveur HTTP llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
