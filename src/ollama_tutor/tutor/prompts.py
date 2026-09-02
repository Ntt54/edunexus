"""Tutor prompt construction (research D8/D11, FR-012).

Encodes the base tutor persona, the socratic on/off contract, the four
adaptation levels (beginner..expert), the citation discipline (mechanical
``[Livre X — chapitre Y, p. Z]`` references) and the "if not grounded, say so"
honesty rule. The think toggle appends (or omits) an extended-thinking
instruction (research D10).

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

from .retrieval import assemble_context_blocks
from .vector import ScoredChunk

_VALID_LEVELS = {"beginner", "intermediate", "advanced", "expert"}

# In-conversation level-override phrases (FR-014): an explicit request inside
# the question text wins over the configured/default level. French + English.
_LEVEL_OVERRIDE_KEYS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("débutant", "debutant", "beginner", "niveau débutant", "niveau debutant",
         "comme pour un enfant", "explain like i'm a beginner",
         "explain like im a beginner", "explain like a beginner",
         "pour un débutant", "pour un debutant"),
        "beginner",
    ),
    (
        ("intermédiaire", "intermediaire", "intermediate"),
        "intermediate",
    ),
    (
        ("avancé", "avance", "advanced", "niveau avancé"),
        "advanced",
    ),
    (
        ("expert", "niveau expert", "pour un expert", "comme pour un expert"),
        "expert",
    ),
)

# In-conversation socratic-override phrases: explicit "answer me directly"
# disables socratic mode; explicit "guide me by questions" enables it.
_SOCRATIC_OFF_KEYS = (
    "réponds directement", "donne la réponse", "réponse directe",
    "answer directly", "just give me the answer", "give me the answer",
)
_SOCRATIC_ON_KEYS = (
    "guide moi", "guide-moi", "par des questions", "guide by questions",
    "socratique", "mode socratique",
)


def _normalize_level(level: str | None) -> str:
    if level in _VALID_LEVELS:
        return level  # type: ignore[return-value]
    return "intermediate"


def detect_level_override(question: str) -> str | None:
    """Detect an explicit level request inside the question (FR-014).

    Returns the matched level or ``None`` when the question carries no
    override signal. Matching is case-insensitive substring over a lowered
    copy of the question.
    """
    q = (question or "").lower()
    if not q:
        return None
    for keys, lvl in _LEVEL_OVERRIDE_KEYS:
        if any(k in q for k in keys):
            return lvl
    return None


def detect_socratic_override(question: str) -> bool | None:
    """Detect an explicit socratic on/off request inside the question.

    Returns ``True`` (force socratic), ``False`` (force direct) or ``None``
    when the question carries no socratic signal.
    """
    q = (question or "").lower()
    if not q:
        return None
    if any(k in q for k in _SOCRATIC_OFF_KEYS):
        return False
    if any(k in q for k in _SOCRATIC_ON_KEYS):
        return True
    return None


def resolve_overrides(
    question: str, level: str | None, socratic: bool | None
) -> tuple[str, bool]:
    """Apply in-conversation overrides on top of already config-defaulted
    ``level``/``socratic`` (research D10 + FR-014).

    The question text is the strongest signal: an explicit "explain like I'm a
    beginner" forces ``beginner`` even if the UI/config selected ``expert``.
    Returns the resolved ``(level, socratic)`` pair.
    """
    lvl = _normalize_level(level)
    soc: bool = bool(socratic)
    lvl_ov = detect_level_override(question)
    if lvl_ov:
        lvl = lvl_ov
    soc_ov = detect_socratic_override(question)
    if soc_ov is not None:
        soc = soc_ov
    return lvl, soc


def build_system_prompt(
    subject: str,
    level: str | None,
    socratic: bool,
    sources: list[dict],
) -> str:
    """Build the tutor system prompt (FR-012 behaviors + citation + honesty).

    ``sources`` is the list of retrieved-source metadata dicts
    (``{"book", "chapter", "page", "score"}``); it is used only to remind the
    model which books are in scope, not for answer text.
    """
    level = _normalize_level(level)
    lines: list[str] = []
    lines.append(
        "Tu es un tuteur personnel, bienveillant et rigoureux, spécialisé dans le "
        f"sujet : {subject}."
    )
    lines.append("Tu réponds toujours en français.")
    lines.append(
        f"Niveau de l'élève à viser : {level}. "
        + _level_directive(level)
    )
    lines.append("")
    lines.append("Comportements attendus (FR-012) :")
    lines.append(
        "- Explique étape par étape en t'appuyant sur les extraits des livres "
        "de l'élève."
    )
    lines.append(
        "- Privilégie la compréhension profonde plutôt que l'apprentissage par cœur."
    )
    lines.append(_socratic_directive(socratic))
    lines.append("")
    lines.append("Discipline de citation :")
    lines.append(
        "Quand tu tires une information d'un extrait, cite-le entre crochets "
        "exactement sous la forme fournie, par exemple "
        "\u00ab [Livre X — chapitre Y, p. Z] \u00bb. N'invente jamais de "
        "référence qui n'apparaît pas dans les extraits."
    )
    lines.append("")
    lines.append("Règle d'honnêteté (si non fondé) :")
    lines.append(
        "Si la réponse n'est pas fondée sur les extraits fournis (ou s'il n'y a "
        "aucun extrait disponible), dis explicitement que tu ne peux pas "
        "répondre à partir des livres de l'élève, et propose de préciser la "
        "question ou d'importer le livre concerné. Ne fabrique pas de contenu "
        "pseudo-cité."
    )
    return "\n".join(lines)


def _level_directive(level: str) -> str:
    return {
        "beginner": (
            "Utilise un vocabulaire simple, des analogies concrètes et définit "
            "chaque terme technique."
        ),
        "intermediate": (
            "Equilibre précision et accessibilité ; tu peux utiliser le "
            "vocabulaire standard du sujet."
        ),
        "advanced": (
            "Va au fond : nuance, contre-exemples et liens entre concepts ; "
            "vocabulaire technique admis."
        ),
        "expert": (
            "Réponse dense et rigoureuse, sans pédagogie superficielle ; "
            "suppositions de prérequis complets."
        ),
    }.get(level, "Equilibre précision et accessibilité.")


def _socratic_directive(socratic: bool) -> str:
    """Return the socratic-on / direct-off behavior directive (FR-013).

    Socratic mode follows a strict "questions first" contract with an explicit
    escalation example so the model guides rather than answers outright.
    """
    if socratic:
        return (
            "- Mode socratique : guide l'élève par des questions progressives "
            "avant de donner la réponse (contrat « questions d'abord »). Ne "
            "donne pas immédiatement la réponse complète ; aide-le à la "
            "construire. Exemple d'escalade : (1) pose une question ouverte "
            "« D'après ce que tu as lu, que remarques-tu ? » ; (2) enchaîne "
            "avec des questions de relance ciblées « Pourquoi cela se produit-il "
            "? » ; (3) ne fournis la réponse complète qu'en dernier recours, si "
            "l'élève bloque."
        )
    return (
        "- Mode direct : donne immédiatement une réponse claire, complète et "
        "structurée, sans faire répondre l'élève."
    )


def build_user_prompt(question: str, sources: list[ScoredChunk]) -> str:
    """Build the user prompt: the question plus the assembled grounded context."""
    context = assemble_context_blocks(sources)
    if context:
        return (
            f"Question de l'élève : {question}\n\n"
            "Extraits de ses livres (à citer entre crochets) :\n"
            f"{context}"
        )
    return (
        f"Question de l'élève : {question}\n\n"
        "(Aucun extrait disponible dans les livres de l'élève.)"
    )


def build_think_instruction(think: bool) -> str:
    """Return the extended-thinking instruction line when ``think`` is on."""
    if think:
        return (
            "Avant de répondre, prends le temps de réfléchir en profondeur ; "
            "explicite ton raisonnement."
        )
    return ""


# ---------------------------------------------------------------------------
# Compare mode (US7 / T047, FR-033): multi-book synthesis
# ---------------------------------------------------------------------------


def build_compare_system_prompt(subject: str) -> str:
    """System prompt for compare mode (FR-033).

    Instructs the model to synthesize a notion across the provided book
    passages, to cite every claim with its mechanical ``[Livre X — chapitre Y,
    p. Z]`` reference, and to dedicate a section to the *differences* between
    the ouvrages (divergences, nuances, contradictions).
    """
    lines: list[str] = []
    lines.append(
        "Tu es un tuteur personnel, bienveillant et rigoureux, spécialisé dans le "
        f"sujet : {subject}."
    )
    lines.append("Tu réponds toujours en français.")
    lines.append("")
    lines.append("Tâche — synthèse comparative multi-ouvrages (FR-033) :")
    lines.append(
        "À partir des extraits de plusieurs livres de l'élève, produis une "
        "synthèse qui compare les points de vue."
    )
    lines.append("")
    lines.append("Discipline de citation (obligatoire) :")
    lines.append(
        "Chaque affirmation tirée d'un extrait doit être citée entre crochets "
        "exactement sous la forme fournie, par exemple "
        "\u00ab [Livre X — chapitre Y, p. Z] \u00bb. N'invente jamais de "
        "référence qui n'apparaît pas dans les extraits."
    )
    lines.append("")
    lines.append("Structure attendue de la réponse :")
    lines.append(
        "1) Une synthèse générale de la notion en t'appuyant sur les extraits "
        "cités."
    )
    lines.append(
        "2) Une section intitulée exactement « Différences entre les ouvrages » "
        "où tu identifies et expliques les divergences, nuances ou contradictions "
        "entre les livres (en citant chacun)."
    )
    lines.append(
        "Si un extrait ne traite pas la notion, ne le force pas ; si aucun extrait "
        "n'est disponible, dis-le explicitement."
    )
    return "\n".join(lines)


def build_compare_user_prompt(
    notion: str, per_book: list[tuple[str, list[ScoredChunk]]]
) -> str:
    """Build the compare user prompt: the notion plus per-book passages.

    ``per_book`` is an ordered list of ``(book_title, [ScoredChunk])`` pairs so
    the model can attribute each passage to its source book.
    """
    parts: list[str] = []
    parts.append(f"Notion à comparer : {notion}\n")
    parts.append("Extraits de ses livres (à citer entre crochets) :\n")
    for book_title, chunks in per_book:
        parts.append(f"=== Livre : {book_title} ===")
        blocks = assemble_context_blocks(chunks)
        parts.append(blocks if blocks else "(aucun extrait pour ce livre)")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Revision sheet (US2 / T015): auto-generated fiche de révision
# ---------------------------------------------------------------------------


def build_revision_sheet_prompt(
    chunks: list[str],
    subject_name: str,
    level: str = "intermediate",
) -> str:
    """Build the system prompt for auto-generating a revision sheet (fiche).

    Takes raw text chunks from the user's books, the subject name and an
    adaptation level.  Returns a system prompt instructing the LLM to produce
    a structured revision sheet with: definitions, key formulas / concepts,
    key points and a conceptual map — all in French, adapted to the level.

    ``chunks`` is a list of **raw text** strings (already assembled context).
    """
    level = _normalize_level(level)
    context_block = "\n\n---\n\n".join(chunks) if chunks else "(aucun extrait)"

    complexity = {
        "beginner": (
            "une version simple et vulgarisée, avec des mots courants. "
            "Définit chaque terme technique. Utilise des analogies."
        ),
        "intermediate": (
            "une version équilibrée, précise mais accessible, en utilisant le "
            "vocabulaire standard du sujet."
        ),
        "advanced": (
            "une version détaillée et technique, avec des nuances, des "
            "contre-exemples et des liens approfondis entre concepts."
        ),
        "expert": (
            "une version exhaustive et rigoureuse, dense, sans simplification, "
            "en supposant des prérequis complets."
        ),
    }.get(level, "une version équilibrée et accessible.")

    lines: list[str] = []
    lines.append(
        "Tu es un tuteur personnel, bienveillant et rigoureux, spécialisé dans le "
        f"sujet : {subject_name}."
    )
    lines.append("Tu réponds toujours en français.")
    lines.append("")
    lines.append("Tâche — Fiche de révision auto-générée :")
    lines.append(
        f"À partir des extraits ci-dessous, produis {complexity}"
    )
    lines.append("")
    lines.append("Structure attendue de la fiche :")
    lines.append("1) **Définitions** — Les termes et concepts clés, définis brièvement.")
    lines.append(
        "2) **Formules / Concepts fondamentaux** — Les formules, théorèmes ou "
        "principes essentiels (s'il y en a ; sinon, sauter cette section)."
    )
    lines.append(
        "3) **Points clés** — Les idées maîtresses à retenir, sous forme de "
        "liste à puces."
    )
    lines.append(
        "4) **Carte conceptuelle** — Une représentation textuelle des relations "
        "entre les concepts (utilise des flèches → ou une liste hiérarchique)."
    )
    lines.append("")
    lines.append("Discipline de citation :")
    lines.append(
        "Quand tu tires une information d'un extrait, cite-le entre crochets "
        "exactement sous la forme fournie, par exemple "
        "\u00ab [Livre X — chapitre Y, p. Z] \u00bb. N'invente jamais de "
        "référence qui n'apparaît pas dans les extraits."
    )
    lines.append("")
    lines.append("Règle d'honnêteté (si non fondé) :")
    lines.append(
        "Si les extraits fournis sont insuffisants pour une section, indique-le "
        "explicitement plutôt que de fabriquer du contenu. Ne génère pas de "
        "pseudo-citations."
    )
    lines.append("")
    lines.append("=== Extraits des livres de l'élève ===")
    lines.append(context_block)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# US1 — Résumé de document
# ---------------------------------------------------------------------------


def build_summary_prompt(chunks: list[str], book_title: str, chapter: str | None = None) -> str:
    """Build system prompt for document summary generation."""
    context = "\n\n".join(f"[Extrait {i+1}]\n{c}" for i, c in enumerate(chunks))
    target = f"du chapitre '{chapter}' du livre" if chapter else "du livre"
    return f"""Tu es un assistant pédagogique expert. Résume {target} «{book_title}».

Extraits du document :
{context}

Produis un résumé structuré en markdown avec :
1. **Résumé global** (2-3 paragraphes)
2. **Sections clés** (liste à puces par thème)
3. **Points essentiels** (5-10 points à retenir)

Sois précis, cite les concepts importants. Toute la réponse en français."""


# ---------------------------------------------------------------------------
# US13 — Parcours auto-générés (T074)
# ---------------------------------------------------------------------------


def build_learning_path_prompt(
    concepts: list[str],
    gaps: list[str],
    level: str = "intermediate",
) -> str:
    """Build a system prompt asking the LLM to order concepts by pedagogical dependency.

    ``concepts`` is a list of concept names the learner needs to cover.
    ``gaps`` is a list of weak areas identified from a diagnostic or progress
    tracker — these should be prioritised (placed earlier in the path).
    ``level`` is the learner's adaptation level.

    The LLM must return a JSON array of concept names in the recommended
    learning order, with gap concepts placed first and prerequisite concepts
    before dependent ones.
    """
    level = _normalize_level(level)
    concept_block = "\n".join(f"- {c}" for c in concepts) if concepts else "(aucun concept)"
    gap_block = "\n".join(f"- {g}" for g in gaps) if gaps else "(aucun point faible identifié)"

    lines: list[str] = []
    lines.append(
        "Tu es un tuteur pédagogique qui conçoit un parcours d'apprentissage "
        "optimisé en français."
    )
    lines.append("")
    lines.append("Tâche — Ordonner les concepts par dépendance pédagogique :")
    lines.append(
        "À partir de la liste de concepts et des points faibles identifiés, "
        "produis un ordre d'apprentissage optimal."
    )
    lines.append("")
    lines.append(f"Niveau de l'élève : {level}.")
    lines.append("")
    lines.append("Concepts à couvrir :")
    lines.append(concept_block)
    lines.append("")
    lines.append("Points faibles identifiés (à prioriser) :")
    lines.append(gap_block)
    lines.append("")
    lines.append("Règles d'ordonnancement :")
    lines.append(
        "- Les concepts identifiés comme « points faibles » doivent apparaître "
        "en priorité (plus tôt dans le parcours)."
    )
    lines.append(
        "- Les concepts prérequis doivent précéder les concepts qui en dépendent."
    )
    lines.append(
        "- Chaque concept de la liste initiale DOIT apparaître exactement une fois "
        "dans la réponse."
    )
    lines.append(
        "- Si des concepts n'ont aucune dépendance entre eux, garde leur ordre "
        "original."
    )
    lines.append("")
    lines.append("Réponds STRICTEMENT en JSON, sans aucun texte autour, sous la forme :")
    lines.append('["Concept 1", "Concept 2", "Concept 3"]')
    lines.append("")
    lines.append(
        "Toute la réponse est en français. Le tableau doit contenir EXACTEMENT "
        f"{len(concepts)} éléments correspondant aux concepts fournis."
    )
    return "\n".join(lines)


def build_path_from_books_prompt(
    book_structures: list[dict],
    level: str = "intermediate",
) -> str:
    """Build a system prompt asking the LLM to create a learning path from books' TOC.

    ``book_structures`` is a list of dicts with keys: title, chapters (list of
    dicts with title, sections).
    ``level`` is the learner's level.

    The LLM must return a JSON array of step objects with keys:
    - title: string
    - type: "concept" | "exercise" | "quiz" | "reading"
    - duration: number (minutes)
    - source: string (book title or "Général")

    Structure: 2 lessons → exercises → 2 lessons → exercises → ... → project.
    """
    level = _normalize_level(level)

    # Format book structures as readable text.
    book_blocks: list[str] = []
    for bs in book_structures:
        title = bs.get("title", "Sans titre")
        chapters = bs.get("chapters", [])
        chapter_lines: list[str] = []
        for ch in chapters:
            ch_title = ch.get("title", "")
            sections = ch.get("sections", [])
            if sections:
                section_str = ", ".join(sections)
                chapter_lines.append(f"  - {ch_title} : {section_str}")
            else:
                chapter_lines.append(f"  - {ch_title}")
        chapters_str = "\n".join(chapter_lines) if chapter_lines else "  (aucun chapitre)"
        book_blocks.append(f"📖 {title}\n{chapters_str}")
    books_str = "\n\n".join(book_blocks) if book_blocks else "(aucun livre)"

    lines: list[str] = []
    lines.append(
        "Tu es un tuteur pédagogique qui conçoit un parcours d'apprentissage "
        "structuré en français."
    )
    lines.append("")
    lines.append(
        "Tâche — Créer un parcours d'apprentissage à partir des tables des matières "
        "de livres sélectionnés."
    )
    lines.append("")
    lines.append(f"Niveau de l'élève : {level}.")
    lines.append("")
    lines.append("Structure des livres :")
    lines.append(books_str)
    lines.append("")
    lines.append("Règles de construction du parcours :")
    lines.append(
        "- Le parcours alterne 2 leçons (concept ou reading) puis des exercices "
        "qui testent les leçons précédentes."
    )
    lines.append(
        "- Après chaque bloc de 2-3 leçons, insère un quiz de validation."
    )
    lines.append(
        "- Les exercices testent les concepts des leçons précédentes (pas les suivantes)."
    )
    lines.append(
        "- La dernière étape est un projet qui synthétise tout le parcours."
    )
    lines.append(
        "- Chaque étape doit avoir un titre explicite, un type, une durée estimée "
        "(en minutes) et une source (titre du livre ou 'Général')."
    )
    lines.append(
        "- Répartis les leçons sur les différents livres quand c'est pertinent."
    )
    lines.append("")
    lines.append("Réponds STRICTEMENT en JSON, sans aucun texte autour, sous la forme :")
    lines.append(
        '[{"title": "Titre de l\'étape", "type": "concept", "duration": 15, '
        '"source": "Titre du livre"}]'
    )
    lines.append("")
    lines.append(
        "Les valeurs de type possibles sont : \"concept\", \"exercise\", \"quiz\", "
        "\"reading\"."
    )
    lines.append(
        "Le parcours doit contenir au moins 6 étapes et au plus 20 étapes."
    )
    lines.append("")
    lines.append(
        "Toute la réponse est en français. Chaque étape doit être pertinente "
        "par rapport au contenu des livres fournis."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# US3 — Diagnostic initial / quiz de positionnement (T020)
# ---------------------------------------------------------------------------

_DIAGNOSTIC_LEVEL_GUIDE = {
    "beginner": (
        "des questions élémentaires qui vérifient la compréhension des "
        "définitions et des notions de base, avec un vocabulaire simple."
    ),
    "intermediate": (
        "des questions de niveau moyen qui testent la compréhension des "
        "relations entre concepts et l'application pratique."
    ),
    "advanced": (
        "des questions avancées qui évaluent l'analyse critique, la "
        "synthèse et les cas complexes."
    ),
}


def build_diagnostic_question_prompt(
    concept_name: str, difficulty_level: str
) -> str:
    """Build a system prompt asking the LLM to generate a diagnostic MCQ.

    Returns a prompt that instructs the model to produce a JSON object with:
    ``question``, ``options`` (dict with keys A/B/C/D), ``correct`` (letter),
    and ``explanation`` (short French text).  All UI text is in French.

    ``difficulty_level`` must be one of ``"beginner"``, ``"intermediate"``,
    or ``"advanced"``.
    """
    level = difficulty_level if difficulty_level in _DIAGNOSTIC_LEVEL_GUIDE else "intermediate"
    complexity = _DIAGNOSTIC_LEVEL_GUIDE[level]

    lines: list[str] = []
    lines.append(
        "Tu es un tuteur pédagogique qui crée un quiz de positionnement en français."
    )
    lines.append(
        f"Le concept à évaluer est : {concept_name}."
    )
    lines.append(
        f"Tu dois générer {complexity}"
    )
    lines.append("")
    lines.append("Réponds STRICTEMENT en JSON, sans aucun texte autour, selon la forme :")
    lines.append(
        '{"question": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
        '"correct": "A", "explication": "..."}'
    )
    lines.append("")
    lines.append("Contraintes :")
    lines.append("- Le champ 'question' est une question claire et non ambiguë.")
    lines.append("- Le champ 'options' contient exactement 4 choix (A, B, C, D).")
    lines.append(
        "- Le champ 'correct' est la lettre (A, B, C ou D) de la bonne réponse."
    )
    lines.append(
        "- Le champ 'explication' est une courte explication (1-2 phrases) en français "
        "qui justifie la bonne réponse."
    )
    lines.append("- Toute la réponse est en français.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# US14 — Mode Épreuve (T080): exam document analysis prompt
# ---------------------------------------------------------------------------


def build_exam_analysis_prompt(exam_text: str) -> list:
    """Build prompts asking the LLM to analyze OCR-extracted exam text.

    Returns a list of ``Message`` objects (system + user) that instruct the
    model to extract numbered questions with their associated concepts from the
    raw exam text.  The response format is a JSON array of question objects.

    ``exam_text`` is the concatenated text extracted from the exam document(s).
    """
    from ..models import Message, MessageRole

    system = (
        "Tu es un assistant pédagogique expert, spécialisé dans l'analyse "
        "d'épreuves et d'examens.\n"
        "Tu réponds toujours en français.\n\n"
        "Tâche — Analyse de document d'examen :\n"
        "À partir du texte OCR d'une épreuve (ou d'un examen), identifie et "
        "extrait chaque question.\n\n"
        "Contraintes de sortie :\n"
        "- Réponds UNIQUEMENT par un objet JSON (pas de texte autour).\n"
        "- L'objet contient une clé \"questions\" associée à un tableau.\n"
        "- Chaque élément du tableau contient :\n"
        "  - \"number\" (int) : le numéro de la question telle qu'elle apparaît "
        "dans le document.\n"
        "  - \"statement\" (str) : l'énoncé complet de la question.\n"
        "  - \"concepts\" (list[str]) : les notions/concepts évalués par cette "
        "question.\n"
        "  - \"status\" : \"pending\" par défaut.\n\n"
        "Exemple de forme attendue :\n"
        '{"questions": [{"number": 1, "statement": "...", "concepts": ["..."], '
        '"status": "pending"}]}\n\n'
        "Si le texte ne contient aucune question identifiable, retourne "
        '{"questions": []}.'
    )

    user = f"Texte OCR de l'épreuve :\n\n{exam_text}"

    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]


def build_exam_resolve_prompt(
    question_statement: str,
    concepts: list[str],
    hint_level: int,
    rag_context: str = "",
) -> list:
    """Build prompts to resolve an exam question (full answer or progressive hint).

    When ``hint_level`` is 0 the model generates a full answer.  For
    ``hint_level`` > 0 it generates a progressive hint that guides the learner
    without revealing the complete solution.

    ``rag_context`` is an optional string of retrieved passages to ground the
    answer (may be empty when no source material is available).
    """
    from ..models import Message, MessageRole

    concepts_str = ", ".join(concepts) if concepts else "non spécifié"
    context_block = (
        f"\n\nExtraits pertinents :\n{rag_context}" if rag_context else ""
    )

    if hint_level == 0:
        system = (
            "Tu es un tuteur pédagogique expert, spécialisé dans la correction "
            "et l'explication d'épreuves.\n"
            "Tu réponds toujours en français.\n\n"
            "Tâche — Réponse complète à une question d'examen :\n"
            "Fournis une réponse détaillée, structurée et pédagogique à la "
            "question posée.\n\n"
            "Contraintes :\n"
            "- Si des extraits de livres sont fournis, appuie-toi dessus et "
            "cite-les entre crochets.\n"
            "- Sinon, réponds à partir de tes connaissances en précisant que "
            "ce n'est pas fondé sur les livres de l'élève.\n"
            "- La réponse doit être complète et autonome."
        )
        user = (
            f"Question : {question_statement}\n"
            f"Notions évaluées : {concepts_str}"
            f"{context_block}"
        )
    else:
        level_desc = {
            1: "une première piste de réflexion très générale (orientation "
            "sans donner la réponse).",
            2: "un indice intermédiaire qui précise l'approche à adopter "
            "mais ne dévoile pas la solution complète.",
            3: "un indice presque complet qui guide vers la réponse finale "
            "sans la donner entièrement.",
        }.get(
            hint_level,
            "un indice progressif qui aide l'élève sans révéler la réponse.",
        )
        system = (
            "Tu es un tuteur pédagogique bienveillant, spécialisé dans "
            "l'accompagnement à la résolution d'épreuves.\n"
            "Tu réponds toujours en français.\n\n"
            f"Tâche — Indice de niveau {hint_level} :\n"
            f"Fournis {level_desc}\n\n"
            "Contraintes :\n"
            "- Ne donne JAMAIS la réponse complète dans un indice.\n"
            "- Formule l'indice comme une question ou une suggestion "
            "pédagogique.\n"
            "- Adapte la profondeur au niveau de l'indice demandé."
        )
        user = (
            f"Question : {question_statement}\n"
            f"Notions évaluées : {concepts_str}\n"
            f"Niveau d'indice demandé : {hint_level}/3"
            f"{context_block}"
        )

    return [
        Message(role=MessageRole.SYSTEM, content=system),
        Message(role=MessageRole.USER, content=user),
    ]
