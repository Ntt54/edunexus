# EduNexus Web — Migration Vue 3 + Vite

Ce dossier est un **frontend Vue 3 + Vite autonome** préparé pour la migration progressive de l’interface historique `tutor.html` d’EduNexus. Il est volontairement séparé du dépôt Python afin qu’OpenCode puisse l’intégrer après ses modifications backend, sans conflit ni écrasement de fichiers métier.

L’interface suit la direction **Atelier de progression** : une navigation organisée par intention, une prochaine étape dominante, des sources visibles, des preuves de maîtrise textuelles et une frise de parcours accessible. Elle est optimisée pour les ordinateurs modestes : aucune dépendance de graphique lourde, aucune image locale embarquée et des interactions CSS légères.

## Lancer le frontend

```bash
pnpm install
pnpm dev
```

Pour générer les fichiers statiques :

```bash
pnpm build
```

## Apparence et langues

L’en-tête comporte un sélecteur **FR / EN** et une commande **Nuit / Clair**. Les deux préférences sont stockées localement dans le navigateur sous `edunexus:locale` et `edunexus:theme`; elles sont donc conservées au prochain lancement sans solliciter FastAPI.

La langue traduit l’interface, les statuts et les consignes. Les titres, chapitres et extraits provenant des livres restent volontairement dans leur langue source : le frontend ne traduit pas le corpus pédagogique de l’utilisateur. Pour prévisualiser un état sans préférence enregistrée, utiliser par exemple `?theme=dark&lang=en` avant la partie `#` de l’URL.

Le mode de démonstration est activé tant que `VITE_EDUNEXUS_DEMO_MODE` n’est pas explicitement défini à `false`. Les données servent uniquement à présenter les vues avant le branchement FastAPI.

## Ce qui est inclus

| Domaine | Contenu |
|---|---|
| Navigation | Shell Vue responsive, matière active, navigation par intention et repère Nexus. |
| Accueil | Carte « prochaine étape », progression, révisions, notions stables, sources et recommandations. |
| Parcours | Frise d’étapes, statut courant, étape à faire, durée, source et action de poursuite. |
| Apprentissage | Séance de révision en trois temps, rappel actif, maîtrise et fiches. |
| Sources | Documents, provenance, recherche, file nocturne et statuts d’indexation. |
| Entraînement et quiz | Interfaces prêtes à raccorder à la génération FastAPI. |
| Progression | Carte de maîtrise avec preuve textuelle et lien vers l’exercice suivant. |
| Tuteur | Interface préparée pour le WebSocket, citations et réponses streaming. |
| Réglages | Ressources CPU, automatisations nocturnes, maintenance et confidentialité. |

## Intégrer à EduNexus

La seule couche à adapter pour les données réelles est `client/src/services/api.ts`. L’architecture, la stratégie de coexistence avec `tutor.html` et les routes à vérifier sont documentées dans [docs/INTEGRATION_FASTAPI.md](docs/INTEGRATION_FASTAPI.md).

La migration conseillée est d’ajouter d’abord une route distincte, telle que `/tutor-vue`, de raccorder accueil/parcours, puis de migrer les autres vues. Ne pas supprimer l’ancienne interface avant les tests sur les données et le WebSocket réels.

## Structure

```text
client/src/
├── components/  composants réutilisables
├── data/        données de démonstration
├── services/    adaptateurs HTTP
├── stores/      état Vue
├── views/       vues de l’application
├── App.vue      composition principale
├── router.ts    routes hash
└── index.css    design system global
```

Les assets de marque sont servis depuis le stockage du projet et ne sont pas intégrés dans l’archive source. Le symbole possède un repli CSS ; l’application reste utilisable tant que les visuels asynchrones ne sont pas disponibles.
