# Quickstart — Leçon cliquable, discussion centrée (Feature 009)

Branch `009-lecon-discussion-centree` | Spec: [spec.md](./spec.md) | Plan: [plan.md](./plan.md)

Guide de validation de bout en bout. Référence les contrats et le modèle de données.

## Prérequis

- Venv : `source venv/bin/activate` (Python ≥ 3.11, repo `projet_ollama_tutor`).
- Suite verte : `venv/bin/pytest tests/ -q` (~100+ tests, hors-ligne via `httpx.MockTransport`).
- Aucun démon Ollama/llama.cpp requis pour tests automatisés.
- Pour test manuel UI : `venv/bin/uvicorn ollama_tutor.web.server:create_app --host 127.0.0.1 --port 9215` ou `edunexus` (si disponible), puis navigateur sur `http://127.0.0.1:9215`.

## Scénarios de validation (S1–S4)

### S1 — Ouvrir une discussion centrée depuis une leçon (US1, FR-001..003 / FR-009 / FR-014)

1. Créer un parcours (une des 3 méthodes) contenant une leçon « Variables » (via `POST /api/tutor/paths` + `POST /api/tutor/paths/{id}/steps` ou parcours adaptatif).
2. Lister le parcours : `GET /api/tutor/paths?learner_id=<id>` et `GET /api/tutor/paths/{id}` — chaque étape expose `status` (`not_started`|`in_progress`|`completed`) et `discussion_id`.
3. Cliquer la leçon dans l'UI (ou `POST /api/tutor/path-steps/{step_id}/discussion` avec header `X-Learner-Id`). Vérifier que la discussion s'ouvre avec `notion_id` visible et historique vide/isolé.
4. Vérifier que l'étape passe de `not_started` à `in_progress` dès l'ouverture.
5. Envoyer « c'est quoi une variable ? » dans la discussion — réponse RAG filtrée par mots-clés de la notion avec `sources` citées, pas de dérive vers une autre leçon. Deux leçons distinctes (« Variables » vs « Boucles ») ont des historiques séparés.

**API**:
```
POST /api/tutor/path-steps/{step_id}/discussion  Header: X-Learner-Id
GET  /api/tutor/lesson-discussions/{discussion_id}
```

**Tests** : `venv/bin/pytest tests/contract/test_lesson_api.py -q -k discussion` + `tests/unit/test_lesson_discussion.py -q -k isolation`

**Edge T025** :
- Leçon sans sources → UI affiche « Aucune source disponible… » + bouton « Importer des sources » (renvoie vers Bibliothèque).
- Leçon supprimée/réordonnée → `GET /api/tutor/lesson-discussions/{id}` reste 200 via `notion_id`; UI affiche bannière lecture seule.

### S2 — Générer un cours ou une synthèse (US2, FR-004 / FR-005 / FR-015)

1. Dans la discussion « Variables », cliquer **Générer le cours** (`POST /api/tutor/lesson-discussions/{id}/generate-course`). Vérifier contenu `lesson_course` 800–1200 mots, ancré dans les sources, avec `confidence`.
2. Recharger la discussion — le cours persiste dans `generated_contents`.
3. Cliquer **Faire une synthèse** sans cours préalable (`POST .../generate-summary`). Vérifier synthèse 150–250 mots générée directement depuis les sources (indépendante).
4. Avec un cours existant, refaire synthèse → synthèse dérivée du cours (même `sources`/`confidence` que le cours).
5. Vérifier que pendant une génération en cours les trois boutons (cours / synthèse / exercices) sont désactivés et un indicateur « génération en cours » s'affiche (T025).

**API**:
```
POST /api/tutor/lesson-discussions/{id}/generate-course
POST /api/tutor/lesson-discussions/{id}/generate-summary
```

**Tests** : `tests/unit/test_lesson_discussion.py -q -k "course or summary"` + `tests/contract/test_lesson_api.py -q -k "generate"`

**Erreurs** : échec LLM → 500 + message UI + entrée dans `data/errors.log` (ou `~/.config/ollama-tui/errors.log` selon `config_dir`) avec source `lesson-course` / `lesson-summary` (Constitution VI, T026).

### S3 — Faire des exercices sur la notion et clôturer la leçon (US3, FR-006..008)

1. Dans « Variables », cliquer **Faire des exercices** (`POST .../exercises`) — 3–5 questions typées selon le `PedagogicalTemplate` de la matière, régénérées à chaque appel (3,4,5 en rotation).
2. Répondre 2/5 (score < 60%) et soumettre (`POST .../exercises/{attempt_id}/submit` body `{"answers":{question_id: value}}`). Vérifier `score` 0.4, `passed` false, feedback par question, et que la leçon reste `in_progress`.
3. Cliquer **Marquer comme terminé quand même** (`POST .../complete` ou `.../complete-manual`) → statut `completed` et progression du parcours `x/n` mise à jour.
4. Relancer **Refaire** → 3–5 nouvelles questions. Répondre 4/5 (≥60%) → passage automatique à `completed` sans action manuelle.
5. Abandonner en cours (fermer la vue sans soumettre) → l'étape reste `in_progress`, reprise possible sans passage à `completed`.

**API**:
```
POST /api/tutor/lesson-discussions/{id}/exercises
POST /api/tutor/lesson-discussions/{id}/exercises/{attempt_id}/submit  {answers: {qid: ans}}
POST /api/tutor/lesson-discussions/{id}/complete
POST /api/tutor/lesson-discussions/{id}/complete-manual  (alias)
```

**Tests** : `tests/unit/test_lesson_discussion.py -q -k exercise` + `tests/contract/test_lesson_api.py -q -k exercise`

### S4 — Naviguer et retrouver l'état d'avancement (US4, FR-009..011)

1. Créer un parcours de 3 leçons. Terminer « Variables » (S3), laisser « Boucles » non commencée, ouvrir « Conditions » (passe en `in_progress`).
2. Vérifier dans `GET /api/tutor/paths` et dans la vue Parcours : badges `Non commencé` / `En cours` / `Terminée` et Barre progression `1/3 · 33%` + `progress_count` `1/3`.
3. Rouvrir « Variables » terminée — cours, synthèse, exercices et historique restaurés sans réinitialisation du statut.
4. Réordonner ou supprimer une leçon du parcours — la discussion associée reste consultable via `GET /api/tutor/lesson-discussions/{id}` (ancrage `notion_id`) en lecture seule.

**Tests** : `venv/bin/pytest tests/integration/test_lesson_flow.py -q`

## Validation de bout en bout

```bash
# 1. Suite complète hors-ligne
venv/bin/pytest tests/ -q

# 2. Contrats d'architecture (principe I : tutor/ sans fastapi/textual)
venv/bin/pytest tests/contract/test_tutor_imports.py -q

# 3. Syntaxe UI après modif tutor.html
python3 -c "
import re, pathlib, subprocess, tempfile, os
html=pathlib.Path('src/ollama_tutor/web/static/tutor.html').read_text()
m=re.search(r'<script[^>]*>(.*?)</script>', html, re.S)
open('/tmp/tutor_inline.js','w').write(m.group(1))
" && node --check /tmp/tutor_inline.js && echo "node --check OK"

# 4. Parité débit après modif client/render
MODEL=gemma3:1b ./benchmark.sh
```

## Critères de succès (rappel SC)

- SC-001 : ouvrir → cours → synthèse → exercices → terminée < 5 min hors lecture sur CPU.
- SC-003 : 95% des clics leçon ouvrent la discussion < 500 ms hors LLM, historique restauré.
- SC-004 : progression `x/n` traçable, re-ouverture ne réinitialise pas `completed`.
- SC-005 : 100% des échecs LLM/RAG → message UI + entrée `errors.log`.
