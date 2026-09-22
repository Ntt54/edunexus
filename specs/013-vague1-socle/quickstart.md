# Quickstart — 013 Vague 1 Socle Pédagogique

## Prérequis

```bash
source venv/bin/activate
./edunexus-local.sh &   # 127.0.0.1:9215 via data/ (EDUNEXUS_DATA_DIR)
curl -s http://127.0.0.1:9215/tutor -I | head -1  # 200
```

## Scénario 1 — Leçons fichiers + pièges (US1)

1. Déposer 10 fichiers leçons JSON (dont 3 invalides + 1 doublon) sous `tutor/data/lessons/`.
2. Recharger : les 7 valides servent les parcours, les 3 invalides sont loggés/skippés, warning doublon.
3. Générer un parcours sur une leçon à prérequis manquant : le prérequis vient d'abord, un piège classique apparaît dans le diagnostic.

## Scénario 2 — File par urgence (US2)

1. Créer 6 notions de stabilités étalées (2 overdue, 1 urgent, 1 warning, 2 ok).
2. `GET /api/tutor/adaptation/stability` → ordre overdue-first exact, niveaux conformes aux seuils fixes.
3. Noter une révision → urgences recalculées sans second moteur.

## Scénario 3 — Prompts éditables (US3)

1. Modifier `assets/prompts/tutor-system.md` (ton directif), recharger : le ton change.
2. Renommer/supprimer le fichier : FALLBACK intégré, fonctionnement nominal, incident loggé.
3. Vérifier qu'aucun secret n'est présent (`grep -ri "sk-\|api.key" assets/` → vide).

## Scénario 4 — Goldens (US4)

1. `python3 -m pytest tests/pedagogy/ -q` → vert sur les goldens initiaux (≥10 cas).
2. Introduire volontairement une solution complète dans un feedback mocké → le golden correspondant échoue en citant le fragment.

## Vérifications automatiques

```bash
python3 -m pytest tests/pedagogy/ tests/contract/test_013_forecast.py tests/integration/test_013_lesson_flow.py tests/integration/test_013_forecast_flow.py -q  # regression gate
python3 -m pytest tests/contract -q -k "tutor_imports"   # gate I découplage
python3 -m pytest tests/ -q  # suite complète verte
```

## Attendu

Après ces 4 scénarios, SC-001…SC-005 sont verts : chargement <1 s avec logs, urgences exactes, prompts éditables avec FALLBACK, goldens bloquants, zéro nouvelle dépendance.
