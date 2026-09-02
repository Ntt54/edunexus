# Quickstart — Validation d'EduNexus adaptatif (Feature 008)

**Phase 1 output** — Guide de validation de bout en bout. Référence les [contrats](./contracts/api.md) et le [modèle de données](./data-model.md) au lieu de les dupliquer.

## Prérequis

- Venv : `source venv/bin/activate` (Python ≥ 3.11).
- Suite existante verte : `venv/bin/pytest tests/ -q` (~391 tests).
- Aucun démon Ollama/llama.cpp requis pour les tests (flux simulés via `httpx.MockTransport`).
- Pour le test manuel de l'UI : `ollama serve` sur `localhost:11434` + `./ollama-tui` ou `edunexus` (port 9215).

## Scénarios de validation

### S1. Profil pédagogique de matière (US1, FR-001..006, SC-001)

1. Créer une matière « Java » via le modèle « Programmation ».
2. Vérifier que les activités et preuves sont préremplies, puis les modifier.
3. Rouvrir la matière : le profil est restauré tel quel.
4. Lancer « apprendre Java pour créer des projets » → paramètres internes générés.

**Test automatisé** : `venv/bin/pytest tests/unit/test_profiles.py -q` + `tests/contract/test_tutor_profiles_api.py -q`.

### S2. Graphe de compétences (US2, FR-007..011, SC-002)

1. Importer deux livres Java couvrant les mêmes concepts dans un ordre différent.
2. Construire le graphe : les notions communes sont fusionnées en un seul nœud avec références aux deux livres.
3. Consulter un nœud : prérequis, sources, confiance, raison de la position.
4. Vérifier que les propositions LLM sont distinguées du texte extrait et soumises à validation.

**Test automatisé** : `venv/bin/pytest tests/unit/test_graph.py -q` + `tests/contract/test_tutor_graph_api.py -q`.

### S3. Parcours explicable (US3, FR-012..015, SC-003/004)

1. Avec un profil Java et un graphe construit, générer le parcours.
2. Vérifier que chaque étape affiche « pourquoi maintenant », prérequis, sources, activité, preuve.
3. Vérifier que l'ordre respecte les prérequis (tri topologique).
4. Déplacer une étape → le changement est persisté et le parcours reste cohérent.

**Test automatisé** : `venv/bin/pytest tests/unit/test_path_builder.py -q`.

### S4. Adaptation après séance (US4, FR-016..019, SC-005/006)

1. Réussir puis échouer sur une notion.
2. Vérifier que seule la fenêtre proche du parcours est recalculée (pas l'ensemble).
3. Vérifier qu'une compétence n'est validée qu'après ≥2 preuves de types différents.
4. Vérifier que les objectifs de séance, la notion principale et le critère restent affichés.

**Test automatisé** : `venv/bin/pytest tests/unit/test_adaptation.py -q`.

### S5. Tableau de bord (US5, FR-020..022, SC-007)

1. Ouvrir le tableau de bord de la matière.
2. Vérifier les notions couvertes / non couvertes / contradictoires / incertaines.
3. Vérifier la distinction visuelle des 3 catégories (extrait / généré / confirmé).

**Test automatisé** : couvert par `tests/unit/test_graph.py` + `test_path_builder.py`.

### S6. Capture de programme par OCR (US6, FR-023..028, SC-009/010)

1. Importer une photo d'une table des matières (ou un PDF).
2. Vérifier que le texte est extrait localement, structuré en arbre éditable, avec statut de file visible.
3. Vérifier que les passages incertains sont signalés.
4. Corriger une erreur d'OCR avant génération du parcours → la correction est persistée.

**Test automatisé** : `venv/bin/pytest tests/unit/test_program_capture.py -q` + `tests/contract/test_tutor_capture_api.py -q`.

### S7. Import de photo dans une conversation (US7, FR-029..031, SC-011)

1. Dans une conversation, importer une photo d'un énoncé.
2. Vérifier que le texte est extrait localement, affiché et confirmable.
3. Vérifier que le tuteur répond en s'appuyant sur ce texte et le distingue de sa propre génération.

**Test automatisé** : `venv/bin/pytest tests/unit/test_conversation_photo.py -q`.

### S8. Carnet de matière (US8, FR-032..036, SC-012)

1. Ouvrir le carnet d'une matière, ajouter une note personnelle.
2. Lancer « résumer cette source » et « me questionner sans afficher la réponse ».
3. Vérifier que les sorties sont liées aux sources et supprimables.

**Test automatisé** : `venv/bin/pytest tests/unit/test_notebook.py -q`.

### S9. Multi-utilisateur familial (US9, FR-037..039, SC-013)

1. Créer deux profils d'apprenant.
2. Créer une matière distincte pour chacun (même nom possible).
3. Basculer entre profils : chaque membre ne voit que ses propres matières, parcours, progression, conversations.
4. Supprimer un profil : ses données sont supprimées en cascade, sans affecter l'autre.

**Test automatisé** : `venv/bin/pytest tests/unit/test_learners.py -q` + `tests/contract/test_tutor_learners_api.py -q`.

## Validation de bout en bout

```bash
# 1. Suite complète (hors-ligne)
venv/bin/pytest tests/ -q

# 2. Contrats d'architecture (principe I)
venv/bin/pytest tests/contract/test_tutor_imports.py -q

# 3. Syntaxe UI (après toute modif de tutor.html)
#    extraire le <script> inline puis :
node --check /tmp/tutor_inline.js

# 4. Parité de débit (après modif de client.py / chemins de rendu)
MODEL=gemma3:1b ./benchmark.sh
```

## Critères de succès mesurables (rappel)

- SC-001 : profil complet < 3 min via template.
- SC-003 : génération de parcours < 5 s.
- SC-005 : recalcul par fenêtre locale, jamais complet.
- SC-006 : maîtrise seulement après ≥2 preuves différentes.
- SC-008 : fonctionne sur CPU 8 Go RAM, hors-ligne.
- SC-009 : capture OCR < 30 s.
- SC-013 : isolation multi-utilisateur après bascule.
