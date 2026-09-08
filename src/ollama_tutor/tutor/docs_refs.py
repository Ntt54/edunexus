"""Références documentaires curées (010 P2-Pédagogie, T045).

Adapté de ``autreprojet/python-tutor-main`` (``backend/app/docs_refs.py`` :
``DEFAULT_ALLOWED_HOSTS``, ``_CURATED`` tokens→URLs, ``lookup`` max 4
refs + ``online_ok``/``note``).

Le tuteur doit citer de la *vraie* documentation, jamais des URLs
inventées par le LLM : soit l'URL est curée ET sur l'allowlist, soit elle
n'apparaît pas. Différences assumées :
- ``_CURATED`` = sous-ensemble (essentiels du langage + test/typing,
  ~22 clés sur 47) ; le mécanisme est identique, l'extension est un ajout
  de données sans code ;
- ``lookup`` est SYNCHRONE et offline-first (``verify_online=False`` par
  défaut — la source vérifie par défaut via ``TUTOR_DOCS_ONLINE=1``) :
  aucun réseau en tests ; la vérification HEAD reste opt-in explicite
  (httpx importé paresseusement, erreurs jamais propagées).

Stdlib seul (hors httpx opt-in), aucun import UI (ni fastapi ni textual).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit


#: Hôtes autorisés pour toute URL exposée par le tuteur (source, identique).
DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "docs.python.org",
        "packaging.python.org",
        "peps.python.org",
        "docs.pytest.org",
        "pytest.org",
        "typing.readthedocs.io",
        "mypy.readthedocs.io",
        "pip.pypa.io",
        "setuptools.pypa.io",
        "numpy.org",
        "pandas.pydata.org",
        "matplotlib.org",
        "scipy.org",
        "flask.palletsprojects.com",
        "fastapi.tiangolo.com",
        "docs.djangoproject.com",
        "requests.readthedocs.io",
        "httpx.readthedocs.io",
        "docs.sqlalchemy.org",
    }
)


def allowed_hosts() -> frozenset[str]:
    """Hôtes autorisés : ``TUTOR_DOCS_ALLOWLIST`` (CSV) ou le défaut."""
    raw = os.getenv("TUTOR_DOCS_ALLOWLIST", "")
    if not raw.strip():
        return DEFAULT_ALLOWED_HOSTS
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def online_enabled() -> bool:
    return os.getenv("TUTOR_DOCS_ONLINE", "0") == "1"


def online_timeout() -> float:
    try:
        v = float(os.getenv("TUTOR_DOCS_TIMEOUT", "2.0"))
    except ValueError:
        return 2.0
    return max(0.5, min(10.0, v))


#: Sous-ensemble curé (source : essentiels + test/typing). Clé = token
#: minuscule matché en mot entier ; valeur = [(label, url)].
_CURATED: dict[str, list[tuple[str, str]]] = {
    "print": [
        ("print() — built-in function", "https://docs.python.org/3/library/functions.html#print"),
    ],
    "input": [
        ("input() — built-in function", "https://docs.python.org/3/library/functions.html#input"),
    ],
    "len": [
        ("len() — built-in function", "https://docs.python.org/3/library/functions.html#len"),
    ],
    "range": [
        ("range — sequence type", "https://docs.python.org/3/library/stdtypes.html#range"),
        ("range() — built-in function", "https://docs.python.org/3/library/functions.html#func-range"),
    ],
    "for": [
        ("for statements — tutorial", "https://docs.python.org/3/tutorial/controlflow.html#for-statements"),
        ("the for statement — reference", "https://docs.python.org/3/reference/compound_stmts.html#the-for-statement"),
    ],
    "while": [
        ("while statements — reference", "https://docs.python.org/3/reference/compound_stmts.html#the-while-statement"),
    ],
    "if": [
        ("if statements — tutorial", "https://docs.python.org/3/tutorial/controlflow.html#if-statements"),
    ],
    "else": [
        ("if statements — tutorial", "https://docs.python.org/3/tutorial/controlflow.html#if-statements"),
    ],
    "elif": [
        ("if statements — tutorial", "https://docs.python.org/3/tutorial/controlflow.html#if-statements"),
    ],
    "def": [
        ("Defining functions — tutorial", "https://docs.python.org/3/tutorial/controlflow.html#defining-functions"),
    ],
    "return": [
        ("the return statement — reference", "https://docs.python.org/3/reference/simple_stmts.html#the-return-statement"),
    ],
    "lambda": [
        ("Lambda expressions", "https://docs.python.org/3/reference/expressions.html#lambda"),
    ],
    "class": [
        ("Classes — tutorial", "https://docs.python.org/3/tutorial/classes.html"),
    ],
    "import": [
        ("The import system", "https://docs.python.org/3/reference/import.html"),
        ("Modules — tutorial", "https://docs.python.org/3/tutorial/modules.html"),
    ],
    "try": [
        ("Errors and Exceptions — tutorial", "https://docs.python.org/3/tutorial/errors.html"),
    ],
    "except": [
        ("Errors and Exceptions — tutorial", "https://docs.python.org/3/tutorial/errors.html"),
    ],
    "raise": [
        ("Raising exceptions", "https://docs.python.org/3/tutorial/errors.html#raising-exceptions"),
    ],
    "with": [
        ("the with statement", "https://docs.python.org/3/reference/compound_stmts.html#the-with-statement"),
    ],
    "list": [
        ("Lists — tutorial", "https://docs.python.org/3/tutorial/introduction.html#lists"),
    ],
    "dict": [
        ("Dictionaries — tutorial", "https://docs.python.org/3/tutorial/datastructures.html#dictionaries"),
    ],
    "str": [
        ("Text sequence type — str", "https://docs.python.org/3/library/stdtypes.html#text-sequence-type-str"),
    ],
    "test": [
        ("pytest — how to invoke", "https://docs.pytest.org/en/stable/how-to/invoke.html"),
    ],
    "pytest": [
        ("pytest — Get started", "https://docs.pytest.org/en/stable/getting-started.html"),
    ],
    "assert": [
        ("pytest — assertions", "https://docs.pytest.org/en/stable/how-to/assert.html"),
    ],
    "typing": [
        ("typing — type hints", "https://docs.python.org/3/library/typing.html"),
    ],
}

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokenise(text: str) -> set[str]:
    if not text:
        return set()
    tokens = {m.group(0).lower() for m in _TOKEN_RE.finditer(text)}
    lowered = text.lower()
    for compound in ("f-string", "list comprehension", "dict comprehension"):
        if compound in lowered:
            tokens.add(compound.split()[0] if " " in compound else compound)
    return tokens


@dataclass(frozen=True)
class DocRef:
    label: str
    url: str
    source: str  # "curated" | "exercise"


@dataclass
class DocsLookup:
    refs: list[DocRef] = field(default_factory=list)
    online: bool = False
    online_ok: bool = False
    note: str | None = None


def is_allowlisted(url: str, allowed: Iterable[str] | None = None) -> bool:
    """True si URL http(s) sur l'allowlist d'hôtes (port/userinfo neutralisés)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("https", "http"):
        return False
    host = (parts.netloc or "").lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    hosts = frozenset(allowed) if allowed is not None else allowed_hosts()
    return host in hosts


def filter_allowlisted(urls: Iterable[str]) -> list[str]:
    return [u for u in urls if is_allowlisted(u)]


def _curated_for_tokens(tokens: set[str]) -> list[DocRef]:
    seen: set[str] = set()
    out: list[DocRef] = []
    for token in tokens:
        for label, url in _CURATED.get(token, ()):
            if url in seen or not is_allowlisted(url):
                continue
            seen.add(url)
            out.append(DocRef(label=label, url=url, source="curated"))
    return out


def _verify_online_sync(urls: list[str], timeout: float) -> tuple[set[str], bool]:
    """HEAD synchrone (urllib stdlib) ; erreurs → ensemble vide, jamais levées."""
    import urllib.request

    ok: set[str] = set()
    for url in urls:
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 400:
                    ok.add(url)
                    continue
                if status == 405:  # HEAD refusé → repli GET
                    with urllib.request.urlopen(url, timeout=timeout) as resp2:
                        if 200 <= getattr(resp2, "status", 200) < 400:
                            ok.add(url)
        except Exception:  # noqa: BLE001 - vérification best-effort
            continue
    return ok, bool(ok)


def lookup(
    *,
    code: str | None = None,
    question: str | None = None,
    section: str | None = None,
    concepts: Iterable[str] | None = None,
    exercise_refs: Iterable[str] | None = None,
    verify_online: bool = False,
    timeout: float | None = None,
    max_refs: int = 4,
) -> DocsLookup:
    """Références docs crédibles pour un paquet d'évidence (offline-first).

    Tokens extraits de code/question/section/concepts ; curées d'abord,
    refs d'exercice filtrées ensuite ; plafond ``max_refs``. Vérification
    réseau UNIQUEMENT si ``verify_online=True`` explicite (ou env
    ``TUTOR_DOCS_ONLINE=1`` + param None → non, défaut offline).
    """
    tokens: set[str] = set()
    for field_text in (code or "", question or "", section or ""):
        tokens |= _tokenise(field_text)
    for c in concepts or ():
        tokens |= _tokenise(c)

    refs = _curated_for_tokens(tokens)
    for url in exercise_refs or ():
        if not is_allowlisted(url):
            continue
        if any(r.url == url for r in refs):
            continue
        refs.append(DocRef(label=url, url=url, source="exercise"))
    if len(refs) > max_refs:
        refs = refs[:max_refs]

    do_online = bool(verify_online) or (verify_online is None and online_enabled())
    if not do_online or not refs:
        return DocsLookup(refs=refs, online=False, online_ok=False, note=None)
    try:
        reachable, ok = _verify_online_sync(
            [r.url for r in refs], timeout or online_timeout()
        )
    except Exception as exc:  # noqa: BLE001 - jamais propagée
        return DocsLookup(
            refs=refs, online=True, online_ok=False,
            note=f"online check failed: {exc.__class__.__name__}",
        )
    if not ok:
        return DocsLookup(
            refs=refs, online=True, online_ok=False,
            note="docs network unreachable; showing curated references unverified",
        )
    return DocsLookup(
        refs=[r for r in refs if r.url in reachable],
        online=True, online_ok=True, note=None,
    )


__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "DocRef",
    "DocsLookup",
    "allowed_hosts",
    "filter_allowlisted",
    "is_allowlisted",
    "lookup",
    "online_enabled",
    "online_timeout",
]
