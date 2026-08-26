# Feature 006: Apprentissage adaptatif multi-domaine + Parcours + llama.cpp

## Feature Summary

EduNexus doit devenir un vrai tuteur adaptatif capable de s'adapter à tout type de contenu (programmation, mathématiques, sciences, langues, etc.) avec des méthodologies pédagogiques appropriées. La vue Parcours (actuellement placeholder "à venir") devient fonctionnelle. Enfin, llama.cpp est intégré en tant que moteur d'infération parallèle pour décharger Ollama et permettre le parallélisme réel.

**Recherche sous-jacente :**
- Systèmes adaptatifs : +42% d'amélioration des résultats d'apprentissage (données Carnegie Learning MATHia, 1M+ étudiants, 2026)
- Taxonomie de Bloom (Remember → Understand → Apply → Analyze → Evaluate → Create) : framework standard pour la classification des objectifs pédagogiques
- Répétition espacée (Ebbinghaus) : courbe de l'oubli, intervalles optimaux 1j → 3j → 1sem → 2sem → 1mois
- Alt-ED (2026) : taxonomie adaptative pour configurer les systèmes LLM en éducation
- llama.cpp : moteur C++ pur, OpenAI-compatible API via llama-server, supporte `--parallel N` pour N conversations simultanées, 10-20% plus rapide qu'Ollama en direct
- llama-cpp-python : bindings Python, `pip install llama-cpp-python`, API OpenAI-like intégrée

---

## 1. User Scenarios & Testing

### Primary User Flow: Apprentissage adaptatif

1. L'utilisateur crée un espace (matière) et importe des documents
2. Le système analyse le contenu et classe la matière (programmation, maths, sciences, etc.)
3. L'utilisateur choisit un parcours d'apprentissage ou commence une session
4. Le tuteur adaptera son approche selon la matière :
   - **Programmation** : exercices pratiques, code review, résolution de bugs, progression par concepts
   - **Mathématiques** : démonstrations, problèmes gradués, rappels de formules, erreurs courantes
   - **Sciences** : explications conceptuelles, quiz de compréhension, analogies, expériences mentales
   - **Langues** : vocabulaire, grammaire, contexte culturel, répétition espacée
   - **Générique** : pédagogie socratique, questions de compréhension, résumés

### Testing Approach

- Tests hors-ligne avec MockTransport (constitution principle III)
- Classification de contenu testée via fixtures de documents par type
- Parcours testé via scénarios CRUD + progression
- llama.cpp testé via subprocess mock + transport httpx

---

## 2. Functional Requirements

### FR-1: Classification de contenu (content classification)

Le système doit classifier automatiquement le type de contenu d'un espace à partir des documents importés.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1.1 | Classifier le contenu en catégories : programmation, mathématiques, sciences, langues, générique | High |
| FR-1.2 | La classification est basée sur l'analyse du texte (mots-clés, structure) et/ou les métadonnées | High |
| FR-1.3 | L'utilisateur peut manuellement ajuster la classification | Medium |
| FR-1.4 | La classification détermine la stratégie pédagogique utilisée | High |

### FR-2: Stratégies pédagogiques par domaine (domain-specific pedagogy)

Chaque domaine de contenu a une stratégie pédagogique adaptée.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-2.1 | Stratégie programmation : exercices pratiques, analyse de code, résolution de bugs | High |
| FR-2.2 | Stratégie mathématiques : démonstrations, problèmes gradués, erreurs courantes | High |
| FR-2.3 | Stratégie sciences : analogies, explications conceptuelles, quiz de compréhension | High |
| FR-2.4 | Stratégie langues : vocabulaire, grammaire, contexte culturel | Medium |
| FR-2.5 | Stratégie générique : pédagogie socratique, résumés, questions | High |
| FR-2.6 | Le système utilise Bloom's Taxonomy pour adapter la profondeur (Remember → Create) | Medium |

### FR-3: Répétition espacée et progression (spaced repetition)

Le système intègre la répétition espacée pour optimiser la rétention.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-3.1 | Tracker les dates de révision par concept/notion | High |
| FR-3.2 | Calculer les intervalles optimaux selon la courbe de l'oubli | High |
| FR-3.3 | Proposer des révisions au moment optimal | Medium |
| FR-3.4 | Afficher la progression par niveau de Bloom | Medium |

### FR-4: Vue Parcours fonctionnelle (functional paths)

La vue Parcours (actuellement placeholder) devient fonctionnelle.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-4.1 | Créer/modifier/supprimer des parcours d'apprentissage | High |
| FR-4.2 | Un parcours = séquence ordonnée de modules/étapes | High |
| FR-4.3 | Chaque étape pointe vers un concept/notion ou un ensemble de documents | High |
| FR-4.4 | Suivre la progression dans le parcours (complété/en cours/non commencé) | High |
| FR-4.5 | Proposer automatiquement un parcours basé sur les lacunes détectées | Medium |

### FR-5: Intégration llama.cpp (llama.cpp integration)

llama.cpp est intégré comme moteur d'infération alternatif pour le parallélisme.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-5.1 | Télécharger et compiler llama.cpp depuis GitHub | High |
| FR-5.2 | Exposer llama-server sur un port dédié (ex: 8081) | High |
| FR-5.3 | Le provider OpenAI-compat (B1) pointe vers llama-server par défaut | High |
| FR-5.4 | Supporter `--parallel N` pour N conversations simultanées | High |
| FR-5.5 | Gestion automatique du lifecycle (démarrage/arrêt) via config | High |
| FR-5.6 | Fallback vers Ollama si llama.cpp n'est pas disponible | High |

### FR-6: Audit de code et réconciliation (code audit)

Analyser l'état actuel du code après le travail parallèle des agents.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-6.1 | Vérifier la cohérence des imports et des dépendances | High |
| FR-6.2 | Identifier les incohérences entre les modules | High |
| FR-6.3 | Valider que les tests couvrent les nouveaux chemins | High |

---

## 3. Success Criteria

### Quantitative

- La classification de contenu atteint ≥80% de précision sur un jeu de test de 50 documents variés
- Le parallélisme llama.cpp supporte ≥3 conversations simultanées sans dégradation
- Le temps de réponse pour une question pédagogique reste <10s en mode parallèle
- La répétition espacée réduit le taux d'oubli de ≥30% (mesuré par quiz de rétention)

### Qualitative

- L'utilisateur peut créer un parcours en <2 minutes
- Le tuteur s'adapte visiblement au type de contenu (pas de réponse générique pour la programmation)
- Le switch entre Ollama et llama.cpp est transparent (même interface)

---

## 4. Key Entities

| Entity | Description |
|--------|-------------|
| ContentProfile | Classification du contenu d'un espace (type, confiance, tags) |
| PedagogyStrategy | Stratégie pédagogique (templates, approche, Bloom levels) |
| SpacedRepetitionCard | Carte de répétition espacée (concept, intervalle, prochaine révision) |
| LearningPath | Parcours d'apprentissage (séquence d'étapes) |
| PathStep | Étape d'un parcours (concept, statut, ordre) |
| LlamaCppConfig | Configuration llama.cpp (binaire, port, parallel, model) |

---

## 5. Assumptions

- L'utilisateur a un CPU suffisant pour faire tourner llama.cpp en parallèle d'Ollama
- Le modèle GGUF est déjà téléchargé ou peut l'être via le mechanisme existant
- La classification de contenu peut être basée sur des règles simples (mots-clés) pour commencer
- Bloom's Taxonomy est utilisée comme cadre de référence, pas comme obligation stricte
- La répétition espacée est optionnelle (l'utilisateur peut la désactiver)

---

## 6. Out of Scope

- Fine-tuning de modèles pour un domaine spécifique
- Support vocal / reconnaissance d'ordinateur
- Multi-utilisateurs (gestion de groupe/classe)
- Intégration LMS (Canvas, Moodle)
- VR/AR learning

---

## 7. Dependencies

- llama.cpp compilation depuis source (git clone + cmake)
- OpenAI-compat provider (B1) déjà implémenté
- Store et schema existants (005-platform-ui-library)
- Constitution v1.0.0 (principes I-VI)

---

## 8. Risks

| Risk | Mitigation |
|------|-----------|
| llama.cpp compilation échoue sur certaines plateformes | Fallback Ollama garanti, compilation optional |
| Classification de contenu incorrecte | Permettre la correction manuelle |
| Parallélisme consomme trop de RAM | Limiter `--parallel` selon la config machine |
| Répétition espacée trop agressive | Paramètres conservateurs par défaut, ajustables |
