"""T020 Contract: notes atomiques CRUD/links/plan + POST /planner/semester.

012-real-learning-packs, FR-010/FR-011 (US3, niveau universitaire) :
- POST /api/tutor/notes/atomic {title, body, ...} → 201 + id ;
  400 titre vide / corps vide (en propres mots requis).
- GET /api/tutor/notes/atomic → 200 {"notes": [...]}.
- POST /api/tutor/notes/atomic/{id}/links {to_id, rel} → 200 ;
  400 rel invalide (précise/contredit/mécanisme-de/exemple-de uniquement) ;
  404 note inconnue.
- GET /api/tutor/notes/atomic/plan?question= → 200 plan assemblé depuis
  les liens (lecture seule) ; 400 question vide.
- POST /api/tutor/planner/semester {ues, epreuves} → 200 créneaux hebdo,
  aucune semaine >150 % de la moyenne ; 400 si impossible sans dépassement.

100 % offline : TestClient + store tmp partagé (même config_dir), aucun
démon Ollama (transport d'embed scripté).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.ollama_tutor.web.server as web_server


def _make_embed_transport(dim: int = 4):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        inputs = body.get("input", [])
        vecs = [[float((i * 3 + j) % 5) / 5 for j in range(dim)] for i in range(len(inputs))]
        return httpx.Response(200, json={"embeddings": vecs}, request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    class ScriptedClient(web_server.OllamaClient):
        def __init__(self, *a, **k):
            super().__init__(transport=_make_embed_transport())

    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _create_note(c: TestClient, title: str = "La photosynthèse produit du glucose",
                 body: str = "les plantes fabriquent leur sucre avec la lumière") -> dict:
    r = c.post("/api/tutor/notes/atomic", json={"title": title, "body": body})
    assert r.status_code == 201, r.text
    return r.json()["note"]


# ---------------------------------------------------------------------------
# Notes atomiques — CRUD
# ---------------------------------------------------------------------------


def test_atomic_note_create_201_and_list(client: TestClient):
    note = _create_note(client)
    assert note["id"]
    assert note["title"] == "La photosynthèse produit du glucose"
    assert note["body"] == "les plantes fabriquent leur sucre avec la lumière"
    r = client.get("/api/tutor/notes/atomic")
    assert r.status_code == 200, r.text
    notes = r.json()["notes"]
    assert any(n["id"] == note["id"] for n in notes)


def test_atomic_note_empty_title_400(client: TestClient):
    r = client.post("/api/tutor/notes/atomic", json={"title": "   ", "body": "du contenu"})
    assert r.status_code == 400


def test_atomic_note_empty_body_400(client: TestClient):
    r = client.post("/api/tutor/notes/atomic", json={"title": "Une affirmation", "body": "  "})
    assert r.status_code == 400


def test_atomic_note_title_too_long_400(client: TestClient):
    r = client.post("/api/tutor/notes/atomic", json={"title": "x" * 121, "body": "du contenu"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Liens entre notes
# ---------------------------------------------------------------------------


def test_atomic_link_valid_rel_200(client: TestClient):
    a = _create_note(client, "La mitose sépare les chromatides", "la cellule divise ses chromosomes en deux")
    b = _create_note(client, "Les chromatides sont des copies", "chaque chromosome copié donne deux bras identiques")
    r = client.post(f"/api/tutor/notes/atomic/{a['id']}/links", json={"to_id": b["id"], "rel": "précise"})
    assert r.status_code == 200, r.text
    assert r.json()["link"]["rel"] == "précise"


@pytest.mark.parametrize("rel", ["précise", "contredit", "mécanisme-de", "exemple-de"])
def test_atomic_link_all_valid_rels(client: TestClient, rel: str):
    a = _create_note(client, f"Affirmation A {rel}", "corps en propres mots numéro un")
    b = _create_note(client, f"Affirmation B {rel}", "corps en propres mots numéro deux")
    r = client.post(f"/api/tutor/notes/atomic/{a['id']}/links", json={"to_id": b["id"], "rel": rel})
    assert r.status_code == 200, r.text


def test_atomic_link_invalid_rel_400(client: TestClient):
    a = _create_note(client)
    b = _create_note(client, "Autre affirmation", "un autre corps en propres mots")
    r = client.post(f"/api/tutor/notes/atomic/{a['id']}/links", json={"to_id": b["id"], "rel": "ressemble-à"})
    assert r.status_code == 400


def test_atomic_link_unknown_note_404(client: TestClient):
    b = _create_note(client)
    r = client.post("/api/tutor/notes/atomic/nope-unknown/links", json={"to_id": b["id"], "rel": "précise"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Assemblage de plan depuis les liens (lecture seule)
# ---------------------------------------------------------------------------


def test_atomic_plan_assembled_from_links(client: TestClient):
    seed = _create_note(client, "La photosynthèse produit du glucose",
                        "les plantes fabriquent leur sucre avec la lumière")
    n2 = _create_note(client, "La chlorophylle capte la lumière", "le pigment vert absorbe les photons")
    n3 = _create_note(client, "Le CO2 entre par les stomates", "les pores des feuilles laissent passer le gaz")
    n4 = _create_note(client, "L'eau monte par la sève brute", "les racines pompent et la tige conduit")
    n5 = _create_note(client, "Le glucose stocké donne de l'amidon", "la plante met son sucre en réserve")
    for target, rel in ((n2["id"], "mécanisme-de"), (n3["id"], "précise"),
                        (n4["id"], "précise"), (n5["id"], "exemple-de")):
        r = client.post(f"/api/tutor/notes/atomic/{seed['id']}/links",
                        json={"to_id": target, "rel": rel})
        assert r.status_code == 200, r.text
    before = len(client.get("/api/tutor/notes/atomic").json()["notes"])
    r = client.get("/api/tutor/notes/atomic/plan", params={"question": "Comment la photosynthèse produit du glucose ?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["question"]
    assert len(body["sections"]) >= 3
    titles = [s["title"] for s in body["sections"]]
    assert seed["title"] in titles
    # Lecture seule : aucun effet de bord.
    after = len(client.get("/api/tutor/notes/atomic").json()["notes"])
    assert after == before


def test_atomic_plan_empty_question_400(client: TestClient):
    r = client.get("/api/tutor/notes/atomic/plan", params={"question": "   "})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Planning semestre ECTS — règle 150 %
# ---------------------------------------------------------------------------


def _semester_payload(n_ues: int = 5, heures: float = 120.0) -> dict:
    return {
        "ues": [{"subject_id": f"ue-{i}", "heures": heures} for i in range(n_ues)],
        "epreuves": [],
    }


def test_planner_semester_200_no_week_over_150pct(client: TestClient):
    r = client.post("/api/tutor/planner/semester", json=_semester_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    weeks = body["semaines"]
    assert len(weeks) >= 1
    totals = [w["minutes_total"] for w in weeks]
    mean = sum(totals) / len(totals)
    assert mean > 0
    for total in totals:
        assert total <= 1.5 * mean, f"semaine à {total} min > 150 % de la moyenne {mean}"
    # Créneaux hebdo révision/simulation présents.
    kinds = {s["kind"] for w in weeks for s in w["creneaux"]}
    assert kinds <= {"révision", "simulation", "cours"}
    assert any(k in kinds for k in ("révision", "simulation"))


def test_planner_semester_empty_ues_400(client: TestClient):
    r = client.post("/api/tutor/planner/semester", json={"ues": [], "epreuves": []})
    assert r.status_code == 400


def test_planner_semester_zero_hours_400(client: TestClient):
    r = client.post("/api/tutor/planner/semester",
                    json={"ues": [{"subject_id": "ue-0", "heures": 0}], "epreuves": []})
    assert r.status_code == 400


def test_planner_semester_overload_400(client: TestClient):
    # Plafond hebdo volontairement impossible : même répartie, la charge
    # moyenne dépasse le plafond → 400 (aucune semaine >150 % possible).
    payload = _semester_payload()
    payload["max_heures_semaine"] = 1.0
    r = client.post("/api/tutor/planner/semester", json=payload)
    assert r.status_code == 400


def test_recompact_overloaded_week_reports_unplaced_minutes():
    # Semaine surchargée : le dû non replacé sous plafond est comptabilisé
    # (placé + non-replanifié == dû), sans surcharge des semaines restantes.
    from src.ollama_tutor.tutor.planner import plan_semester, recompact_plan

    plan = plan_semester([{"subject_id": "ue-0", "heures": 6}], [], weeks=2)
    before_remaining = sum(w["minutes_total"] for w in plan["semaines"] if w["semaine"] != 1)
    due = sum(c["minutes"] for w in plan["semaines"] if w["semaine"] == 1 for c in w["creneaux"])
    out = recompact_plan(plan, [1])
    assert out["recompacte"] is True
    unplaced = out["minutes_non_replanifiees"]
    placed = sum(w["minutes_total"] for w in out["semaines"] if w["semaine"] != 1) - before_remaining
    assert unplaced.get("ue-0", 0) > 0 and placed + out["minutes_non_replanifiees_total"] == due
