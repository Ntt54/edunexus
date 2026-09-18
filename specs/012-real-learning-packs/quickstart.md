# Quickstart — 012 Real Learning Packs

## Prérequis

```bash
source venv/bin/activate
./edunexus-local.sh &   # 127.0.0.1:9215 via data/ (EDUNEXUS_DATA_DIR)
curl -s http://127.0.0.1:9215/tutor -I | head -1  # 200
```

## Scénario 1 — Mémorisation active (US1)

1. Créer des cartes sur un chapitre, les noter en retard (SQL ou backdate).
2. Ouvrir l'app : la vue « À réviser » liste les dues en <10 s avec badge.
3. Lancer un quiz `recall_written` : répondre par écrit, vérifier le feedback correctif immédiat.
4. Lancer une séance mixte (3 types) : vérifier l'entremêlement (jamais de bloc unique) et la refonte surface des ratés.

```bash
curl -s "http://127.0.0.1:9215/api/tutor/reminders?subject_id=<id>&learner_id=<lid>"
```

## Scénario 2 — BEPC / probatoire / bac (US2)

1. `GET /api/tutor/packs` → vérifier `cm/terminale-c` (`squelette_à_valider`), chapitres au programme.
2. `POST /api/tutor/exams` avec `blueprint` + `duree_min` → copie chronométrée, verrouillage, `score_20`.
3. Rater un item d'annales → vérifier l'exercice de même compétence en séance suivante.
4. `POST /learners/<id>/share` → `GET /parent/overview?token=` → temps/maîtrise/erreurs/jalon ; `DELETE` → 403.

## Scénario 3 — Université (US3)

1. Question sur un PDF : chaque affirmation porte une citation cliquable vers le paragraphe exact.
2. Créer 5 notes atomiques liées → `GET /notes/atomic/plan?question=` → plan assemblé.
3. `POST /planner/semester` (5 UE + partiels) → planning <1 min, aucune semaine >150 %.

## Scénario 4 — Motivation + lisibilité (US4)

1. Refaire 20× le même exercice facile → vérifier le plafond (<10 % du XP d'une séance normale).
2. Activer le preset Dyslexie → vérifier contraste, interligne 1.5, 65ch, focus visible, TTS mot-à-mot.
3. Bookmarklet d'espacement 1.4.12 → aucune troncature.

## Vérifications automatiques

```bash
python3 -m pytest tests/ -q -k "012 or packs or exam or reminder or planner or readability or atomic or parent"  # regression gate
python3 -m pytest tests/contract -q -k "tutor_imports"   # gate I découplage
npm --prefix web/vue run build   # vue-tsc + vite (prefs lisibilité, nouvelles vues)
```

## Attendu

Après ces 4 scénarios, SC-001…SC-008 sont verts : rappels <10 s, 90 % de quiz avec rappel rédigé, épreuve blanche <2 min de préparation, 100 % d'erreurs avec correctif ciblé, 95 % d'affirmations citées, planning <1 min sans surcharge, anti-grinding, WCAG 2.2 AA.
