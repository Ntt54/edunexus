# Contract — Contract test web (P1-B)

## `tests/contract/test_web_contract.py`

1. **Extraction front** : regex `fetch\s*\(\s*["'`]([^"'`]+)["'`]` sur
   `web/static/tutor.html` (+ méthodes via `method:\s*["'](GET|POST|…)` à
   proximité, défaut `GET`). Ne suit que les chemins `/api/…` (préfixe
   `/tutor` normalisé).
2. **Extraction back** : `app.get|post|put|delete|patch|websocket(` dans
   `web/server.py` (+ routers inclus), chemins template `{param}` → joker.
3. **Assertions** :
   - tout endpoint fetché matche une route (même méthode) ;
   - toute route `/api/…` non-WS est fetchée par au moins un espace
     (tolérance : liste explicite `IGNORED_ROUTES`, ex. `/api/log-error`
     appelé depuis le handler global, `/api/health` pour sondes).
4. **Garanties** : test offline pur (lecture fichiers + import routes sans
   démarrer le serveur) ; < 2 s ; message d'échec listant les orphelins
   avec fichier:ligne.

## Gate associé

`node --check` sur le JS inline de `tutor.html` après toute modification UI
(gate existant, rappelé ici car le lot touche l'affichage des statuts jobs).
