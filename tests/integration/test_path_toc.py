"""TOC exploitables pour les parcours (chapitres réels + anti-miettes).

Constat prod : 3035 chunks indexés mais AUCUN avec `chapter` renseigné —
TOC plates, étapes miettes (« linewidth : épaisseur du trait »).

100 % offline, SANS vrais PDF (outlines simulés via PdfReader patché,
chunks fabriqués) :
- outlines PDF natifs → `chapter` par page (sans outline : rien, sans crash) ;
- heuristique titres (jamais de code/commentaire comme chapitre) ;
- garde-fous étapes LLM + repli (longueur, phrase, code, dedupe) ;
- repli filtré sous le minimum ⇒ erreur explicite (422 route).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.extractors import chunk_text_structured
from src.ollama_tutor.tutor.service import PathGenerationError, TutorService
from src.ollama_tutor.tutor.store import LibraryStore


@pytest.fixture
def svc(tmp_path: Path) -> SimpleNamespace:
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    service = TutorService(store, None, config)
    return SimpleNamespace(store=store, config=config, service=service)


def _seed_book(
    svc: SimpleNamespace,
    tmp_path: Path,
    subject_id: str,
    name: str,
    chapters: list[tuple[str, list[str]]],
) -> str:
    p = tmp_path / name
    p.write_text(f"contenu {name} " * 10, encoding="utf-8")
    book = svc.store.import_document(subject_id, str(p))
    chunks: list[dict] = []
    vecs: list[list[float]] = []
    for ch_title, sections in chapters:
        if sections:
            for sec in sections:
                chunks.append(
                    {"text": f"{ch_title} {sec} texte", "chapter": ch_title, "section": sec}
                )
                vecs.append([0.1, 0.2])
        else:
            chunks.append({"text": f"{ch_title} texte", "chapter": ch_title, "section": ""})
            vecs.append([0.1, 0.2])
    if chunks:
        svc.store.add_chunks(subject_id, book.id, chunks, vecs, "test-model")
    return book.id


# ---------------------------------------------------------------------------
# 1. Outlines PDF natifs (simulés, sans vrai PDF)
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeDest:
    def __init__(self, title: str, pagenum_0based: int) -> None:
        self.title = title
        self._pagenum = pagenum_0based


class _FakeReader:
    outline: list = []

    def __init__(self, path: str) -> None:
        self.pages = [
            _FakePage("Page de garde du manuel."),
            _FakePage("Contenu du chapitre un sur les boucles."),
            _FakePage("Contenu du chapitre deux sur les fonctions."),
        ]

    def get_destination_page_number(self, dest: _FakeDest) -> int:
        return dest._pagenum


def test_pdf_outlines_become_chapters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.ollama_tutor.tutor import extractors

    _FakeReader.outline = [
        _FakeDest("Les boucles", 1),
        [_FakeDest("Boucle for", 1), _FakeDest("Boucle while", 2)],
    ]
    monkeypatch.setattr("pypdf.PdfReader", _FakeReader)
    p = tmp_path / "manuel.pdf"
    p.write_bytes(b"%PDF-fake")
    segments = list(extractors.extract_text(p, fmt="pdf"))
    assert len(segments) == 3
    assert segments[0][1].get("chapter") in (None, "")
    assert segments[1][1].get("chapter") == "Boucle for"
    assert segments[2][1].get("chapter") == "Boucle while"


def test_pdf_without_outline_no_chapter_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.ollama_tutor.tutor import extractors

    _FakeReader.outline = []
    monkeypatch.setattr("pypdf.PdfReader", _FakeReader)
    p = tmp_path / "manuel.pdf"
    p.write_bytes(b"%PDF-fake")
    segments = list(extractors.extract_text(p, fmt="pdf"))
    assert len(segments) == 3
    assert all(not s[1].get("chapter") for s in segments)


def test_pdf_broken_outline_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.ollama_tutor.tutor import extractors

    class _BrokenReader(_FakeReader):
        @property
        def outline(self):  # type: ignore[override]
            raise RuntimeError("outline illisible")

    monkeypatch.setattr("pypdf.PdfReader", _BrokenReader)
    p = tmp_path / "manuel.pdf"
    p.write_bytes(b"%PDF-fake")
    segments = list(extractors.extract_text(p, fmt="pdf"))
    assert len(segments) == 3


# ---------------------------------------------------------------------------
# 1b. Heuristique titres (jamais de code)
# ---------------------------------------------------------------------------


def test_heading_h1_becomes_chapter_section_kept() -> None:
    text = "# Les boucles en Python\n\nContenu des boucles ici.\n\n## La boucle for\n\nDétails for."
    chunks = chunk_text_structured(text, max_chars=1200)
    assert chunks[0].get("chapter") == "Les boucles en Python"
    assert "boucle for" in (chunks[-1].get("section") or "").lower()


def test_numbered_title_lines_become_chapters() -> None:
    text = (
        "Chapitre 1 : les variables\n\n"
        "Les variables stockent des valeurs en mémoire pour les réutiliser.\n\n"
        "Chapitre 2 : les boucles\n\n"
        "Les boucles répètent un bloc d'instructions plusieurs fois de suite."
    )
    chunks = chunk_text_structured(text, max_chars=1200)
    chapters = [c.get("chapter") for c in chunks]
    assert chapters[0] == "Chapitre 1 : les variables"
    assert chapters[-1] == "Chapitre 2 : les boucles"


def test_code_never_becomes_chapter() -> None:
    text = (
        "x = 3\n"
        "for i in range(5):\n"
        "    print(i)\n\n"
        "Un vrai paragraphe de cours qui explique patiemment les variables et leur portée."
    )
    chunks = chunk_text_structured(text, max_chars=1200)
    assert all(not c.get("chapter") for c in chunks)


def test_comment_never_becomes_chapter() -> None:
    text = (
        "// TODO: optimiser cette boucle, commentaire de code\n"
        "x = 1\n\n"
        "Un paragraphe de cours qui explique les commentaires en Python et leur usage."
    )
    chunks = chunk_text_structured(text, max_chars=1200)
    assert all(not c.get("chapter") for c in chunks)


def test_hash_code_comment_never_becomes_chapter() -> None:
    text = (
        "# x = 1  # initialisation, commentaire de code\n"
        "y = 2\n\n"
        "Un paragraphe de cours qui explique l'affectation des variables en Python."
    )
    chunks = chunk_text_structured(text, max_chars=1200)
    assert all(not c.get("chapter") for c in chunks)


# ---------------------------------------------------------------------------
# 2. Garde-fous qualité des titres d'étapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "linewidth : épaisseur du trait",
        "de la boucle while",
        "x = 3",
        "a := 3",
        "for i in range(5)",
        "A",
        "   ",
        "",
    ],
)
def test_crumb_titles_rejected(title: str) -> None:
    assert TutorService._is_step_title_usable(title) is False


@pytest.mark.parametrize(
    "title",
    [
        "La boucle while répète le bloc",
        "Comprendre les tuples en Python",
        "Les variables et leur portée",
        # Titres courts légitimes (seuil 15c rejeté : suites existantes).
        "Leçon 1",
        "Cellule",
        "ADN",
        "Court",
    ],
)
def test_good_titles_kept(title: str) -> None:
    assert TutorService._is_step_title_usable(title) is True


# ---------------------------------------------------------------------------
# 3. e2e : miettes ⇒ erreur, bons chapitres ⇒ étapes
# ---------------------------------------------------------------------------


def _mock_llm(monkeypatch: pytest.MonkeyPatch, payload: str):
    async def _fake(self, messages, options):
        return payload

    monkeypatch.setattr(TutorService, "_llm_collect", _fake)


@pytest.mark.asyncio
async def test_crumbs_only_raises_no_miettes(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(
        svc, tmp_path, sid, "miettes.txt",
        [
            ("de la boucle while", []),
            ("x = 3", []),
            ("linewidth : épaisseur du trait", []),
        ],
    )
    _mock_llm(monkeypatch, "garbage non json")
    with pytest.raises(PathGenerationError, match="insuffisants"):
        await svc.service.generate_path_from_books(sid, [bid])
    assert svc.store.list_learning_paths(sid) == []


@pytest.mark.asyncio
async def test_crumbs_route_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_llm(monkeypatch, "garbage non json")

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__()

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        p = tmp_path / "miettes.txt"
        p.write_text("contenu miettes " * 20, encoding="utf-8")
        assert c.post("/api/tutor/import", json={"subject": "SVT", "path": str(p)}).status_code == 200
        from src.ollama_tutor.tutor.store import LibraryStore as LS

        store = LS(tmp_path / "config")
        sid = next(s["id"] for s in c.get("/api/tutor/subjects").json()["subjects"] if s["name"] == "SVT")
        bid = c.get("/api/tutor/books").json()["books"][0]["id"]
        store.add_chunks(
            sid, bid,
            [{"text": "x = 3 texte", "chapter": "x = 3", "section": ""}],
            [[0.1, 0.2]], "test-model",
        )
        r = c.post(
            f"/api/tutor/subjects/{sid}/path/generate-from-books",
            json={"book_ids": [bid]},
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_good_chapters_fallback_steps(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(
        svc, tmp_path, sid, "bio.txt",
        [
            ("La cellule : unité du vivant", ["Le noyau"]),
            ("L'ADN support de l'hérédité", []),
            ("La photosynthèse des plantes", []),
        ],
    )
    _mock_llm(monkeypatch, "garbage non json")
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is True
    assert len(result["steps"]) >= 3
    for s in result["steps"]:
        assert len(s["title"]) >= 15
        assert s["title"][:1] == s["title"][:1].upper()


@pytest.mark.asyncio
async def test_llm_duplicates_deduped(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(
        svc, tmp_path, sid, "bio.txt",
        [
            ("La cellule : unité du vivant", []),
            ("L'ADN support de l'hérédité", []),
            ("La photosynthèse des plantes", []),
        ],
    )
    steps = [
        {"title": "Comprendre la cellule vivante", "type": "concept", "duration": 15, "source": "bio"},
        {"title": "Comprendre  la  CELLULE vivante !", "type": "concept", "duration": 15, "source": "bio"},
        {"title": "Comprendre la cellule vivante.", "type": "concept", "duration": 15, "source": "bio"},
        {"title": "L'ADN support de l'hérédité", "type": "concept", "duration": 15, "source": "bio"},
    ]
    _mock_llm(monkeypatch, json.dumps(steps))
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is True  # 4 brutes → 2 uniques < 3 ⇒ repli
    assert len(result["steps"]) >= 3
