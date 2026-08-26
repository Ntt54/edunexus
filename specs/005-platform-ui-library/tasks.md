# Tasks: Plateforme multi-vues & bibliothèque de connaissances

**Input**: Design documents from `/specs/005-platform-ui-library/`

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · contracts/api.md ✅ · quickstart.md ✅

**Tests**: Inclus — le principe III de la constitution (`.specify/memory/constitution.md`) rend la couverture hors-ligne non négociable ; chaque story porte ses contrats/tests.

**Organization**: Tasks groupées par user story (US1–US8 de spec.md) pour implémentation et livraison indépendantes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallélisable (fichiers différents, aucune dépendance incomplète)
- **[Story]**: story propriétaire (US1…US8) — phases Setup/Foundational/Polish sans label
- Chemins exacts exigés dans chaque description

## Path Conventions

Projet unique : `src/ollama_tutor/`, `tests/{contract,integration,unit}/`,
UI = `src/ollama_tutor/web/static/tutor.html`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Vérifier la baseline : suite complète verte (`venv/bin/python -m pytest tests/ -q`, ≥209) et `node --check` sur le script inline de `src/ollama_tutor/web/static/tutor.html`
- [X] T002 [P] Créer le squelette `src/ollama_tutor/tutor/conversations.py` : docstring de service, classe `ConversationService` avec signatures prévues par `contracts/api.md` (create/list/get/rename/delete/set_sources/get_sources), corps `raise NotImplementedError`

**Checkpoint Phase 1**: suite verte, squelette compilable.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: couche données partagée par US1 (conversations) et US4 (sources actives). AUCUNE story ne démarre avant complétion.

- [X] T003 Migration idempotente dans `src/ollama_tutor/tutor/store.py` : `ALTER TABLE sessions ADD COLUMN title TEXT` + `ADD COLUMN updated_at REAL` sous garde PRAGMA-table_info (style maison) ; backfill `updated_at=started_at` au premier listage
- [X] T004 Table `conversation_sources(conversation_id TEXT, book_id TEXT, PK composée, FK ON DELETE CASCADE)` dans `src/ollama_tutor/tutor/store.py` + méthodes `set_conversation_sources(id, book_ids)`, `get_conversation_sources(id)`, nettoyage cascade sur suppression session/livre
- [X] T005 Tests unitaires `tests/unit/test_store_conversations.py` : migration idempotente (réouverture), backfill, set/get/clear sources, cascade suppression livre/session

**Checkpoint Phase 2**: suite verte incluant T005 ; schéma prêt pour US1 et US4.

---

## Phase 3: US1 — Conversations persistantes indépendantes (P1)

**Story Goal**: créer/basculer/renommer des conversations nommées sans jamais perdre d'historique.

**Independent Test**: deux conversations avec questions différentes, bascule, arrêt/relance serveur → historiques et titres intacts.

- [X] T010 [US1] Implémenter `ConversationService` dans `src/ollama_tutor/tutor/conversations.py` : create (session vierge), list (TOUTES les sessions triées updated_at desc, avec subject_name/message_count/active_sources), rename, delete (cascade messages + sources via store, journalisée `_log_error` si échec)
- [X] T011 [US1] Routes REST dans `src/ollama_tutor/web/server.py` : GET/POST `/api/tutor/conversations`, GET/PATCH/DELETE `/api/tutor/conversations/{id}` (formes exactes de contracts/api.md §1 ; 404 inconnue)
- [X] T012 [US1] Tests contrat `tests/contract/test_conversations_api.py` : CRUD complet, 404, renommage persistant, suppression cascade (messages + sources), aucune autre conversation affectée
- [X] T013 [US1] UI vue Conversations dans `src/ollama_tutor/web/static/tutor.html` : liste (titre/espace/date/nb messages), boutons créer/renommer (inline)/supprimer (confirm), ouverture → historique rejoué via mécanisme `resume` existant ; état par conversation restauré
- [X] T014 [US1] Test intégration `tests/integration/test_conversations_persist.py` : créer 2 conversations + messages via TestClient/WS, redémarrer l'app (nouvelle create_app), rouvrir → historiques distincts intacts

**Checkpoint**: MVP livrable — conversations utilisables au quotidien.

---

## Phase 4: US2 — Bibliothèque hiérarchique (P1)

**Story Goal**: gérer les documents en arborescence Domaine → Catégorie → Documents avec recherche et statuts.

**Independent Test**: 3 PDF rangés dans 2 catégories → arborescence correcte, recherche par titre fonctionnelle, compteurs exacts.

- [X] T015 [P] [US2] Vue Bibliothèque arborescente dans `src/ollama_tutor/web/static/tutor.html` : regroupement Domaine (subjects) ▸ Catégorie ▸ Documents depuis endpoints existants, pliage/dépliage, compteur par nœud
- [X] T016 [P] [US2] Recherche par titre filtrant l'arbre avec chemin hiérarchique affiché + état vide explicite (aucun résultat) dans `tutor.html`
- [X] T017 [US2] Statut d'indexation temps réel par document (badge `indexing|ready|error`) via polling `GET /api/tutor/index-status` existant dans `tutor.html`
- [X] T018 [US2] Déplacement de document vers une autre catégorie (membership PUT existant) + confirmation obligatoire à la suppression d'un conteneur non vide avec choix du devenir, dans `tutor.html`
- [X] T019 [US2] Test contrat `tests/contract/test_library_move.py` : déplacement conserve le statut `ready` et ne déclenche aucune ré-indexation ni perte de chunks

**Checkpoint**: gestionnaire de connaissances utilisable seul.

---

## Phase 5: US3 — Sélection multi-niveaux & groupes (P2)

**Story Goal**: cocher domaines/catégories/documents avec états tri-états et déduplication.

**Independent Test**: catégorie cochée = tous ses docs cochés ; décocher un doc → conteneur partiel ; doc multi-catégories = état unique.

- [X] T020 [US3] Module d'état de sélection client dans `src/ollama_tutor/web/static/tutor.html` (`S.selection` : Set de book_ids, dédupliqué quelle que soit la catégorie) + cases documents
- [X] T021 [US3] Cases conteneurs tri-états (tout/partiel/vide) avec action coche-tout/décoche-tout + compteur global « documents actifs » dans `tutor.html`
- [X] T022 [US3] Validation : `node --check` + parcours manuel S4 du quickstart (sélection groupe, partielle, multi-catégories)

**Checkpoint**: sélection exploitable ; prépare US4 sans dépendance backend nouvelle.

---

## Phase 6: US4 — Sources actives par conversation (P2)

**Story Goal**: chaque conversation applique son propre périmètre RAG ; sélecteur popover dans la conversation.

**Independent Test**: sélection limitée à Java → question hors sélection répond sans citer ces livres ; bibliothèque inchangée.

**Dépendance**: Phases 3 (conversations) + 5 (état sélection).

- [X] T023 [US4] Frame WS `ask` : champs optionnels `conversation_id` et `book_ids` dans `src/ollama_tutor/web/server.py` (résolution périmètre : book_ids prioritaire, sinon sources de la conversation, sinon illimité)
- [X] T024 [US4] Filtre `book_ids` optionnel dans `src/ollama_tutor/tutor/retrieval.py` (filtrage passages avant scoring) + paramètre transmis par `service.ask()` dans `src/ollama_tutor/tutor/service.py`
- [X] T025 [US4] Routes GET/PUT `/api/tutor/conversations/{id}/sources` dans `src/ollama_tutor/web/server.py` (contrat api.md §2 ; dédupliqué, ids inconnus ignorés)
- [X] T026 [US4] Tests contrat `tests/contract/test_rag_scope.py` : question couverte par livre hors sélection ⇒ aucune citation de ce livre ; liste vide ⇒ mode sans contexte ; périmètre prioritaire sur défaut
- [X] T027 [US4] UI sélecteur popover « Sources : … » dans `src/ollama_tutor/web/static/tutor.html` : recherche, arborescence avec cases, Annuler/Appliquer, bouton résumé permanent (« Java · 4 documents »), indicateur « Aucune source active », persistance par conversation

**Checkpoint**: RAG scopé opérationnel de bout en bout.

---

## Phase 7: US5 — Navigation principale multi-espaces (P2)

**Story Goal**: coquille de navigation reliant les 8 espaces ; relocalisation exhaustive sans perte.

**Independent Test**: parcourir les 8 entrées → chaque fonctionnalité accessible exactement une fois ; retour = état préservé.

**Dépendance**: Phases 3–6 (les vues à relocaliser existent).

- [X] T028 [US5] Coquille de routage par hash (`#/accueil`, `#/conversations`, `#/bibliotheque`, `#/apprentissage`, `#/entrainer`, `#/quiz`, `#/progression`, `#/explorer`) + barre de navigation persistante + sections vues masquées/montées dans `src/ollama_tutor/web/static/tutor.html`
- [X] T029 [US5] Relocaliser les blocs existants selon la matrice de `research.md` §5 (import/recherche → Bibliothèque ; chat/vocal/réglages → Conversations ; notions/révisions → Apprentissage ; exercices → Entraîner ; quiz/examens → Quiz/Examens ; progress → Progression ; glossaire/comparer/carte/localiser/rank → Explorer) dans `tutor.html`
- [X] T030 [US5] Préservation d'état inter-vues (exercice en cours, filtres, position) + navigation compacte responsive dans `tutor.html`
- [X] T031 [US5] Audit final : grep croisé endpoints ↔ server.py, `node --check`, matrice « zéro perte » revérée élément par élément

**Checkpoint**: plateforme naviguable complète.

---

## Phase 8: US6 — Tableau de bord synthétique (P3)

**Story Goal**: Accueil agrégeant révisions dues, notion en cours, conversations récentes, dernières sources, prochaine évaluation — cliquable.

**Independent Test**: données d'exemple → chaque carte affiche et mène à son espace ; première utilisation → cartes vides propres.

- [X] T032 [US6] Vue Accueil dans `src/ollama_tutor/web/static/tutor.html` : composition des endpoints existants (gaps/progress, conversations, books, examens) en cartes résumé
- [X] T033 [US6] Cartes cliquables vers leur espace + états vides « première utilisation » (inviter import/conversation) dans `tutor.html`

---

## Phase 9: US7 — Classification proposée à l'import (P3)

**Story Goal**: suggestion de rangement acceptée/modifiable en un clic lors de l'import.

**Independent Test**: titre explicite → proposition cohérente applicable en un clic ; modification possible.

- [X] T034 [US7] Suggestion de catégorie à l'import par correspondance floue titre ↔ catégories existantes (sans appel LLM) dans le panneau d'import de `src/ollama_tutor/web/static/tutor.html` (accepter / choisir autre)
- [X] T035 [US7] Vérifier non-régression de la classification par lots existante (« Classer automatiquement ») après intégration — parcours manuel + tests existants verts

---

## Phase 10: US8 — Emplacement Parcours réservé (P3)

- [X] T036 [US8] Entrée « Parcours » dans la navigation, désactivée avec badge « à venir », + commentaire architecture pointant data-model.md §Parcours dans `src/ollama_tutor/web/static/tutor.html`

---

## Phase 11: Polish & Cross-Cutting

- [X] T037 Audit final de la matrice de relocalisation (research.md §5) : chaque fonctionnalité actuelle accessible exactement une fois, aucune régression (liste de contrôle manuelle + suite)
- [X] T038 [P] Relancer `./benchmark.sh` (chemins de rendu touchés) et comparer tok/s à la baseline (3,83 tok/s API)
- [X] T039 Suite complète verte + quickstart S1–S8 passés manuellement et cochés
- [X] T040 Mettre à jour `README.md` et `AGENTS.md` (navigation multi-espaces, commande edunexus, port 9215 déjà documentés — compléter si écart)

---

## Dependencies & Execution Order

```text
Phase 1 (Setup) → Phase 2 (Foundational: schéma)
   → Phase 3 (US1 conversations)
      → Phase 4 (US2 bibliothèque)  ─┐
      → Phase 5 (US3 sélection) ─────┤
      → Phase 6 (US4 sources actives)◀┘ (dépend US3+US1)
         → Phase 7 (US5 shell navigation, dépend US1–US4)
            → Phase 8 (US6 dashboard) · Phase 9 (US7 classif) · Phase 10 (US8 parcours)
               → Phase 11 (Polish)
```

- US2 et US3 peuvent s'exécuter en parallèle après US1 (fichiers/UI distincts).
- US4 nécessite US1 (conversations) + US3 (état sélection).
- US5 nécessite US1–US4 (relocalisation des vues construites).
- US6/US7/US8 indépendants entre eux, après US5.

## Parallel Execution Examples

- Après Phase 2 : T015/T016 (US2) ∥ T020 (US3) ∥ T010 (US1 service) sur fichiers distincts.
- Phase 6 : T023/T024/T025 touchent server.py/retrieval/service — séquencer server.py en dernier ; T026 rédigé en parallèle.
- Phase 8/9/10 entièrement parallélisables entre elles (tutor.html séquentialisé sinon).

## Implementation Strategy

- **MVP = Phase 3 seule** (US1) : valeur quotidienne immédiate, livrable et démontrable indépendamment.
- Incréments suivants : US2 → US3 → US4 (boucle bibliothèque↔conversation complète), puis US5 (plateforme), puis US6–US8.
- Chaque checkpoint de phase = suite verte + validation manuelle du quickstart correspondant.
