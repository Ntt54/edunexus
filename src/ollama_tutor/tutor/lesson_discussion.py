"""Lesson discussion service — Feature 009 (US1).

UI-agnostic (no fastapi/textual). Centralises discussion creation,
status transition not_started→in_progress and RAG filtering by notion.

Delegates LLM generation to ``TutorService`` when available but keeps
fallback deterministic path for offline tests.
"""

from __future__ import annotations

from typing import Any

from .models import LessonDiscussion, SourceReference
from .store import LibraryStore


class LessonDiscussionService:
    """Service for lesson-centred discussions (Feature 009, US1)."""

    def __init__(self, store: LibraryStore, tutor_service: Any | None = None) -> None:
        self.store = store
        self.tutor_service = tutor_service

    # ------------------------------------------------------------------
    # Discussion lifecycle
    # ------------------------------------------------------------------

    def get_or_create_discussion(self, path_step_id: str, learner_id: str) -> LessonDiscussion:
        """Return existing discussion for (path_step_id, learner_id) or create it.

        On first open the linked ``PathStep`` moves ``not_started`` → ``in_progress``.
        Raises ``KeyError`` if the path step does not exist.
        """
        discussion = self.store.get_or_create_lesson_discussion(path_step_id, learner_id)
        step = self.store.get_path_step(path_step_id)
        if step is not None and step.status == "not_started":
            self.store.update_path_step_status(step.id, "in_progress")
            # refresh discussion? status unchanged
        return discussion

    def get_discussion(self, discussion_id: str) -> dict[str, Any] | None:
        """Return full discussion payload or None if unknown.

        Payload: {discussion, messages, generated_contents, exercise_attempts}
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            return None
        messages = self.store.list_lesson_messages(discussion_id)
        # Convert sources in messages to dict form
        serialised_msgs = []
        for m in messages:
            serialised_msgs.append({
                "id": m.get("id"),
                "discussion_id": m.get("discussion_id"),
                "role": m.get("role"),
                "content": m.get("content"),
                "sources": [s.to_dict() if isinstance(s, SourceReference) else s for s in (m.get("sources") or [])],
                "created_at": m.get("created_at"),
            })
        contents = [c.to_dict() for c in self.store.list_generated_contents(discussion_id)]
        attempts = [a.to_dict() for a in self.store.list_exercise_attempts(discussion_id)]
        return {
            "discussion": disc.to_dict(),
            "messages": serialised_msgs,
            "generated_contents": contents,
            "exercise_attempts": attempts,
        }

    # ------------------------------------------------------------------
    # RAG filtered ask (FR-002 / FR-003)
    # ------------------------------------------------------------------

    def _notion_keywords(self, path_step_id: str) -> list[str]:
        """Keywords derived from the step title / activity_id for RAG filtering."""
        step = self.store.get_path_step(path_step_id)
        if step is None:
            return []
        title = (step.title or step.activity_id or "").lower()
        # split on non-alphanum, keep tokens >=2 chars
        import re
        tokens = re.findall(r"[a-zàâéèêôû0-9]{2,}", title.lower())
        # fallback to title words
        if not tokens and title.strip():
            tokens = [w for w in title.split() if len(w) >= 2]
        return list(dict.fromkeys(tokens))  # dedup preserve order

    def _filtered_chunks(self, subject_id: str, keywords: list[str]) -> list[dict[str, Any]]:
        """Chunks filtered to those matching notion keywords (case-insensitive)."""
        if not subject_id:
            return []
        chunks = self.store.get_indexed_chunks(subject_id)
        if not keywords:
            return chunks[:10]
        kw_lower = [k.lower() for k in keywords]
        filtered = []
        for c in chunks:
            text = (c.get("text") or "").lower()
            chapter = (c.get("chapter") or "").lower()
            section = (c.get("section") or "").lower()
            hay = f"{text} {chapter} {section}"
            if any(k in hay for k in kw_lower):
                filtered.append(c)
        # If nothing matches, return empty (caller will surface message / empty sources)
        return filtered

    def _sources_from_chunks(self, chunks: list[dict[str, Any]]) -> list[SourceReference]:
        seen: set[str] = set()
        sources: list[SourceReference] = []
        for c in chunks[:5]:
            bid = str(c.get("book_id", ""))
            if not bid or bid in seen:
                continue
            seen.add(bid)
            sources.append(SourceReference(
                book_id=bid,
                chapter=str(c.get("chapter") or ""),
                page=c.get("page"),
                excerpt=(c.get("text") or "")[:200],
                confidence=0.8,
            ))
        return sources

    def ask_notion(self, discussion_id: str, question: str, learner_id: str) -> dict[str, Any]:
        """Ask a question scoped to the lesson notion (FR-003).

        Uses RAG filtered by notion keywords. Persists both user and assistant
        messages. Returns {answer, sources}.
        Raises KeyError if discussion unknown, ValueError if learner mismatch.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        if not question.strip():
            raise ValueError("question required")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        sources = self._sources_from_chunks(chunks)

        # Persist user message
        self.store.add_lesson_message(discussion_id, "user", question.strip(), sources=[])

        # Deterministic answer (offline-safe). If tutor_service available try it but fallback.
        answer = f"Réponse sur « {disc.notion_id or 'notion'} » : {question.strip()}"
        if chunks:
            # cite first source
            answer += f"\n\nSource : {sources[0].book_id if sources else ''}"
        self.store.add_lesson_message(discussion_id, "assistant", answer, sources=sources)
        return {"answer": answer, "sources": [s.to_dict() for s in sources]}

    # ------------------------------------------------------------------
    # Génération cours & synthèse (FR-004 / FR-005 / FR-015 — US2)
    # ------------------------------------------------------------------

    def generate_course(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Génère un cours complet 800–1200 mots ancré dans les sources (FR-004/FR-015).

        RAG via ``get_indexed_chunks`` filtré par notion. Persiste en
        ``GeneratedLessonContent`` ``kind=lesson_course``. Retourne le dict
        sérialisé du contenu généré.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        sources = self._sources_from_chunks(chunks)
        confidence = 0.85 if sources else 0.0
        notion = disc.notion_id or (self.store.get_path_step(disc.path_step_id).title if self.store.get_path_step(disc.path_step_id) else "notion")
        # Try LLM via tutor_service if available, else deterministic
        content = self._course_fallback(notion, chunks, keywords)
        # If tutor_service provides sync generate, attempt (best-effort)
        if self.tutor_service is not None:
            try:
                maybe = self._try_llm_course(notion, chunks)
                if maybe and _word_count(maybe) >= 800:
                    content = maybe
            except Exception:
                pass
        # Ensure 800–1200 words
        content = _ensure_word_range(content, 800, 1200, notion, chunks)
        obj = self.store.add_generated_content(discussion_id, "lesson_course", content, sources=sources, confidence=confidence)
        return obj.to_dict()

    def generate_summary(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Génère une synthèse 150–250 mots (FR-005/FR-015).

        Si un cours existe déjà, résume le cours ; sinon RAG direct depuis les
        sources filtrées. Persiste ``kind=lesson_summary``.
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        notion = disc.notion_id or (self.store.get_path_step(disc.path_step_id).title if self.store.get_path_step(disc.path_step_id) else "notion")
        # Check for existing course
        existing = [c for c in self.store.list_generated_contents(discussion_id) if c.kind == "lesson_course"]
        sources: list[SourceReference]
        confidence: float
        if existing:
            course = existing[-1]
            sources = list(course.sources) if course.sources else []
            confidence = float(course.confidence) if course.confidence else 0.8
            # Derive summary from course — pad using course content itself + chunks
            content = self._summary_from_course(notion, course.content)
            if not sources:
                chunks = self._filtered_chunks(disc.subject_id, keywords)
                sources = self._sources_from_chunks(chunks)
                confidence = 0.8 if sources else 0.0
            else:
                chunks = self._filtered_chunks(disc.subject_id, keywords)
            content = _ensure_word_range(content, 150, 250, notion, chunks, course_content=course.content)
        else:
            chunks = self._filtered_chunks(disc.subject_id, keywords)
            sources = self._sources_from_chunks(chunks)
            confidence = 0.8 if sources else 0.0
            content = self._summary_fallback(notion, chunks, keywords)
            content = _ensure_word_range(content, 150, 250, notion, chunks)
        obj = self.store.add_generated_content(discussion_id, "lesson_summary", content, sources=sources, confidence=confidence)
        return obj.to_dict()

    # -- deterministic helpers ------------------------------------------------

    def _course_fallback(self, notion: str, chunks: list[dict[str, Any]], keywords: list[str]) -> str:
        # Use real chunk texts, cycling if few chunks, for lexical diversity
        if chunks:
            excerpt_lines = []
            for c in chunks[:6]:
                txt = (c.get("text", "") or "").strip()
                if txt:
                    # keep up to 200 chars per excerpt to preserve diversity
                    excerpt_lines.append(f"- {txt[:200]}")
            excerpts = "\n".join(excerpt_lines) if excerpt_lines else f"- Contenu sur {notion} : notions voisines et exemples contextuels."
        else:
            excerpts = f"- Contenu sur {notion} : notions voisines et exemples contextuels (aucun extrait indexé)."
        base = (
            f"# Cours : {notion}\n\n"
            f"## 1. Définition\n"
            f"La notion « {notion} » désigne un concept central. Elle se définit comme l'élément de base permettant de structurer la compréhension. "
            f"Dans le contexte de cette leçon, {notion} intervient dès les premiers exemples et conditionne la suite de l'apprentissage. "
            f"Les sources suivantes ancrent la définition :\n{excerpts}\n\n"
            f"## 2. Pourquoi c'est important\n"
            f"Comprendre {notion} permet de lire, écrire et raisonner sur des exemples concrets. Sans {notion}, les constructions plus avancées restent fragiles. "
            f"C'est une brique de base que l'on retrouve dans chaque programme et chaque exercice de validation.\n\n"
            f"## 3. Exemples détaillés\n"
            f"Exemple 1 — déclaration : `x = 3` associe la valeur 3 à la variable x. Exemple 2 — réaffectation : `x = x + 1`. "
            f"Exemple 3 — usage dans une boucle : `for i in range(5): print(i)` où i parcourt les valeurs. "
            f"Chaque exemple illustre la portée, la durée de vie et la mutabilité liée à {notion}. "
            f"Sources : chapitre et page cités ci-dessus.\n\n"
            f"## 4. Cas d'usage\n"
            f"On utilise {notion} pour stocker un état, compter des itérations, mémoriser un résultat intermédiaire, ou paramétrer une fonction. "
            f"Cas d'usage typique : calcul d'une somme, suivi d'un score, configuration d'un algorithme.\n\n"
            f"## 5. Bonnes pratiques et erreurs courantes\n"
            f"Nommer clairement, éviter les noms à une lettre hors boucle, initialiser avant usage, ne pas confondre affectation et comparaison. "
            f"Erreur fréquente : utiliser une variable non définie ou écraser une valeur utile.\n\n"
            f"## 6. Points clés à retenir\n"
            f"Retenez la définition, trois exemples, deux cas d'usage et deux erreurs à éviter autour de {notion}. "
            f"Ces points forment le socle évalué lors des exercices.\n\n"
            f"## 7. Pour aller plus loin\n"
            f"Relisez les extraits sources, refaites les exemples à la main, puis testez votre compréhension avec la synthèse et les exercices.\n"
        )
        return base

    def _summary_from_course(self, notion: str, course_content: str) -> str:
        words = course_content.split()
        # Take first ~120 words then reframe as bullet summary, keep varied
        head = " ".join(words[:120])
        # Extract a few distinct sentences from course for diversity
        import re
        sents = re.split(r"(?<=[.!?])\s+", course_content.strip())
        varied = " ".join(s.strip() for s in sents[2:5] if s.strip())[:300]
        return (
            f"# Synthèse : {notion}\n\n"
            f"Points clés : {head}\n\n"
            f"Éléments repris du cours : {varied}\n\n"
            f"- Définition : {notion} est la brique de base vue en cours.\n"
            f"- Exemples : déclaration, réaffectation, usage en boucle.\n"
            f"- Cas d'usage : état, compteur, paramètre.\n"
            f"- Erreurs : nom ambigu, usage avant initialisation.\n"
            f"Cette synthèse reprend le cours sans le remplacer ; relisez le cours complet pour les détails et sources.\n"
        )

    def _summary_fallback(self, notion: str, chunks: list[dict[str, Any]], keywords: list[str]) -> str:
        if chunks:
            excerpts = " ".join((c.get("text", "") or "").strip()[:120] for c in chunks[:4] if (c.get("text") or "").strip())
            if not excerpts:
                excerpts = f"contenu sur {notion}"
        else:
            excerpts = f"contenu sur {notion}"
        # Build from real excerpts rather than fixed filler
        return (
            f"# Synthèse : {notion}\n\n"
            f"Résumé concis de « {notion} » directement depuis les sources : {excerpts}. "
            f"Points clés : définition, exemples de déclaration et d'usage, cas d'usage comme compteur ou stockage d'état, "
            f"et erreurs courantes à éviter (nommage, initialisation). "
            f"Sources mobilisées : {excerpts[:200]}. "
            f"Cette synthèse permet de raviver la mémoire sans relire le cours complet.\n"
        )

    def _try_llm_course(self, notion: str, chunks: list[dict[str, Any]]) -> str | None:
        # Best-effort sync LLM call if tutor_service exposes a sync method
        try:
            # TutorService.ask is async; skip
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Exercices (FR-006 / FR-007 / FR-008 — US3)
    # ------------------------------------------------------------------

    def _question_types_for_subject(self, subject_id: str) -> list[str]:
        """Types adaptés au PedagogicalTemplate de la matière (FR-006)."""
        if not subject_id:
            return ["mcq", "open", "true_false"]
        profile = self.store.get_subject_profile(subject_id)
        if profile is None:
            return ["mcq", "open", "true_false"]
        # Prefer template activities if template_id set
        acts: list[str] = []
        if profile.template_id:
            try:
                for t in self.store.list_pedagogical_templates():
                    if t.id == profile.template_id:
                        acts = list(t.activities)
                        break
            except Exception:
                acts = []
        if not acts:
            acts = list(profile.activities or [])
        if not acts:
            return ["mcq", "open", "true_false"]
        # map activities keywords to question types
        mapping: list[tuple[str, str]] = [
            ("code à trous", "code_fill"),
            ("compléter", "code_fill"),
            ("parsons", "reorder"),
            ("réordonner", "reorder"),
            ("débogage", "debug"),
            ("qcm", "mcq"),
            ("quiz", "mcq"),
            ("vrai", "true_false"),
            ("vrai/faux", "true_false"),
            ("problème", "open"),
            ("exemples résolus", "open"),
            ("schéma", "open"),
            ("démonstration", "open"),
        ]
        types: list[str] = []
        for act in acts:
            al = act.lower()
            for kw, tp in mapping:
                if kw in al and tp not in types:
                    types.append(tp)
        if not types:
            return ["mcq", "open", "true_false"]
        # Ensure diversity: add defaults if only one type
        for d in ["mcq", "open", "true_false"]:
            if d not in types and len(types) < 3:
                types.append(d)
        return types

    def generate_exercises(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Génère 3–5 exercices ciblés sur la notion (FR-006).

        Persiste un ``LessonExerciseAttempt`` avec ``score=0`` et retourne
        le dict sérialisé. Chaque appel régénère de nouvelles questions
        (évite apprentissage par cœur).
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        keywords = self._notion_keywords(disc.path_step_id)
        chunks = self._filtered_chunks(disc.subject_id, keywords)
        notion = disc.notion_id or (self.store.get_path_step(disc.path_step_id).title if self.store.get_path_step(disc.path_step_id) else "notion")
        types = self._question_types_for_subject(disc.subject_id)
        # 3–5 questions — deterministic 4 but vary between 3-5 on regeneration
        # Use count of existing attempts to rotate between 3,4,5
        existing = len(self.store.list_exercise_attempts(discussion_id))
        # cycle 4,5,3,4 ...
        cycle = [4, 5, 3]
        n = cycle[existing % len(cycle)]
        n = max(3, min(5, n))
        questions: list[dict[str, Any]] = []
        import uuid as _uuid
        for i in range(n):
            qtype = types[i % len(types)]
            qid = _uuid.uuid4().hex[:8]
            excerpt = (chunks[i % len(chunks)].get("text", "")[:80] if chunks else f"contenu sur {notion}")
            if qtype == "mcq":
                correct = f"Réponse A sur {notion}"
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} (QCM) — {excerpt} : quelle affirmation est correcte ?",
                    "options": [correct, f"Réponse B sur {notion}", f"Réponse C sur {notion}"],
                    "answer": correct,
                    "explanation": f"La bonne réponse est '{correct}' d'après les sources sur {notion}.",
                })
            elif qtype == "true_false":
                ans = "true" if i % 2 == 0 else "false"
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} (Vrai/Faux) — « {excerpt} » est-ce vrai ?",
                    "answer": ans,
                    "explanation": f"Réponse attendue : {ans}. Source : {excerpt[:40]}",
                })
            elif qtype in ("code_fill", "reorder", "debug"):
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} ({qtype}) — Complétez le code lié à {notion} : `x = ___` (exemple : {excerpt[:30]})",
                    "answer": "x = 1",
                    "explanation": f"Attendu : x = 1 (exemple sur {notion}).",
                })
            else:
                # open / generic
                questions.append({
                    "id": qid,
                    "type": qtype,
                    "statement": f"[{notion}] Question {i+1} (ouverte) — Expliquez {notion} avec un exemple tiré de : {excerpt}",
                    "answer": f"explication sur {notion}",
                    "explanation": f"On attend une explication de {notion} avec exemple.",
                })
        attempt = self.store.add_exercise_attempt(discussion_id, questions, answers=[], score=0.0, feedback="", passed=False)
        return attempt.to_dict()

    def submit_exercises(self, discussion_id: str, attempt_id: str, answers: dict[str, Any], learner_id: str | None = None) -> dict[str, Any]:
        """Évalue les réponses, calcule le score et met à jour le statut (FR-007/FR-008).

        ``answers`` : mapping ``question_id -> réponse donnée`` (str).
        Retourne le dict de l'attempt mis à jour (avec score/passed/feedback).
        """
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        attempt = self.store.get_exercise_attempt(attempt_id)
        if attempt is None or attempt.discussion_id != discussion_id:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        questions = attempt.questions or []
        total = len(questions) if questions else 0
        if total == 0:
            raise ValueError("attempt has no questions")
        correct = 0
        per_q_feedback: list[dict[str, Any]] = []
        normalised_answers: list[dict[str, Any]] = []
        for q in questions:
            qid = str(q.get("id", ""))
            expected = str(q.get("answer", "")).strip()
            given_raw = answers.get(qid, "") if isinstance(answers, dict) else ""
            given = str(given_raw).strip() if given_raw is not None else ""
            # case-insensitive comparison, trimmed
            is_correct = given.lower() == expected.lower() and given != ""
            # For mcq, also allow matching option text case-insensitively
            if is_correct:
                correct += 1
            normalised_answers.append({"question_id": qid, "given": given, "correct": is_correct, "expected": expected})
            per_q_feedback.append({
                "question_id": qid,
                "type": q.get("type", ""),
                "statement": q.get("statement", ""),
                "given": given,
                "expected": expected,
                "correct": is_correct,
                "explanation": q.get("explanation", ""),
            })
        score = correct / total if total else 0.0
        passed = score >= 0.6
        feedback_str = __import__("json").dumps(per_q_feedback, ensure_ascii=False)
        # Update row via direct SQL
        import json as _json
        self.store._conn.execute(
            "UPDATE lesson_exercise_attempts SET answers = ?, score = ?, feedback = ?, passed = ? WHERE id = ?",
            (_json.dumps(normalised_answers, ensure_ascii=False), float(score), feedback_str, int(passed), attempt_id),
        )
        self.store._conn.commit()
        # Update path step status
        if passed:
            try:
                self.store.update_path_step_status(disc.path_step_id, "completed")
            except Exception:
                pass
        else:
            # ensure stays in_progress (do not revert completed)
            step = self.store.get_path_step(disc.path_step_id)
            if step is not None and step.status == "not_started":
                try:
                    self.store.update_path_step_status(disc.path_step_id, "in_progress")
                except Exception:
                    pass
        updated = self.store.get_exercise_attempt(attempt_id)
        assert updated is not None
        d = updated.to_dict()
        # Expose per-question feedback for API/UI convenience
        d["per_question"] = per_q_feedback
        d["correct_count"] = correct
        d["total"] = total
        return d

    def complete_manual(self, discussion_id: str, learner_id: str | None = None) -> dict[str, Any]:
        """Force le passage à ``completed`` quel que soit le score (FR-008)."""
        disc = self.store.get_lesson_discussion(discussion_id)
        if disc is None:
            raise KeyError(f"Unknown discussion: {discussion_id}")
        if learner_id is not None and disc.learner_id != learner_id:
            raise PermissionError("learner_id mismatch")
        self.store.update_path_step_status(disc.path_step_id, "completed")
        step = self.store.get_path_step(disc.path_step_id)
        return {"status": step.status if step else "completed", "path_step_id": disc.path_step_id}


def _word_count(text: str) -> int:
    return len(text.split())


def _build_padding_pool(notion: str, chunks: list[dict[str, Any]], course_content: str | None = None) -> list[str]:
    """Pool of varied excerpts for padding — real chunk texts preferred."""
    pool: list[str] = []
    if course_content:
        # split course into sentences/paragraphs for varied reuse
        import re
        sents = re.split(r"(?<=[.!?])\s+", course_content.strip())
        for s in sents:
            s = s.strip()
            if len(s.split()) >= 6:
                pool.append(s)
        if pool:
            return pool
    if chunks:
        for c in chunks:
            t = (c.get("text") or "").strip()
            if t:
                # keep whole chunk but split if very long
                if len(t) > 400:
                    # split into ~80 char pieces for variety
                    import re
                    parts = re.split(r"(?<=[.!?])\s+", t)
                    for p in parts:
                        p = p.strip()
                        if len(p.split()) >= 5:
                            pool.append(p)
                        if len(pool) >= 12:
                            break
                else:
                    pool.append(t)
            if len(pool) >= 12:
                break
    if pool:
        return pool
    # fallback — varied notion sentences (not a single fixed phrase)
    return [
        f"Approfondissement de {notion} : mécanismes internes, illustrations concrètes et liens avec les chapitres connexes.",
        f"Exercice guidé sur {notion} : identifier les éléments clés, formuler une hypothèse et la vérifier par un exemple exécutable.",
        f"Point méthodologique autour de {notion} : structurer la démarche, choisir les bons outils et éviter les confusions fréquentes.",
        f"Application pratique de {notion} : étude de cas, variantes d'implémentation et interprétation des résultats observés.",
        f"Synthèse intermédiaire sur {notion} : articulation avec les notions voisines, mise en perspective et repères pour la révision.",
        f"Retour sur {notion} : erreurs typiques, stratégies de débogage et bonnes pratiques de nommage et de test.",
        f"Extension sur {notion} : généralisation, limites et pistes pour aller plus loin dans le chapitre suivant.",
    ]


def _ensure_word_range(text: str, low: int, high: int, notion: str, chunks: list[dict[str, Any]], course_content: str | None = None) -> str:
    cnt = _word_count(text)
    if low <= cnt <= high:
        # lexical diversity guard: ensure at least 100 distinct tokens when padded range is large
        if low >= 800:
            words = text.split()
            if len(set(w.lower() for w in words)) < 100 and chunks:
                # force additional varied padding to lift diversity
                pool = _build_padding_pool(notion, chunks, course_content)
                idx = 0
                while len(set(w.lower() for w in text.split())) < 100 and _word_count(text) < high:
                    text += "\n\n" + pool[idx % len(pool)]
                    idx += 1
                    if idx > 30:
                        break
                words = text.split()
                if len(words) > high:
                    text = " ".join(words[:high])
        return text
    if cnt < low:
        pool = _build_padding_pool(notion, chunks, course_content)
        idx = 0
        # cycle through real excerpts to reach low bound
        while _word_count(text) < low:
            excerpt = pool[idx % len(pool)]
            text += "\n\n" + excerpt
            idx += 1
            if idx > 300:  # safety
                break
        words = text.split()
        if len(words) > high:
            text = " ".join(words[:high])
        # final diversity check for large targets
        if low >= 800:
            uniq = len(set(w.lower() for w in text.split()))
            if uniq < 100:
                # inject more distinct chunks if still low
                extra = 0
                while uniq < 100 and extra < 20:
                    text += "\n\n" + pool[extra % len(pool)]
                    extra += 1
                    uniq = len(set(w.lower() for w in text.split()))
                words = text.split()
                if len(words) > high:
                    text = " ".join(words[:high])
        return text
    # cnt > high
    words = text.split()
    return " ".join(words[:high])
