# Research: 012 Real Learning Packs

**Feature**: 012 | **Date**: 2026-09-18 | **Sources**: inventaire code (exp-4), pédagogie factuelle (lib-1), spécificités techniques (lib-2), touch-points (exp-5)

## Décisions

### D1. Rappels sans push : SQLite due + poll au lancement/focus + badges in-app
- **Decision**: V1 = `due_reviews` existant enrichi (compteurs, retard) + interrogation à l'ouverture et au focus + badge persistant « N révisions dues » + vue « À réviser ». Hook CLI `edunexus --check-due` optionnel pour cron OS. Ni thread de polling, ni notification push, ni nouvelle dépendance.
- **Rationale**: Offline-first, ≤8 Go RAM : aucun daemon. Les badges persistants surpassent les toasts éphémères pour le hors-ligne (pattern AnkiDroid/Brainscape, schéma `cards(did,queue,due)` d'Anki).
- **Alternatives considered**: Notifications OS natives (rejeté : infra par plateforme, sort du périmètre local) ; thread de fond (rejeté : coût RAM permanent pour un besoin ponctuel).

### D2. Quiz à rappel rédigé : nouveau kind `recall_written` + juge LLM existant
- **Decision**: Ajouter `recall_written` à `_VALID_Q_KINDS`, rendu réponse cachée/écrite côté `QuizView`, correction via `grade_answer` (juge LLM, fallback exact offline). L'effet test vient du rappel effortful, pas de la reconnaissance.
- **Rationale**: Réutilise `QuizEngine`/`submit_answers`/XP ; le juge LLM existe déjà pour `open`/`code`.
- **Alternatives considered**: Moteur d'évaluation séparé (rejeté : duplication, principe II).

### D3. Entremêlement : échantillonneur remplaçant le round-robin
- **Decision**: Remplacer l'ordre `concepts[i%len]/kinds[i%len]` (`assessment.py:871`) par un échantillonneur entremêlé (mix de 3 types, refonte surface des ratés en fin de séance), paramétrable depuis `adaptation.py` (WINDOW_SIZE conservé ou paramétré).
- **Rationale**: L'entremêlement vaut ~3× mieux à une semaine (Rohrer & Taylor, Dunlosky) ; aucun séquenceur n'existe, le round-robin est le seul point d'ordre.
- **Alternatives considered**: Planification externe par le LLM (rejeté : non déterministe, coûteux, non testable offline).

### D4. Packs curriculum JSON versionnés, squelettes sans textes sous droit
- **Decision**: `data/curriculum/cm/{bepc,premiere,terminale-{a,c,d}}.json`, enveloppe `{schemaVersion, packVersion, minAppVersion, source}`, validation en stdlib `json` (pas de `jsonschema`), checksum en table `packs`, progression en tables séparées. Statut `squelette_à_valider`, coeffs BEPC/probatoire à `null` + `source: "OBC/MINESEC à confirmer"` (jamais copier les sites d'annales).
- **Rationale**: Offline-first, versionnable, migrations in-memory ; hiérarchie `cycle → classe → matière → chapitre {objectifs, durée, prérequis}` inspirée MDN/freeCodeCamp.
- **Alternatives considered**: Tables SQL seedées (rejeté : mélange données livrées/progression utilisateur) ; import utilisateur seul (rejeté par clarification : hybride décidé).

### D5. Épreuves blanches : `create_exam` étendu + barème /20 + blueprints OBC
- **Decision**: Réutiliser `create_exam` (déjà `allow_help=False`, `time_limit_s`, auto-submit) + scaler /20 (moyenne pondérée, Passable ≥10) + verrouillage + reprise « interrompue » + blueprints par examen (BEPC/1ère/Terminale A/C/D, durées/coeffs OBC vérifiés humainement avant v1).
- **Rationale**: Le formalisme d'examen existe (modèle `ExamSession`, 409 sur aide) ; il manque le barème et le verrouillage.
- **Alternatives considered**: Nouveau moteur d'examen (rejeté : principe II).

### D6. Vue parent : consentement sur LearnerProfile + route lecture seule
- **Decision**: Champs `consent_parent` + `share_token` sur le profil, route GET agrégée (temps, maîtrise, erreurs, jalons) à token, révocable. Parents seuls en v1 (clarification).
- **Rationale**: Données de mineur : partage explicite ; lecture seule via token évite tout modèle de rôles.
- **Alternatives considered**: Comptes parents avec auth (rejeté : pas d'auth multi-rôle en local v1) ; assignation prof (différé par clarification).

### D7. Citations : `SourceReference` existant rendu inline
- **Decision**: Le chemin chunks→`SourceReference`→`{answer,sources}` existe (`lesson_discussion.py:599→635→652`) ; ajouter le rendu inline (affirmation → paragraphe exact, un clic) dans `LessonView`, même patron que `notebook.py:138`.
- **Rationale**: Zéro changement modèle ; contrat anti-hallucination standard (pattern Audeus : citations inline vers paragraphes exacts).
- **Alternatives considered**: Nouveau pipeline de grounding (rejeté : le chaînage existe et est testé).

### D8. Notes atomiques : table dédiée, carnet existant conservé
- **Decision**: Nouvelle table `atomic_notes {id, title, body_own_words, links[], concept_ids, source_refs}` + assemblage de plan depuis les liens ; `notes:string[]` du carnet inchangé (compat).
- **Rationale**: Une idée/note + liens explicites (précise/contredit/mécanisme-de) = élaboration + organisation (Zettelkasten-lite) ; le carnet RAG reste la capture brute.
- **Alternatives considered**: Réutiliser `notes:string[]` avec convention de format (rejeté : non requêtable, fragile).

### D9. Planning semestre : service dédié, règle 150 %
- **Decision**: Nouveau `tutor/planner.py` : charge par UE + dates d'épreuves → répartition hebdomadaire (1 ECTS = 25-30 h, guide ECTS) + créneaux révision/simulation, aucune semaine >150 % de la moyenne, recompaction plafonnée après absence.
- **Rationale**: 30 ECTS ≈ 750-900 h : sans planificateur, bachotage garanti ; `memory.py` ne fait que détecter des mots-clés, aucun objet planning n'existe.
- **Alternatives considered**: Règles dans `service.py` (rejeté : gonflement, extraction par domaine préférée).

### D10. XP sain : pondération + décroissance + caps + série hebdo
- **Decision**: `XP = base(difficulté) × decay(répétition <7j)` aux points d'appel existants (`service.py:2211/2686`), cap journalier, série hebdomadaire + 1 joker, 25 % max pour re-do même item même jour. Sources : GrowthEngineering (pondération), Smashing/Chou (décroissance, joker).
- **Rationale**: Les streaks retiennent mais dérivent vers l'anxiété et le grinding ; XP proportionné à l'effort + récupération obligatoire.
- **Alternatives considered**: Ligues/leaderboards (rejeté en v1 : opt-in futur, risque de punir les assidus).

### D11. Lisibilité WCAG 2.2 AA en CSS pur
- **Decision**: Pref `readability:{fontScale≤200%, lineHeight 1.0/1.5, letterSpacing, theme}` + variables CSS + preset « Dyslexie » BDA (Verdana/Arial 17-19px, 1.5, fond crème `#FDF6E3` + texte `#1A1A1A` vérifié ≥4.5:1, gras seul, 65ch, sans justification) + anneau de focus 2px visible + colonne unique. Vérif : bookmarklet d'espacement 1.4.12, zéro troncature.
- **Rationale**: WCAG 2.2 AA décidé en clarification ; zéro dépendance (CSS variables, `setTimeout` existant) ; BDA : familiarité + taille + espacement > « police dys » dédiée (preuves faibles).
- **Alternatives considered**: Librairie d'accessibilité (rejeté : principe V) ; TTS serveur (rejeté : `speechSynthesis` navigateur suffit en v1).

## Inconnues résolues (toutes levées, aucune NEEDS CLARIFICATION restante)

- Barèmes/coeffs OBC : table bac récupérée (français A/ABI 4h coeff 3, maths C coeff 7, physique 4h coeff 5) mais OCR bruité → **relecture humaine obligatoire avant v1**, squelettes marqués `squelette_à_valider`.
- BEPC : aucune table officielle trouvée en crawl ouvert → coeffs `null`, pas d'invention.
- Structure probatoire : mêmes séries que le bac (A5 probatoire uniquement) ; détails dans la famille d'arrêtés 2022.
