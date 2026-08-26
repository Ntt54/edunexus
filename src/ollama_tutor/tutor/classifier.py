"""Classificateur de domaine éducatif pour l'apprentissage adaptatif (Feature 006).

Classe le contenu textuel dans un domaine pédagogique en utilisant des
règles par mots-clés / expressions régulières, avec un repli LLM optionnel.
Contrat UI-framework-free — aucun textual ni fastapi dans ce module.

Utilisation typique :
    >>> from .classifier import classify_content
    >>> domain, conf = await classify_content(texte, client=llm, model="gemma3:1b")
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ..models import Message, MessageRole

# ── Constantes de domaine ──────────────────────────────────────────────

DOMAIN_PROGRAMMATION = "programmation"
DOMAIN_MATHEMATIQUES = "mathematiques"
DOMAIN_SCIENCES = "sciences"
DOMAIN_LANGUES = "langues"
DOMAIN_GENERIQUE = "generique"

ALL_DOMAINS = [
    DOMAIN_PROGRAMMATION,
    DOMAIN_MATHEMATIQUES,
    DOMAIN_SCIENCES,
    DOMAIN_LANGUES,
    DOMAIN_GENERIQUE,
]

# ── Motifs de classification ───────────────────────────────────────────

# Programmation : mots-clés de langages et termes techniques
_PROG_KEYWORDS: list[str] = [
    # mots-clés de langage
    r"\bdef\b", r"\bclass\b", r"\bimport\b", r"\bfunction\b", r"\bconst\b",
    r"\blet\b", r"\bvar\b", r"\breturn\b", r"\bif\b", r"\belse\b",
    r"\bfor\b", r"\bwhile\b", r"\btry\b", r"\bexcept\b", r"\basync\b",
    r"\bawait\b", r"=>",
    # noms de langages
    r"\bpython\b", r"\bjavascript\b", r"\bjava\b", r"\brust\b", r"\bgo\b",
    r"\bc\+\+\b", r"\bc#\b", r"\bswift\b", r"\bkotlin\b", r"\btypescript\b",
    r"\bhtml\b", r"\bcss\b", r"\bsql\b",
    # termes techniques
    r"\bapi\b", r"\bgit\b", r"\bdocker\b", r"\bnpm\b", r"\bpip\b",
    r"\bcompile\b", r"\bdébug\b", r"\bdebug\b", r"\bvariable\b",
    r"\bfonction\b", r"\bobjet\b", r"\bclasse\b", r"\bmodule\b",
    r"\bbibliothèque\b", r"\bpackage\b", r"\bframework\b", r"\bserveur\b",
    r"\bclient\b", r"\bendpoint\b",
]

# Mathématiques : symboles Unicode et vocabulaire
_MATH_SYMBOLS: str = (
    "∑∫∂√∞πθλ≤≥≠≈±×÷²³αβγδεζηκμνξρστφψω"
)
_MATH_KEYWORDS: list[str] = [
    r"\bthéorème\b", r"\blemme\b", r"\bcorollaire\b", r"\bdémonstration\b",
    r"\bpreuve\b", r"\bhypothèse\b", r"\bproposition\b", r"\bformule\b",
    r"\béquation\b", r"\bpolynôme\b", r"\bmatrice\b", r"\bvecteur\b",
    r"\bdérivée\b", r"\bintégrale\b", r"\blimite\b", r"\bconvergence\b",
    r"\bsommatoire\b", r"\bproduit\b", r"\bensemble\b", r"\bprobabilité\b",
    r"\bstatistique\b", r"\bcombinaison\b", r"\bpermutation\b",
    r"\bfactorielle\b", r"\blogarithme\b", r"\bexponentielle\b",
    r"\bsystème\b", r"\binéquation\b", r"\bvariable\b",
    # symboles de notation mathématique dans le texte brut
]

# Sciences : vocabulaire scientifique généraliste
_SCIENCES_KEYWORDS: list[str] = [
    # physique
    r"\batome\b", r"\bphoton\b", r"\bélectron\b", r"\bneutron\b",
    r"\bprotone\b", r"\bgravité\b", r"\binertie\b", r"\bvitesse\b",
    r"\baccélération\b", r"\bpression\b", r"\btempérature\b",
    r"\bénergie\b", r"\bforce\b", r"\bmesure\b", r"\bexpérience\b",
    r"\bentropie\b", r"\benthalpie\b", r"\bquantum\b", r"\bchamp\b",
    r"\bvoltage\b", r"\bcourant\b", r"\brésistance\b",
    # chimie
    r"\bmolécule\b", r"\bréaction chimique\b", r"\bcatalyse\b",
    r"\boxydation\b", r"\bréduction\b", r"\bion\b", r"\bpH\b",
    r"\bsolution\b", r"\bsolvant\b", r"\bsolute\b", r"\bcristal\b",
    # biologie
    r"\bcellule\b", r"\bADN\b", r"\bARN\b", r"\bprotéine\b",
    r"\bmitose\b", r"\bméiose\b", r"\bphotosynthèse\b",
    r"\borganisme\b", r"\bécosystème\b", r"\bgénétique\b",
    r"\bévolution\b", r"\bbactérie\b", r"\bvirus\b",
]

# Langues : grammaire et linguistique
_LANGUES_KEYWORDS: list[str] = [
    r"\bconjugaison\b", r"\bsyntaxe\b", r"\bgrammaire\b",
    r"\bvocabulaire\b", r"\borthographe\b", r"\baccord\b",
    r"\bgenre\b", r"\btemps\b", r"\bverbe\b", r"\badjectif\b",
    r"\badverbe\b", r"\bpronom\b", r"\bdéterminant\b",
    r"\bpréposition\b", r"\bconjonction\b", r"\bphrase\b",
    r"\bproposition\b", r"\bsubordonnée\b", r"\bindicatif\b",
    r"\bsubjonctif\b", r"\bconditionnel\b", r"\bimpératif\b",
    r"\binfinitif\b", r"\bparticipe\b", r"\bgéronif\b",
    r"\bconjuguer\b", r"\bnom\b", r"\bsujet\b", r"\bcomplément\b",
    r"\bsujet\b", r"\battribut\b", r"\bCOD\b", r"\bCOI\b",
]

# Compilation des motifs une seule fois
_PROG_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _PROG_KEYWORDS]
_MATH_KEYWORD_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _MATH_KEYWORDS]
_MATH_SYMBOL_RE = re.compile(f"[{re.escape(_MATH_SYMBOLS)}]")
_SCIENCES_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _SCIENCES_KEYWORDS]
_LANGUES_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _LANGUES_KEYWORDS]

# ── Classification par règles ──────────────────────────────────────────


def _count_matches(text: str, patterns: list[re.Pattern[str]]) -> int:
    """Compter le nombre total de correspondances dans le texte."""
    return sum(len(p.findall(text)) for p in patterns)


def _compute_confidence(match_count: int, word_count: int) -> float:
    """Calculer la confiance normalisée.

    Formule : min(1.0, match_count * 2 / max(word_count, 1))
    Plancher 0.3 s'il y a au moins un match, 0.1 sinon.
    """
    if match_count == 0:
        return 0.1
    raw = match_count * 2.0 / max(word_count, 1)
    return max(0.3, min(1.0, raw))


def classify_rules(text: str) -> tuple[str, float]:
    """Classifier le texte dans un domaine à l'aide de règles par mots-clés.

    Analyse le texte, compte les correspondances par domaine et retourne
    celui avec la plus grande densité de matches par rapport à la longueur
    du texte.

    Args:
        text: Le contenu textuel à classifier.

    Returns:
        Tuple ``(domaine, confiance)`` où la confiance est entre 0.0 et 1.0.
    """
    if not text or not text.strip():
        return DOMAIN_GENERIQUE, 0.1

    word_count = max(len(text.split()), 1)
    text_lower = text.lower()

    # --- Programmation ---
    # On compte les mots-clés standard + les signes de code (paranthèses
    # accolades, crochets) quand ils apparaissent en contexte de code.
    prog_matches = _count_matches(text, _PROG_PATTERNS)
    # Bonus pour les patterns de code visibles (lignes avec indentation +
    # ponctuation de code)
    code_lines = sum(
        1 for line in text.splitlines()
        if re.match(r"^\s{2,}", line) and re.search(r"[(){}\[\];=]", line)
    )
    prog_matches += code_lines

    # --- Mathématiques ---
    math_kw_matches = _count_matches(text, _MATH_KEYWORD_PATTERNS)
    math_sym_matches = len(_MATH_SYMBOL_RE.findall(text))
    math_matches = math_kw_matches + math_sym_matches

    # --- Sciences ---
    sciences_matches = _count_matches(text, _SCIENCES_PATTERNS)

    # --- Langues ---
    langues_matches = _count_matches(text, _LANGUES_PATTERNS)

    # Dictionnaire domaine → nb de matches
    scores: dict[str, int] = {
        DOMAIN_PROGRAMMATION: prog_matches,
        DOMAIN_MATHEMATIQUES: math_matches,
        DOMAIN_SCIENCES: sciences_matches,
        DOMAIN_LANGUES: langues_matches,
    }

    # Trouver le domaine avec le plus de matches
    best_domain = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_count = scores[best_domain]

    if best_count == 0:
        return DOMAIN_GENERIQUE, 0.1

    confidence = _compute_confidence(best_count, word_count)
    return best_domain, confidence


# ── Classification par LLM ─────────────────────────────────────────────

_CLASSIFY_PROMPT = (
    "Tu es un classificateur de contenu éducatif. "
    "Analyse le texte suivant et retourne UNIQUEMENT un objet JSON "
    'avec une clé "domaine" dont la valeur est une des chaînes : '
    '"programmation", "mathematiques", "sciences", "langues", "generique". '
    "Ne retourne rien d'autre que le JSON.\n\n"
    "Texte à classifier :\n{text}"
)

# Mots-clés attendus dans la réponse LLM (normalisés minuscule)
_DOMAIN_ALIASES: dict[str, str] = {
    "programmation": DOMAIN_PROGRAMMATION,
    "code": DOMAIN_PROGRAMMATION,
    "informatique": DOMAIN_PROGRAMMATION,
    "mathematiques": DOMAIN_MATHEMATIQUES,
    "mathématiques": DOMAIN_MATHEMATIQUES,
    "maths": DOMAIN_MATHEMATIQUES,
    "sciences": DOMAIN_SCIENCES,
    "science": DOMAIN_SCIENCES,
    "physique": DOMAIN_SCIENCES,
    "chimie": DOMAIN_SCIENCES,
    "biologie": DOMAIN_SCIENCES,
    "langues": DOMAIN_LANGUES,
    "langue": DOMAIN_LANGUES,
    "français": DOMAIN_LANGUES,
    "grammaire": DOMAIN_LANGUES,
    "generique": DOMAIN_GENERIQUE,
    "générique": DOMAIN_GENERIQUE,
    "général": DOMAIN_GENERIQUE,
}


def _parse_domain_from_response(raw: str) -> str | None:
    """Extraire le domaine de la réponse brute du LLM.

    Tente d'abord de parser du JSON, sinon cherche un des domaines connus
    dans la réponse.
    """
    # Essayer JSON
    try:
        data = json.loads(raw.strip())
        if isinstance(data, dict):
            val = str(data.get("domaine", data.get("domain", ""))).lower().strip()
            if val in _DOMAIN_ALIASES:
                return _DOMAIN_ALIASES[val]
    except (json.JSONDecodeError, ValueError):
        pass

    # Recherche de texte libre
    raw_lower = raw.lower()
    for alias, domain in _DOMAIN_ALIASES.items():
        if alias in raw_lower:
            return domain

    return None


async def classify_llm(client: Any, text: str, model: str) -> tuple[str, float]:
    """Classifier le texte en utilisant le LLM.

    Envoie un prompt demandant au LLM de catégoriser le texte, parse la
    réponse JSON ``{"domaine": "..."}``.

    Args:
        client: Client LLM (OllamaClient ou OpenAICompatProvider).
        text: Le contenu à classifier.
        model: Nom du modèle à utiliser.

    Returns:
        Tuple ``(domaine, confiance)`` — ``(domaine, 0.85)`` en cas de
        succès, ``(generique, 0.5)`` en cas d'échec.
    """
    # Tronquer le texte pour ne pas dépasser le contexte du modèle
    truncated = text[:4000] if len(text) > 4000 else text
    prompt = _CLASSIFY_PROMPT.format(text=truncated)

    messages = [
        Message(role=MessageRole.SYSTEM, content="Tu es un classificateur. Réponds uniquement en JSON."),
        Message(role=MessageRole.USER, content=prompt),
    ]

    try:
        # Accumuler la réponse du stream
        chunks: list[str] = []
        async for event in client.chat_stream(messages=messages, model=model):
            # Les deux clients (OllamaClient, OpenAICompatProvider) émettent
            # des événements avec un attribut `content` (chaîne).
            content = getattr(event, "content", None)
            if content:
                chunks.append(content)

        full_response = "".join(chunks)
        domain = _parse_domain_from_response(full_response)

        if domain and domain in ALL_DOMAINS:
            return domain, 0.85
        return DOMAIN_GENERIQUE, 0.5

    except Exception:  # noqa: BLE001
        # Toute erreur LLM → fallback generique
        return DOMAIN_GENERIQUE, 0.5


# ── Classification hybride ─────────────────────────────────────────────


async def classify_content(
    text: str,
    client: Any | None = None,
    model: str = "",
    confidence_threshold: float = 0.5,
) -> tuple[str, float]:
    """Classifier avec les règles d'abord, repli LLM si la confiance est faible.

    Pipeline hybride :
    1. Appliquer les règles regex/mots-clés.
    2. Si la confiance ≥ ``confidence_threshold``, retourner le résultat.
    3. Sinon, tenter une classification par LLM (si ``client`` est fourni).

    Args:
        text: Le contenu textuel à classifier.
        client: Client LLM optionnel (OllamaClient ou OpenAICompatProvider).
        model: Nom du modèle pour le repli LLM.
        confidence_threshold: Seuil minimum de confiance pour ignorer le LLM.

    Returns:
        Tuple ``(domaine, confiance)``.
    """
    # Étape 1 — classification par règles
    domain, confidence = classify_rules(text)

    if confidence >= confidence_threshold:
        return domain, confidence

    # Étape 2 — repli LLM si un client est disponible
    if client is not None and model:
        llm_domain, llm_confidence = await classify_llm(client, text, model)
        # Ne garder le résultat LLM que s'il est plus confiant
        if llm_confidence > confidence:
            return llm_domain, llm_confidence

    return domain, confidence


# ── Classification par lots ─────────────────────────────────────────────

_SAMPLE_LIMIT = 20  # Nombre max de chunks à classifier pour le vote


async def classify_subject_chunks(
    store: Any,  # LibraryStore
    subject_id: str,
    client: Any | None = None,
    model: str = "",
) -> str:
    """Classifier tous les chunks d'un sujet par vote majoritaire.

    Charge les chunks du sujet, en échantillonne au plus 20 (ou tous si
    moins), classe chacun, puis désigne le domaine gagnant par vote
    majoritaire. Le résultat est stocké comme domaine du sujet.

    Args:
        store: Instance de ``LibraryStore`` (doit exister ``get_subject_chunks``
            et ``set_subject_domain``).
        subject_id: Identifiant du sujet.
        client: Client LLM optionnel pour la classification hybride.
        model: Nom du modèle LLM.
        index: Index d'embedding (non utilisé, gardé pour compatibilité).

    Returns:
        Le domaine dominant (str parmi ``ALL_DOMAINS``).
    """
    chunks = store.get_subject_chunks(subject_id)

    if not chunks:
        store.set_subject_domain(subject_id, DOMAIN_GENERIQUE)
        return DOMAIN_GENERIQUE

    # Échantillonner les premiers N chunks
    sample = chunks[:_SAMPLE_LIMIT]
    votes: Counter[str] = Counter()

    for chunk in sample:
        text = chunk.get("text", "")
        if not text.strip():
            continue
        domain, _confidence = await classify_content(text, client=client, model=model)
        votes[domain] += 1

    if not votes:
        winner = DOMAIN_GENERIQUE
    else:
        winner = votes.most_common(1)[0][0]

    store.set_subject_domain(subject_id, winner)
    return winner
