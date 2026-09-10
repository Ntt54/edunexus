"""Durcissement generate_path_from_books (LLM local dégénéré).

100 % offline : `_llm_collect` monkeypatché (aucun réseau), TestClient
pour la route 422. Couvre :
- JSON valide ⇒ steps LLM + ``fallback: False`` ;
- garbage/texte libre ou sortie sous le minimum ⇒ repli TOC
  déterministe + ``fallback: True`` (titres repris tels quels) ;
- TOC vide ⇒ erreur explicite (aucun parcours coquille vide), route 422 ;
- DB réelle : N étapes titrées depuis les chapitres, cap ~12 ;
- validation : titres requis, duration cloisonnée 5-120, sources normalisées ;
- description/objectif : texte élève dans le prompt + persisté, omis si vide.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server
from src.ollama_tutor.config import Config
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
    """Importe un faux livre + chunks chapitre/section (retourne book_id)."""
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


def _subject(svc: SimpleNamespace) -> str:
    return svc.store.create_subject("SVT").id


def _mock_llm(monkeypatch: pytest.MonkeyPatch, payload: str):
    async def _fake(self, messages, options):
        return payload

    monkeypatch.setattr(TutorService, "_llm_collect", _fake)


@pytest.mark.asyncio
async def test_valid_llm_json_uses_llm_steps(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject(svc)
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", ["Noyau"]), ("ADN", [])])
    steps = [
        {"title": f"Leçon {i}", "type": "concept", "duration": 15, "source": "bio"}
        for i in range(1, 7)
    ]
    _mock_llm(monkeypatch, json.dumps(steps))
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is False
    assert len(result["steps"]) == 6
    assert all(s["title"].strip() for s in result["steps"])
    stored = svc.store.list_path_steps(result["id"])
    assert len(stored) == 6


@pytest.mark.asyncio
async def test_garbage_llm_falls_back_to_toc(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject(svc)
    bid = _seed_book(
        svc, tmp_path, sid, "bio.txt", [("La Cellule", ["Noyau"]), ("ADN", ["Brin"])]
    )
    _mock_llm(monkeypatch, "bla bla pas du json, texte libre dégénéré")
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is True
    titles = [s["title"] for s in result["steps"]]
    # Titres TOC repris tels quels, chapitres vides ignorés.
    assert "La Cellule" in titles
    assert "ADN" in titles
    assert all(t.strip() for t in titles)


@pytest.mark.asyncio
async def test_single_degenerate_step_falls_back(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject(svc)
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", []), ("ARN", [])])
    # Sortie dégénérée prod : 1 étape nommée d'après le livre.
    _mock_llm(
        monkeypatch,
        json.dumps([{"title": "bio", "type": "concept", "duration": 10, "source": "bio"}]),
    )
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is True
    assert len(result["steps"]) >= 3, "le repli TOC restaure un minimum viable"


@pytest.mark.asyncio
async def test_empty_toc_raises_explicit_error(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject(svc)
    bid = _seed_book(svc, tmp_path, sid, "vide.txt", [])
    _mock_llm(monkeypatch, json.dumps([{"title": "Leçon", "type": "concept"}]))
    with pytest.raises(PathGenerationError, match="insuffisants"):
        await svc.service.generate_path_from_books(sid, [bid])
    assert svc.store.list_learning_paths(sid) == [], "aucun parcours coquille vide"


@pytest.mark.asyncio
async def test_fallback_capped_and_verbatim(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject(svc)
    chapters = [(f"Chapitre {i:02d} : Les Bases & Détails", []) for i in range(15)]
    bid = _seed_book(svc, tmp_path, sid, "gros.txt", chapters)
    _mock_llm(monkeypatch, "garbage")
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is True
    assert len(result["steps"]) <= 12, "cap du repli TOC"
    assert result["steps"][0]["title"] == "Chapitre 00 : Les Bases & Détails"


def test_parse_validates_titles_durations_sources() -> None:
    raw = json.dumps(
        [
            {"title": "  Leçon A  ", "type": "concept", "duration": 500, "source": "BIO"},
            {"title": "", "type": "concept", "duration": 10, "source": "x"},
            {"title": "Leçon B", "type": "bizarre", "duration": "abc", "source": ""},
            {"title": "Leçon C", "type": "reading", "duration": -3, "source": None},
            "pas-un-dict",
        ]
    )
    steps = TutorService._parse_path_steps_response(raw)
    assert [s["title"] for s in steps] == ["Leçon A", "Leçon B", "Leçon C"]
    assert steps[0]["duration"] == 120, "clamp haut"
    assert steps[1]["duration"] == 15, "défaut sur non-numérique"
    assert steps[1]["type"] == "concept", "type inconnu normalisé"
    assert steps[2]["duration"] == 5, "clamp bas"


@pytest.mark.asyncio
async def test_source_normalized_to_known_title(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject(svc)
    bid = _seed_book(svc, tmp_path, sid, "Biologie.txt", [("Cellule", []), ("ADN", [])])
    book_title = svc.store.get_book(bid).title
    steps = [
        {"title": "L1", "type": "reading", "duration": 10, "source": "cellule"},
        {"title": "L2", "type": "reading", "duration": 10, "source": book_title.upper()},
        {"title": "L3", "type": "reading", "duration": 10, "source": "xxx inconnu"},
        {"title": "L4", "type": "reading", "duration": 10, "source": "ADN profond"},
    ]
    _mock_llm(monkeypatch, json.dumps(steps))
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["fallback"] is False  # 4 étapes ≥ minimum viable
    by_title = {s["title"]: s for s in result["steps"]}
    # activity_id porte la source normalisée vers les titres connus.
    assert by_title["L1"]["activity_id"] == "Cellule"
    assert by_title["L2"]["activity_id"] == book_title
    assert by_title["L4"]["activity_id"] == "ADN"
    # Source inconnue : conservée telle quelle (pas de destruction d'info).
    assert by_title["L3"]["activity_id"] == "xxx inconnu"


@pytest.mark.asyncio
async def test_generate_route_422_on_empty_toc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake(self, messages, options):
        return "[]"

    monkeypatch.setattr(TutorService, "_llm_collect", _fake)

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__()

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        p = tmp_path / "vide.txt"
        p.write_text("contenu sans chapitres " * 20, encoding="utf-8")
        r = c.post("/api/tutor/import", json={"subject": "SVT", "path": str(p)})
        assert r.status_code == 200, r.text
        book_id = r.json()["book_id"]
        subjects = c.get("/api/tutor/subjects").json()["subjects"]
        sid = next(s["id"] for s in subjects if s["name"] == "SVT")
        r2 = c.post(
            f"/api/tutor/subjects/{sid}/path/generate-from-books",
            json={"book_ids": [book_id]},
        )
        assert r2.status_code == 422, r2.text
        assert "insuffisants" in r2.json().get("detail", "")


# ---------------------------------------------------------------------------
# Description / objectif élève (LLM mocké qui CAPTURE le prompt)
# ---------------------------------------------------------------------------


def _capturing_llm(monkeypatch: pytest.MonkeyPatch, captured: dict, payload: str = ""):
    async def _fake(self, messages, options):
        captured["system"] = messages[0].content
        captured["user"] = messages[1].content
        return payload or json.dumps(
            [
                {"title": f"Leçon {i}", "type": "concept", "duration": 15, "source": "bio"}
                for i in range(1, 7)
            ]
        )

    monkeypatch.setattr(TutorService, "_llm_collect", _fake)


@pytest.mark.asyncio
async def test_description_in_prompt_and_persisted(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    goal = "Réviser la génétique pour l'examen de juin"
    result = await svc.service.generate_path_from_books(sid, [bid], description=goal)
    assert "Objectif de l'élève" in captured["system"]
    assert goal in captured["system"]
    assert result["fallback"] is False
    assert result["description"] == goal
    stored = svc.store.get_learning_path(result["id"])
    assert stored is not None and stored.description == goal


@pytest.mark.asyncio
async def test_no_description_unchanged_behavior(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert "Objectif de l'élève" not in captured["system"]
    assert result["description"].startswith("Parcours structuré basé sur")


@pytest.mark.asyncio
async def test_blank_description_omitted(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    for blank in ("", "   ", None):
        captured: dict = {}
        _capturing_llm(monkeypatch, captured)
        result = await svc.service.generate_path_from_books(sid, [bid], description=blank)
        assert "Objectif de l'élève" not in captured["system"]
        assert result["description"].startswith("Parcours structuré basé sur")


@pytest.mark.asyncio
async def test_description_route_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__()

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        p = tmp_path / "bio.txt"
        p.write_text("contenu cellule adn " * 20, encoding="utf-8")
        assert c.post("/api/tutor/import", json={"subject": "SVT", "path": str(p)}).status_code == 200
        # Indexer le livre pour avoir des chapitres ? Non : chunks requis.
        # On passe par le store direct pour la TOC (même DB, même config).
        from src.ollama_tutor.tutor.store import LibraryStore as LS

        store = LS(tmp_path / "config")
        sid = next(s["id"] for s in c.get("/api/tutor/subjects").json()["subjects"] if s["name"] == "SVT")
        books = c.get("/api/tutor/books").json()["books"]
        bid = books[0]["id"]
        store.add_chunks(
            sid, bid,
            [{"text": "Cellule texte", "chapter": "Cellule", "section": ""}],
            [[0.1, 0.2]], "test-model",
        )
        goal = "Objectif route : maîtriser la cellule"
        r = c.post(
            f"/api/tutor/subjects/{sid}/path/generate-from-books",
            json={"book_ids": [bid], "description": goal},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["description"] == goal
        assert "Objectif de l'élève" in captured["system"]
        assert goal in captured["system"]


# ---------------------------------------------------------------------------
# Remplissage d'un parcours existant (path_id optionnel, additif)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_empty_existing_path(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    manual = svc.store.create_learning_path(sid, "python", "Mon parcours manuel")
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    result = await svc.service.generate_path_from_books(sid, [bid], path_id=manual.id)
    assert result["filled"] is True
    assert result["id"] == manual.id
    # Titre/description manuels CONSERVÉS, jamais écrasés.
    assert result["title"] == "python"
    assert result["description"] == "Mon parcours manuel"
    assert len(result["steps"]) == 6
    assert result["fallback"] is False


@pytest.mark.asyncio
async def test_nonempty_path_creates_new_path_leaves_old_intact(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    manual = svc.store.create_learning_path(sid, "python", "Mon parcours manuel")
    svc.store.add_path_step(manual.id, "concept", "c0", title="Étape manuelle", ordinal=0)
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    result = await svc.service.generate_path_from_books(sid, [bid], path_id=manual.id)
    assert result["filled"] is False
    assert result["id"] != manual.id
    # Ancien parcours intact : 1 étape, titre inchangé (jamais de destruction).
    old_steps = svc.store.list_path_steps(manual.id)
    assert len(old_steps) == 1
    assert old_steps[0].title == "Étape manuelle"
    assert svc.store.get_learning_path(manual.id).title == "python"
    assert len(result["steps"]) == 6


@pytest.mark.asyncio
async def test_unknown_path_id_raises_keyerror(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    with pytest.raises(KeyError):
        await svc.service.generate_path_from_books(sid, [bid], path_id="inexistant")


@pytest.mark.asyncio
async def test_path_from_other_subject_raises_keyerror(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    other = svc.store.create_subject("Maths").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    foreign = svc.store.create_learning_path(other, "ailleurs", "")
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    with pytest.raises(KeyError):
        await svc.service.generate_path_from_books(sid, [bid], path_id=foreign.id)


@pytest.mark.asyncio
async def test_absent_path_id_creates_as_before(
    svc: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = svc.store.create_subject("SVT").id
    bid = _seed_book(svc, tmp_path, sid, "bio.txt", [("Cellule", []), ("ADN", [])])
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)
    result = await svc.service.generate_path_from_books(sid, [bid])
    assert result["filled"] is False
    assert result["title"].startswith("Parcours depuis livres")


@pytest.mark.asyncio
async def test_fill_route_200_and_unknown_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _capturing_llm(monkeypatch, captured)

    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__()

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        p = tmp_path / "bio.txt"
        p.write_text("contenu cellule adn " * 20, encoding="utf-8")
        assert c.post("/api/tutor/import", json={"subject": "SVT", "path": str(p)}).status_code == 200
        from src.ollama_tutor.tutor.store import LibraryStore as LS

        store = LS(tmp_path / "config")
        sid = next(s["id"] for s in c.get("/api/tutor/subjects").json()["subjects"] if s["name"] == "SVT")
        bid = c.get("/api/tutor/books").json()["books"][0]["id"]
        store.add_chunks(
            sid, bid,
            [{"text": "Cellule texte", "chapter": "Cellule", "section": ""}],
            [[0.1, 0.2]], "test-model",
        )
        manual = store.create_learning_path(sid, "python", "manuel")
        r = c.post(
            f"/api/tutor/subjects/{sid}/path/generate-from-books",
            json={"book_ids": [bid], "path_id": manual.id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == manual.id
        assert body["title"] == "python"
        assert body["filled"] is True
        r2 = c.post(
            f"/api/tutor/subjects/{sid}/path/generate-from-books",
            json={"book_ids": [bid], "path_id": "inexistant"},
        )
        assert r2.status_code == 404, r2.text
