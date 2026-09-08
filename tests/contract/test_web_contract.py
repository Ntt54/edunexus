"""US4 P1-B (T026) — contract test web : fetch() du front vs routes du back.

Adapté de ``autreprojet/open-tutor-ai-CE-main/tests/test_contract_coverage.py``
(405 l. : scan ``fetch(...)`` + ``_BASE_URL_MAP``, normalisation
``${chatId}`` → ``{param}``, assert chaque (METHOD, path) ∈ schéma, motifs
interdits anti-legacy, exclusions documentées) au couple
``web/static/tutor.html`` (``fetch(``/``jf(``/``sf(`` + ``method:`` dans
l'appel, défaut GET, chemins ``/api/…``) vs ``web/server.py``
(``app.get/post/put/delete/patch/websocket`` + routers inclus, segments
``{param}`` joker).

Tolérance zéro + allowlist explicite et motivée (FR-007 clarifié) :
- sens front → back : tout endpoint fetché matche une route (même méthode) ;
- sens back → front : toute route ``/api/…`` non-WS est fetchée par au
  moins un espace, sauf ``IGNORED_ROUTES`` motivées ci-dessous ;
- motifs legacy interdits côté front (``FORBIDDEN_UI_PATTERNS``).

Garanties : 100 % offline (lecture fichiers + routes runtime via
``create_app`` SANS démarrer le serveur, aucun TestClient requis) ; < 2 s ;
échecs listant les orphelins avec fichier:ligne.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTOR_HTML = REPO_ROOT / "src" / "ollama_tutor" / "web" / "static" / "tutor.html"
SERVER_PY = REPO_ROOT / "src" / "ollama_tutor" / "web" / "server.py"

# ---------------------------------------------------------------------------
# Allowlist motivée (tolérance zéro par défaut, FR-007 clarifié)
# ---------------------------------------------------------------------------

# Routes /api réellement appelées mais invisibles au scanner statique
# (méthode construite dynamiquement ou appel hors fetch direct).
# Vide par design : le scanner résout LOG_ENDPOINT (const) et toutes les
# méthodes inline ; toute régression du scanner doit faire rougir ce test
# plutôt qu'être masquée ici.
FETCH_ALLOWLIST: set[tuple[str, str]] = set()

# Routes /api non-WS que le front n'a pas à appeler (sens back → front).
# Chaque entrée est motivée ; toute nouvelle route non listée ici DOIT
# être fetchée par au moins un espace (ou ajoutée avec motif).
# Décisions T027 : le sens front → back est VERT sans exception (aucun
# fetch mort, aucune route manquante) ; les 46 routes ci-dessous sont
# réellement absentes de tutor.html (zéro occurrence vérifiée, pas
# d'angle mort du scanner) et consommées par la SPA Vue — UI par défaut
# servie en /tutor (web/vue/client/src/services/api.ts, vues et stores ;
# vérifié par grep le 2026-09-08) — ou réservées aux sondes/admin.
IGNORED_ROUTES: set[tuple[str, str]] = {
    # --- Boot/config globale (SPA Vue, pas tutor.html) ---
    ("GET", "/api/tutor/config"),  # config lue au boot de la SPA Vue
    ("GET", "/api/tutor/profile"),  # profil apprenant global (SPA Vue)
    # --- Corpora (CRUD : SPA Vue LibraryView + services/api.ts) ---
    ("GET", "/api/tutor/corpora"),
    ("POST", "/api/tutor/corpora"),
    ("DELETE", "/api/tutor/corpora/{}"),
    ("POST", "/api/tutor/corpora/{}/rename"),
    ("GET", "/api/tutor/books/{}/corpora"),
    ("PUT", "/api/tutor/books/{}/corpora"),
    ("DELETE", "/api/tutor/books/{}/corpora/{}"),
    # --- Diagnostic / examens / exercices (SPA Vue : diagnostic, Épreuve) ---
    ("POST", "/api/tutor/subjects/{}/diagnostic"),
    ("POST", "/api/tutor/diagnostic/{}/answer"),
    ("GET", "/api/tutor/diagnostic/{}/result"),
    ("POST", "/api/tutor/exam/import"),
    ("POST", "/api/tutor/exam/analyze"),
    ("POST", "/api/tutor/exam/questions/{}/resolve"),
    ("POST", "/api/tutor/answers"),  # espaces Exercices (SPA Vue)
    ("POST", "/api/tutor/solution"),  # espaces Exercices (SPA Vue)
    # --- Parcours / révision (SPA Vue : PathView + services/api.ts) ---
    ("POST", "/api/tutor/subjects/{}/auto-path"),
    ("POST", "/api/tutor/subjects/{}/path/from-program"),
    ("POST", "/api/tutor/subjects/{}/path/generate-from-books"),
    ("PUT", "/api/tutor/subjects/{}/path"),
    ("PUT", "/api/tutor/path"),
    ("POST", "/api/tutor/subjects/{}/revision-sheet"),
    # --- Leçon : validation manuelle (SPA Vue, services/api.ts) ---
    ("POST", "/api/tutor/lesson-discussions/{}/complete-manual"),
    # --- Sessions / reprise (SPA Vue : api.ts + useTutorSocket) ---
    ("GET", "/api/tutor/subjects/{}/resume"),
    ("GET", "/api/tutor/subjects/{}/sessions"),
    ("POST", "/api/tutor/sessions/{}/close"),
    ("GET", "/api/tutor/subjects/{}/errors"),  # historique (SPA Vue)
    # --- Apprenants : suppression admin (SPA Vue, pas tutor.html) ---
    ("DELETE", "/api/tutor/learners/{}"),
    # --- Photos : détail inline (SPA Vue ; tutor.html a photo+confirm) ---
    ("GET", "/api/tutor/conversation-photos/{}"),
    # --- Quiz : détail (SPA Vue ; tutor.html a create + submit) ---
    ("GET", "/api/tutor/quizzes/{}"),
    # --- Opérations livres (SPA Vue LibraryView ; pas d'équivalent legacy) ---
    ("POST", "/api/tutor/books/{}/cancel"),
    ("POST", "/api/tutor/books/{}/retry"),
    ("POST", "/api/tutor/books/{}/reindex"),
    ("POST", "/api/tutor/books/{}/summary"),
    # --- Planificateur (SPA Vue Réglages ; tutor.html lit le statut) ---
    ("POST", "/api/tutor/nightly/start"),
    ("POST", "/api/tutor/nightly/stop"),
    # --- RAG Pleias (SPA Vue ; pas de panneau legacy) ---
    ("POST", "/api/tutor/pleias/ask"),
    ("GET", "/api/tutor/pleias/status"),
    # --- Adaptation (SPA Vue ; recalcul explicite) ---
    ("POST", "/api/tutor/subjects/{}/adaptation/recompute"),
    # --- Agrégats globaux (console admin ; le front utilise /subjects/{}/…) ---
    ("GET", "/api/tutor/errors"),
    ("GET", "/api/tutor/gaps"),
    ("GET", "/api/tutor/progress"),
    # --- Sondes / maintenance (console admin, jamais un espace) ---
    ("GET", "/api/tutor/pgvector/status"),
    ("GET", "/api/tutor/stale-books"),
    ("GET", "/api/tutor/path-steps"),
    # --- Jobs US3 : détail ciblé (le panneau Bibliothèque ne polle que la
    # liste ; le détail reste disponible pour suivi unitaire / debug) ---
    ("GET", "/api/ingestion/jobs/{}"),
}

# Motifs legacy interdits dans le front (adapté de FORBIDDEN_PATTERNS :
# pas de namespace OpenWebUI/Socket.IO dans ce projet, pas d'appel brut
# vers le démon Ollama depuis le navigateur).
FORBIDDEN_UI_PATTERNS = [
    "/ws/socket",  # legacy Socket.IO (le realtime est /ws/tutor)
    "open_webui",  # aucune référence runtime OpenWebUI
    "localhost:11434",  # le navigateur ne parle jamais au démon en direct
    "127.0.0.1:11434",  # idem (tout passe par le backend FastAPI)
]


# ---------------------------------------------------------------------------
# Normalisation (les segments {param} sont des jokers, data-model E-007)
# ---------------------------------------------------------------------------


def _normalize(path: str) -> str:
    """``/api/tutor/books/12?x=1`` → ``/api/tutor/books/{}``."""
    p = path.split("?")[0].split("#")[0].strip()
    p = re.sub(r"\$\{[^}]*\}", "{}", p)  # template JS ${id}
    p = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "{}", p)  # {book_id}, {id}…
    p = re.sub(r"(\{\})+", "{}", p)  # var query (qs="?..."/"") collée → un joker
    p = re.sub(r"^/tutor(?=/api)", "", p)  # préfixe /tutor normalisé
    return re.sub(r"/+", "/", p).rstrip("/") or "/"


# ---------------------------------------------------------------------------
# Extraction front : fetch(/jf(/sf( dans tutor.html
# ---------------------------------------------------------------------------

_CALL_RE = re.compile(r"\b(jf|sf|fetch)\s*\(")
_STRQ_RE = re.compile(r"""\s*(?P<q>["'`])""")
_METHOD_RE = re.compile(r"""method\s*:\s*["']([A-Z]+)["']""")
_CONST_RE = re.compile(
    r"""(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(["'])(/api/[^"'`]*)\2"""
)
_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _const_endpoints(html: str) -> dict[str, str]:
    """``const X = "/api/…"`` → résolution des fetch(X, …)."""
    return {m.group(1): m.group(3) for m in _CONST_RE.finditer(html)}


def _parse_url_expr(buf: str, first_quote: str, consts: dict[str, str]) -> str | None:
    """Parse une expression d'URL JS mono-ligne → gabarit normalisable.

    Gère littéraux, templates `` `${x}` ``, concaténations ``"a" + x`` et
    appels ``encodeURIComponent(x)`` (→ ``{}``). Retourne None si
    non résoluble (ex. variable opaque).
    """
    parts: list[str] = []
    in_str: str | None = first_quote
    lit = ""
    depth = 0
    k = 0
    closed_once = False
    while k < len(buf):
        ch = buf[k]
        if in_str is not None:
            if ch == in_str:
                parts.append(lit)
                lit = ""
                in_str = None
                closed_once = True
            elif ch == "\\" and k + 1 < len(buf):
                lit += buf[k + 1]
                k += 1
            elif (
                first_quote == "`"
                and ch == "$"
                and k + 1 < len(buf)
                and buf[k + 1] == "{"
            ):
                end = buf.find("}", k)
                parts.append(lit)
                parts.append("{}")
                lit = ""
                k = end if end != -1 else k
            else:
                lit += ch
        else:
            if ch in ("\"", "'", "`"):
                in_str = ch
            elif ch == "," and depth == 0:
                break
            elif ch == "(":
                # Appel fn(...) → UN joker ; groupe nu (ternaire, EF-007 :
                # ``"?" + qs…``) → sauté SANS joker (fragment query-only).
                # (compteur local : `depth` garde son rôle de garde-virgule
                # pour la fin d'argument).
                prev = buf[:k].rstrip()[-1:] if buf[:k].rstrip() else ""
                is_call = bool(prev) and (prev.isalnum() or prev in "_$)]")
                d = 1
                k += 1
                while k < len(buf) and d:
                    if buf[k] == "(":
                        d += 1
                    elif buf[k] == ")":
                        d -= 1
                    k += 1
                if is_call:
                    parts.append("{}")
                continue
            elif ch in ("+", " ", "\t"):
                pass
            elif ch == ")":
                break
            elif ch == ".":
                # Accès propriété (b.id, S.activeId) : absorbé avec l'objet
                # (qui a déjà produit son joker), jamais un segment de plus.
                m = re.match(r"\.[A-Za-z_$][\w$]*", buf[k:])
                if m:
                    k += len(m.group(0))
                    continue
                return None
            else:
                m = _WORD_RE.match(buf, k)
                if m:
                    word = m.group(0)
                    k += len(word)
                    # Appel fonctionnel (encodeURIComponent(x), String(x)) :
                    # le mot est absorbé, la parenthèse produit UN joker.
                    rest = buf[k:].lstrip()
                    if rest.startswith("("):
                        continue
                    if word in consts:
                        parts.append(consts[word])
                    elif closed_once or parts:
                        # identifiant post-littéral (lid, b.id…) → joker
                        parts.append("{}")
                    else:
                        return None  # URL entièrement variable → non résoluble
                    continue
                else:
                    return None
        k += 1
    if not closed_once and not parts:
        return None
    if lit:
        parts.append(lit)
    return "".join(parts) or None


def scan_front_endpoints(html_path: Path = TUTOR_HTML) -> dict[tuple[str, str], list[str]]:
    """``{(METHOD, gabarit): ["fichier:ligne", …]}`` depuis tutor.html."""
    html = html_path.read_text(encoding="utf-8")
    hlines = html.splitlines()
    consts = _const_endpoints(html)
    found: dict[tuple[str, str], list[str]] = {}

    for m in _CALL_RE.finditer(html):
        lineno = html.count("\n", 0, m.start()) + 1
        line = hlines[lineno - 1]
        # Définitions des wrappers eux-mêmes, pas des appels métier.
        if re.search(r"function\s+(jf|sf)\s*\(", line):
            continue
        line_end = html.find("\n", m.end())
        frag = html[m.end() : line_end]
        qm = _STRQ_RE.match(frag)
        template: str | None = None
        if qm:
            template = _parse_url_expr(frag[qm.end() :], qm.group("q"), consts)
        else:
            wm = re.match(r"\s*([A-Za-z_$][\w$]*)", frag)
            if wm and wm.group(1) in consts:
                template = consts[wm.group(1)]
        if template is None or not template.startswith("/api/"):
            continue
        # Méthode : cherchée dans TOUT l'appel (parenthèses équilibrées,
        # borne anti-fuite), défaut GET (spec Fetch). Ceci évite les
        # attributions croisées entre appels voisins.
        depth = 1
        k = m.end()
        cap = min(len(html), k + 4000)
        while k < cap and depth:
            if html[k] == "(":
                depth += 1
            elif html[k] == ")":
                depth -= 1
            k += 1
        mm = _METHOD_RE.search(html[m.end() : k])
        method = mm.group(1) if mm else "GET"
        key = (method, _normalize(template))
        found.setdefault(key, []).append(f"tutor.html:{lineno}")
    return found


# ---------------------------------------------------------------------------
# Extraction back : routes runtime via create_app (SANS démarrer le serveur)
# ---------------------------------------------------------------------------

_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")


def scan_back_routes() -> dict[tuple[str, str], str]:
    """``{(METHOD, gabarit): "/chemin/origine"}`` depuis l'app FastAPI.

    Importe les routes SANS démarrer le serveur : ``create_app`` construit
    l'app (100 % offline, aucun bind/listen) et on lit ``app.routes``.
    Inclut ``include_router`` éventuels (préfixe appliqué par FastAPI).
    """
    import tempfile

    import src.ollama_tutor.web.server as web_server

    with tempfile.TemporaryDirectory(prefix="web-contract-") as tmp:
        app = web_server.create_app(config_dir=Path(tmp))
        routes: dict[tuple[str, str], str] = {}
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None) or set()
            if not methods:
                # Route WebSocket (pas de methods) : mémorisée quel que soit
                # le préfixe (le realtime vit en /ws/…), exclue du sens
                # back → front (contrôle d'existence dédié).
                if path:
                    routes[("WEBSOCKET", _normalize(path))] = "websocket"
                continue
            if not path.startswith("/api/"):
                continue
            for method in methods:
                if method.upper() in _HTTP_METHODS:
                    routes[(method.upper(), _normalize(path))] = "route"
        return routes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_fetched_endpoint_has_route() -> None:
    """Tout endpoint fetché matche une route (même méthode)."""
    fetched = scan_front_endpoints()
    assert fetched, "le scanner doit trouver des endpoints dans tutor.html"
    routes = scan_back_routes()
    missing = sorted(
        f"{method} {path}  (vu en {', '.join(sorted(set(locs)))})"
        for (method, path), locs in fetched.items()
        if (method, path) not in routes and (method, path) not in FETCH_ALLOWLIST
    )
    assert not missing, (
        f"\n{len(missing)} endpoint(s) fetché(s) sans route backend :\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_every_api_route_is_used() -> None:
    """Toute route /api non-WS est fetchée, sauf allowlist motivée."""
    fetched = scan_front_endpoints()
    routes = scan_back_routes()
    unused = sorted(
        f"{method} {path}"
        for (method, path) in routes
        if method != "WEBSOCKET"
        and (method, path) not in fetched
        and (method, path) not in IGNORED_ROUTES
    )
    assert not unused, (
        f"\n{len(unused)} route(s) backend jamais fetchée(s) par le front :\n"
        + "\n".join(f"  - {u}" for u in unused)
        + "\nCorriger (fetch mort / route à brancher) ou motiver en IGNORED_ROUTES."
    )


def test_allowlist_entries_are_documented() -> None:
    """Garde-fou anti-derive : chaque entrée d'allowlist existe encore."""
    routes = scan_back_routes()
    stale_fetch = sorted(
        f"{method} {path}"
        for (method, path) in FETCH_ALLOWLIST
        if (method, path) not in routes
    )
    stale_ignored = sorted(
        f"{method} {path}"
        for (method, path) in IGNORED_ROUTES
        if (method, path) not in routes
    )
    assert not stale_fetch + stale_ignored, (
        "\nEntrée(s) d'allowlist périmée(s) (route disparue — nettoyer) :\n"
        + "\n".join(f"  - {s}" for s in stale_fetch + stale_ignored)
    )


def test_no_forbidden_paths_in_ui() -> None:
    """Motifs legacy interdits absents du front (anti-régression)."""
    html = TUTOR_HTML.read_text(encoding="utf-8")
    violations = [
        f"tutor.html:{i}: motif interdit {pat!r}"
        for pat in FORBIDDEN_UI_PATTERNS
        for i, line in enumerate(html.splitlines(), 1)
        if pat in line
    ]
    assert not violations, (
        f"\n{len(violations)} motif(s) legacy dans le front :\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_websocket_route_exists() -> None:
    """Le realtime (/ws/tutor, exclu du ping-pong HTTP) reste déclaré."""
    routes = scan_back_routes()
    assert ("WEBSOCKET", "/ws/tutor") in routes, (
        "route WebSocket /ws/tutor introuvable (requis par les 9 espaces)"
    )


def test_scanner_spots_known_endpoints() -> None:
    """Sanity : le scanner voit les endpoints structurants (anti-aveugle)."""
    fetched = scan_front_endpoints()
    keys = set(fetched)
    assert ("GET", "/api/tutor/subjects") in keys
    assert ("POST", "/api/tutor/import") in keys
    assert ("GET", "/api/ingestion/jobs") in keys
    assert ("POST", "/api/log-error") in keys
    # joker {param} : /api/tutor/books/<id> et /api/tutor/subjects/<id>/profile
    assert ("DELETE", "/api/tutor/book/{}") in keys
    assert ("GET", "/api/tutor/subjects/{}/profile") in keys
