# Quickstart — 011 Subject & Learner Context Isolation

## Prérequis

```bash
source venv/bin/activate
./edunexus-local.sh &   # 127.0.0.1:9215 STD via data/ (EDUNEXUS_DATA_DIR)
curl -s http://127.0.0.1:9215/tutor -I | head -1  # 200
```

## Scénario 1 — Isolation par matière (P1)

1. Créer 2 matières: header → "+ Matière" → "Non classé" (existe), "java".
2. Dans "Non classé": importer 1 PDF Python, vérifier Accueil montre "Introduction à Python" et badge "Matière active: Non classé".
3. Switch header → "java": Accueil doit afficher état vide (0%, pas de "Introduction Python"), badge "Matière active: java". Recharger (`Cmd+R`): doit rester "java".
4. Mon parcours en "java": liste vide, `GET /api/tutor/learning-paths?subject_id=<javaId>&learner_id=0742a64a` → `{"paths": []}`.

## Scénario 2 — Rename & Delete "Non classé" (P2)

1. Renommer "Non classé" → "Python": `PATCH /api/tutor/subjects/<id> {"name":"Python"}` → 200; sélecteur, Accueil, cartes parcours affichent "Python".
2. Tenter rename vers "java" (existant) → 400 "Nom déjà utilisé".
3. Supprimer "Python" (qui est "Non classé" renommée) → modale confirmation → DELETE → 200, fallback vers "java", toast "Matière supprimée".

## Scénario 3 — Apprenants filtrés par matière (P3, Q3=B)

1. En "java" (#/apprenants): "Créer apprenant" → "Alice" → liste montre Alice seule (pas ceux de "Python").
2. Switch header → "Python": liste change (ex: 0742a64a). Activer Alice → `POST /api/tutor/learners/<aliceId>/activate` → header reflète Alice.
3. Badge "Matière active" sur #/apprenants doit suivre header ("java" vs "Python").

## Scénario 4 — Bibliothèque filtrée avec toggle (Q4=C)

1. `GET /api/tutor/books?subject_id=<javaId>` → seulement livres java.
2. Toggle "Toutes" → `GET /api/tutor/books` ou `?all=true` → tous livres.
3. Préférence persistée après reload.

## Vérifications automatiques

```bash
venv/bin/pytest tests/ -q -k "011 or subject or learner or path"   # regression gate
venv/bin/pytest tests/contract -q -k "web_contract"   # allowlist incluse
npm --prefix web/vue/client run build   # vue-tsc + vite, vérifie invalidation caches
```

## Attendu

Après ces 4 scénarios, `spec.md` SC-001…SC-005 sont verts: aucun mélange de données entre matières sur 10 switches, rename 100% persistant, cohérence header/badge 95%+.
