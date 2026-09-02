# API Contracts — EduNexus adaptatif (Feature 008)

**Phase 1 output** — Contrats d'interface pour les nouveaux endpoints REST/WS exposés par `web/server.py`. Tous les endpoints délèguent aux services `tutor/` (principe I) et sont testés hors-ligne via `httpx.MockTransport` (principe III).

Conventions communes :
- Base URL : `http://127.0.0.1:9215`
- Toutes les réponses JSON ; erreurs → `{"error": "<message>"}` avec code HTTP approprié.
- Le profil d'apprenant actif est transmis via en-tête `X-Learner-Id` (ou paramètre de requête) ; les endpoints filtrent par ce profil (FR-037/FR-038).
- Les éléments `ai_proposed` sont toujours marqués et soumis à validation (anti-hallucination).

## 1. Profils d'apprenant (multi-utilisateur familial)

### `GET /api/tutor/learners`
Liste les profils d'apprenant.
```json
200 → {"learners": [{"id": "…", "name": "…", "avatar": "…", "active": true}]}
```

### `POST /api/tutor/learners`
Crée un profil.
```json
Body: {"name": "Thierry", "avatar": "👨‍🎓"}
201 → {"learner": {"id": "…", "name": "Thierry", "avatar": "👨‍🎓"}}
```
Erreur : `400` si `name` vide.

### `POST /api/tutor/learners/{learner_id}/activate`
Bascule le profil actif.
```json
200 → {"active_learner": {"id": "…", "name": "…"}}
```

### `DELETE /api/tutor/learners/{learner_id}`
Supprime un profil et ses données en cascade (matières, graphes, parcours, progression, conversations, carnet).
```json
200 → {"deleted": true}
```

## 2. Profil pédagogique de matière

### `GET /api/tutor/subjects/{subject_id}/profile`
```json
200 → {"profile": {"subject_id": "…", "domain": "…", "level": "…", "objective": "…",
  "deadline": "…", "available_time": "…", "prerequisites": [], "competencies": [],
  "explanation_style": "…", "activities": [], "mastery_criteria": [], "template_id": "…"}}
```

### `PUT /api/tutor/subjects/{subject_id}/profile`
```json
Body: {"domain": "…", "level": "…", "objective": "…", "activities": [], "mastery_criteria": []}
200 → {"profile": {…}}
```
Erreur : `400` si `domain`/`objective` manquants (FR-001).

### `GET /api/tutor/pedagogical-templates`
```json
200 → {"templates": [{"id": "…", "name": "Programmation", "activities": [], "proof_types": []}]}
```

### `POST /api/tutor/subjects/{subject_id}/profile/interpret-goal`
Convertit un objectif en langage courant en paramètres pédagogiques (FR-004).
```json
Body: {"goal": "apprendre Java pour créer des projets"}
200 → {"parameters": {"approach": "projet", "debugging": "high", "practice": "high", "progression": "prereq"}}
```

## 3. Graphe de compétences

### `GET /api/tutor/subjects/{subject_id}/graph`
```json
200 → {"nodes": [{"id": "…", "title": "…", "mastery_score": 0.0, "confidence": 0.9,
  "validation_status": "extracted", "sources": []}],
  "edges": [{"id": "…", "source": "…", "target": "…", "relation": "requires", "confidence": 0.8}]}
```

### `POST /api/tutor/subjects/{subject_id}/graph/build`
Construit/rafraîchit le graphe depuis les livres importés (règles déterministes + candidats LLM).
```json
200 → {"nodes": N, "edges": M, "ai_proposed": K}
```

### `POST /api/tutor/graph/nodes/{node_id}/validate`
Valide un nœud `ai_proposed` → `user_confirmed` (FR-010).
```json
200 → {"node": {"id": "…", "validation_status": "user_confirmed"}}
```

## 4. Parcours explicable

### `POST /api/tutor/subjects/{subject_id}/path/generate`
Génère le parcours (sélection + tri topologique).
```json
200 → {"path": {"id": "…", "steps": [{"id": "…", "ordinal": 1, "why_now": "…",
  "prerequisites": [], "sources": [], "planned_activity": "…", "expected_proof": "…"}]}}
```

### `PUT /api/tutor/subjects/{subject_id}/path`
Réordonne / fusionne / exclut des étapes (FR-014).
```json
Body: {"steps": [{"id": "…", "ordinal": 1}, {"id": "…", "ordinal": 2, "excluded": true}]}
200 → {"path": {…}}
```

## 5. Capture de programme par OCR (photo/PDF)

### `POST /api/tutor/subjects/{subject_id}/program/capture`
Démarre la capture (photo ou PDF) ; traitement incrémental via file d'attente avec statut visible (FR-023).
```json
Body: {"file": "<multipart>", "source_type": "photo|pdf"}
202 → {"program": {"id": "…", "status": "processing", "queue_position": 1}}
```

### `GET /api/tutor/subjects/{subject_id}/program/{program_id}`
```json
200 → {"program": {"id": "…", "status": "ready", "recognized_text": "…",
  "nodes": [{"id": "…", "title": "…", "kind": "chapter", "origin": "ocr",
    "validation_status": "pending"}]}}
```

### `PUT /api/tutor/subjects/{subject_id}/program/{program_id}/nodes/{node_id}`
Corrige un nœud OCR avant génération du parcours (FR-027).
```json
Body: {"title": "…", "validation_status": "corrected"}
200 → {"node": {…}}
```

### `POST /api/tutor/subjects/{subject_id}/program/{program_id}/confirm`
Confirme le programme validé (passe `confirmed`).
```json
200 → {"program": {"id": "…", "validation_status": "confirmed"}}
```

## 6. Import de photo dans une conversation

### `POST /api/tutor/conversations/{conversation_id}/photo`
Importe une photo dans une conversation (FR-029).
```json
Body: {"file": "<multipart>"}
202 → {"photo": {"id": "…", "recognized_text": "…", "confirmation_status": "pending"}}
```

### `POST /api/tutor/conversation-photos/{photo_id}/confirm`
Confirme le texte reconnu avant utilisation par le tuteur (FR-031).
```json
200 → {"photo": {"id": "…", "confirmation_status": "confirmed"}}
```

## 7. Carnet de matière

### `GET /api/tutor/subjects/{subject_id}/notebook`
```json
200 → {"notebook": {"id": "…", "subject_id": "…", "notes": [], "sources": [], "outputs": []}}
```

### `POST /api/tutor/subjects/{subject_id}/notebook/notes`
Ajoute une note personnelle (FR-032).
```json
Body: {"note": "…"}
200 → {"notebook": {"notes": ["…"]}}
```

### `POST /api/tutor/subjects/{subject_id}/notebook/actions`
Exécute une action du carnet (FR-033).
```json
Body: {"action": "summarize_source|compare_chapters|create_study_sheet|quiz_without_answer|explain_with_example|find_prerequisites|create_path|check_missing", "params": {}}
200 → {"output": {"id": "…", "kind": "summary", "content": "…", "sources": []}}
```

### `DELETE /api/tutor/notebook-outputs/{output_id}`
Supprime une sortie du carnet (FR-035).
```json
200 → {"deleted": true}
```

## 8. WebSocket `/ws/tutor`

Le flux WS existant est étendu pour transporter les nouveaux événements :
- `profile_updated` : profil de matière mis à jour.
- `graph_built` : graphe construit/rafraîchi.
- `path_generated` : parcours généré.
- `program_status` : statut de capture OCR (`processing`/`ready`/`error`, position de file).
- `photo_status` : statut d'import de photo (`pending`/`confirmed`).
- `notebook_output` : sortie de carnet générée.

Chaque événement porte `learner_id` pour l'isolation multi-utilisateur.

## Contrats de test (hors-ligne)

Les tests contractuels (`tests/contract/`) valident ces endpoints via `httpx.MockTransport` (factories dans `tests/conftest.py`), sans démon réel. Chaque endpoint a un test de succès et un test d'erreur (400/404/403).
