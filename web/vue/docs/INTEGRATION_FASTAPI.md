# Intégration du frontend Vue avec EduNexus FastAPI

## Principes de migration

Ce dossier constitue un **frontend autonome**. Il ne modifie pas le backend Python, la base SQLite, l’indexation, les modèles ni les routes actuelles. La migration recommandée consiste à faire coexister l’ancienne page `tutor.html` et cette application Vue, puis à remplacer vue par vue les appels de démonstration par les routes FastAPI validées.

La couche à adapter est concentrée dans `client/src/services/api.ts`. Les vues et composants ne doivent pas construire directement des URL API. Cette séparation permet de modifier les contrats, les noms de champs ou les transformations de données sans refaire l’interface.

## Mode de démonstration

Par défaut, le projet présente des données réalistes de démonstration dans `client/src/data/demo.ts`. Elles sont volontairement identifiées comme des données de migration et ne sont ni persistées ni envoyées au backend. Elles servent à visualiser l’expérience complète avant d’avoir branché FastAPI.

Pour activer l’API réelle lors de l’intégration, définir les valeurs suivantes dans l’environnement de build ou l’outil de configuration choisi par OpenCode :

| Variable | Valeur de développement suggérée | Rôle |
|---|---|---|
| `VITE_EDUNEXUS_API_BASE` | `http://127.0.0.1:9215/api/tutor` | Préfixe HTTP des routes FastAPI. |
| `VITE_EDUNEXUS_DEMO_MODE` | `false` | Désactive les données de démonstration. |

Si le frontend est servi par FastAPI à la même origine, la valeur de base peut rester `/api/tutor`. Cela évite les problèmes CORS et garde les protections same-origin existantes.

## Contrats à raccorder

La plupart des données nécessaires existaient déjà dans l’interface historique. Le tableau ci-dessous indique où effectuer le raccordement ; OpenCode doit confirmer les formes JSON définitives contre la version finale du backend avant d’activer `VITE_EDUNEXUS_DEMO_MODE=false`.

| Besoin Vue | Fonction adaptatrice | Route FastAPI existante ou attendue | Transformation à contrôler |
|---|---|---|---|
| Matières | `tutorApi.dashboard()` | `GET /subjects` | Choisir la matière active plutôt que systématiquement le premier élément. |
| Parcours | `tutorApi.dashboard()` | `GET /paths?subject_id={id}`, puis `GET /paths/{id}` | Convertir `activity_type` en `activityType` si nécessaire. |
| Progression | `tutorApi.dashboard()` | `GET /subjects/{id}/progress` | Mapper `concept`, `score`, `recent_failures`, `next_review`. |
| Révisions | `tutorApi.dashboard()` | `GET /subjects/{id}/reviews/due` | Mapper `due` vers la structure `ReviewItem`. |
| Sources | `tutorApi.dashboard()` | route de livres utilisée par `tutor.html` | Vérifier le filtrage par `subject_id` et les statuts `pending/indexing/indexed/error`. |
| File nocturne | `tutorApi.setQueue()` | `GET /index-queue`, `POST /index-queue/start`, `POST /index-queue/stop` | Conserver les compteurs, le livre courant et l’état de pause. |
| Étape terminée | `tutorApi.completeStep()` | route de mise à jour de l’étape actuelle | Confirmer le chemin exact et la clé de statut. |
| Conversation | vue Tuteur | WebSocket FastAPI existant | Reprendre le protocole de streaming, les citations et l’annulation. |

## Stratégie d’intégration progressive

Commencer par servir le build Vue sur une nouvelle route, par exemple `/tutor-vue`, sans enlever l’ancienne interface. Activer ensuite l’API réelle pour **l’accueil et le parcours**, car ce sont les vues dont les contrats sont les plus structurés. Brancher ensuite les sources et la file d’indexation. Les vues Réviser, Exercices, Quiz et Tuteur peuvent rester en présentation contrôlée jusqu’à ce que les flux FastAPI et WebSocket correspondants soient validés.

Une fois une vue raccordée, tester simultanément l’affichage sans donnée, le chargement, les erreurs HTTP, les changements de matière, la navigation clavier et la version mobile. La logique d’apprentissage, la sélection de sources et les décisions de sécurité doivent rester du côté FastAPI ; le frontend se limite au rendu et aux intentions utilisateur.

## Arborescence de migration

```text
client/src/
├── components/           # Shell, motif Nexus, badges et indicateurs réutilisables
├── data/demo.ts          # Données de démonstration, à supprimer ou isoler après raccordement
├── services/api.ts       # Unique adaptateur HTTP vers FastAPI
├── stores/learning.ts    # État applicatif Vue : matière, parcours, progression, file
├── views/                # Une vue par intention d’apprentissage
├── router.ts              # Routes hash, faciles à servir depuis FastAPI
├── types.ts               # Vocabulaire frontal typé
└── index.css              # Tokens et système visuel Atelier de progression
```

## Points de vigilance

Les données de parcours, sources, scores et citations sont des données pédagogiques réelles : ne pas les remplacer par des valeurs génériques lorsque le mode API est actif. En cas de réponse incomplète, la vue doit afficher un état vide explicite plutôt qu’inférer une progression.

Les actions qui écrivent dans FastAPI — import, suppression, marquage d’étape, réglages ou démarrage de file — doivent reprendre les protections same-origin, les messages de confirmation et les erreurs détaillées de l’interface historique. Pour le tuteur en streaming, conserver les mécanismes d’annulation déjà présents avant d’ajouter une nouvelle UX.
