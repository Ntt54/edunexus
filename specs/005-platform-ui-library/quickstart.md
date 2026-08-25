# Quickstart de validation — 005-platform-ui-library

**Date** : 2026-08-25 | Guide de validation manuelle de bout en bout.
Les détails d'implémentation vivent dans [data-model.md](./data-model.md) et
[contracts/api.md](./contracts/api.md).

## Prérequis

- Ollama actif (`ollama serve`) avec `gemma4:e2b` +
  `ibm/granite-embedding:107m-multilingual-q8_0`
- 2–3 PDF de test au titre évocateur (ex. « Fondamentaux Java.pdf »)
- Suite verte : `venv/bin/python -m pytest tests/ -q`

## Lancement

```bash
edunexus        # → http://127.0.0.1:9215/tutor
```

## Scénarios de validation

### S1 — Navigation multi-espaces (US5)
1. Ouvrir `/tutor` → vue par défaut = Conversations.
2. Cliquer chaque entrée de navigation → un seul espace visible à la fois,
   entrée active surlignée.
3. Démarrer un exercice dans Entraîner, aller dans Bibliothèque, revenir →
   l'exercice est dans son état.
✅ Attendu : aucune fonctionnalité manquante ; écran jamais surchargé.

### S2 — Conversation persistante (US1)
1. Créer « Apprendre Java », poser une question → réponse + tok/s affichés.
2. Créer « Réseaux — TCP/IP », poser une autre question.
3. Basculer entre les deux via la liste → historiques distincts intacts.
4. Renommer « Sans titre » → le nom persiste après F5 et après arrêt/relance
   du serveur.
✅ Attendu : zéro perte d'historique ; création n'a rien effacé.

### S3 — Bibliothèque hiérarchique (US2)
1. Importer « Fondamentaux Java.pdf » dans domaine Programmation /
   catégorie Java → réponse immédiate `{book_id, status:"indexing"}`.
2. Ouvrir Bibliothèque → arborescence Programmation ▸ Java ▸ document,
   statut passant `indexing` → `ready`.
3. Rechercher « fondam » → le document est retrouvé avec son chemin.
4. Le déplacer vers une catégorie « Exercices » → déplacement sans
   ré-indexation (statut reste `ready`).
✅ Attendu : compteurs exacts ; recherche < 1 s.

### S4 — Sélection par groupe + individuelle (US3)
1. Ajouter 2 autres PDF en catégorie Java.
2. Cocher la catégorie Java → les 3 documents cochés (compteur +3).
3. Décocher un seul livre → catégorie en état partiel ; les 2 autres restent
   cochés.
✅ Attendu : un document partagé entre deux catégories reflète un état unique.

### S5 — Sources actives & RAG scopé (US4)
1. Dans la conversation « Apprendre Java » : bouton « Sources » → cocher
   uniquement Java → Appliquer (résumé « Java · N documents »).
2. Poser une question couverte → réponse AVEC citations issues de la sélection.
3. Vider les sources actives → poser une question → réponse sans contexte,
   indicateur « Aucune source active ».
4. Vérifier que la bibliothèque n'a pas bougé (aucun document supprimé/déplacé).
✅ Attendu : seules les sources actives alimentent les réponses.

### S6 — Tableau de bord (US6)
1. Avec révisions dues + conversations + source récente : ouvrir Accueil.
2. Chaque carte (À réviser, Notion en cours, Conversations, Sources,
   Évaluations) mène à son espace en un clic.
✅ Attendu : lecture < 30 s ; état vide propre en première utilisation.

### S7 — Classification proposée (US7)
1. Importer un PDF au titre explicite → suggestion de catégorie affichée.
2. Accepter en un clic ; puis importer un second PDF et modifier la
   proposition avant validation.
✅ Attendu : les deux chemins aboutissent au rangement choisi.

### S8 — Robustesse (edge cases)
1. Supprimer une catégorie cochée comme source active → documents sortis des
   sources actives, conversation fonctionnelle, opération journalisée dans
   `~/.config/ollama-tui/errors.log`.
2. Supprimer l'espace d'une conversation → conversation ouvable, marquée
   « espace supprimé ».
3. Re-import du même fichier → déduplication (pas de doublon).

## Gates automatiques

- `venv/bin/python -m pytest tests/ -q` → tout vert (contrats nouveaux inclus)
- `node --check` sur le script inline de `tutor.html` → OK
- Aucune route existante modifiée/supprimée (diff server.py additive)
