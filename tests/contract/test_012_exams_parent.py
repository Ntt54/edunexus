"""T014 Contract: POST /api/tutor/exams (blueprint + /20 + interrompue) + parent share.

012-real-learning-packs, FR-006/FR-008 (US2) :
- POST /api/tutor/exams {blueprint, duree_min} → 200 (blueprints
  BEPC/1ère/Terminale, score_20, statut interrompue du précédent) ;
  400 blueprint inconnu ou durée invalide.
- POST /api/tutor/learners/{id}/share → 200 + share_token (32+ chars) ;
  GET /api/tutor/parent/overview?token= → 200 (temps, maîtrise, erreurs,
  jalon) ; DELETE → revoke, overview → 403 ; sans token → 403 ;
  404 apprenant inconnu.

100 % offline : TestClient + store tmp partagé (même config_dir), génération
LLM en repli offline (charge utile vide), correction sans réponse → aucune
appel juge (aucun démon Ollama, transport d'embed scripté).
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


class ScriptedClient(web_server.OllamaClient):
    def __init__(self, *a, **k):
        super().__init__(transport=_make_embed_transport())


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_server, "OllamaClient", ScriptedClient)
    app = web_server.create_app(config_dir=tmp_path / "config")
    with TestClient(app) as c:
        yield c


def _create_exam(c: TestClient, blueprint: str, duree_min: int = 120, size: int = 2):
    return c.post(
        "/api/tutor/exams",
        json={"blueprint": blueprint, "duree_min": duree_min, "size": size},
    )


# ---------------------------------------------------------------------------
# POST /exams — blueprints BEPC / 1ère / Terminale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blueprint", ["cm/bepc", "cm/premiere-c", "cm/terminale-c"]
)
def test_blueprint_exam_200_with_skeleton_fields(client: TestClient, blueprint: str):
    r = _create_exam(client, blueprint)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blueprint"] == blueprint
    assert body["duree_min"] == 120
    assert body["time_limit_s"] == 120 * 60
    # Pas encore corrigé : /20 en attente, détail vide.
    assert body["score_20"] is None
    assert body["detail_competences"] == []
    assert body["statut"] == "in_progress"
    assert body["locked"] is False
    assert body["remaining_s"] > 0
    # Questions liées aux notions du pack, sans rappel rédigé.
    assert len(body["questions"]) == 2
    kinds = {q["type"] for q in body["questions"]}
    assert kinds <= {"mcq", "true_false", "matching", "open"}
    assert "recall_written" not in kinds


def test_blueprint_exam_unknown_400(client: TestClient):
    r = _create_exam(client, "cm/inconnu")
    assert r.status_code == 400


@pytest.mark.parametrize("duree_min", [0, -30])
def test_blueprint_exam_bad_duration_400(client: TestClient, duree_min: int):
    r = _create_exam(client, "cm/bepc", duree_min=duree_min)
    assert r.status_code == 400


def test_blueprint_exam_unknown_subject_404(client: TestClient):
    r = client.post(
        "/api/tutor/exams",
        json={"blueprint": "cm/bepc", "duree_min": 60, "subject_id": "nope-unknown"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /exams — correction /20 + statut interrompue
# ---------------------------------------------------------------------------


def test_exam_submit_scores_20_per_competence(client: TestClient):
    exam = _create_exam(client, "cm/bepc").json()
    # Aucune réponse → tout incorrect, sans appel juge (offline-safe).
    r = client.post(f"/api/tutor/quizzes/{exam['id']}/submit", json={"answers": {}})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/tutor/quizzes/{exam['id']}").json()
    assert body["status"] == "completed"
    report = body["report"]
    assert report["score_20"] == 0.0
    assert report["mention"] == "Insuffisant"
    assert len(report["detail_competences"]) == len(exam["questions"])
    for entry in report["detail_competences"]:
        assert entry["score_20"] == 0.0
        assert entry["competence"]


def test_second_exam_marks_previous_interrompue(client: TestClient):
    first = _create_exam(client, "cm/terminale-c").json()
    second = _create_exam(client, "cm/terminale-c").json()
    assert second["id"] != first["id"]
    # Même matière auto-résolue : la première épreuve est coupée.
    body = client.get(f"/api/tutor/quizzes/{first['id']}").json()
    assert body["status"] == "interrompue"


# ---------------------------------------------------------------------------
# Parent share — consentement, overview, révocation
# ---------------------------------------------------------------------------


def _create_learner(c: TestClient, name: str = "Awa") -> str:
    r = c.post("/api/tutor/learners", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_parent_share_grant_overview_revoke(client: TestClient):
    learner_id = _create_learner(client)
    # Grant → token 32+ chars.
    r = client.post(f"/api/tutor/learners/{learner_id}/share")
    assert r.status_code == 200, r.text
    token = r.json()["share_token"]
    assert isinstance(token, str) and len(token) >= 32
    # Overview → vue agrégée lecture seule.
    r = client.get("/api/tutor/parent/overview", params={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "temps_semaine_min" in body
    assert "maitrise" in body
    assert "erreurs_frequentes" in body
    assert "prochain_jalon" in body
    # Revoke → token mort immédiat.
    r = client.delete(f"/api/tutor/learners/{learner_id}/share")
    assert r.status_code == 200, r.text
    assert r.json() == {"revoked": True}
    r = client.get("/api/tutor/parent/overview", params={"token": token})
    assert r.status_code == 403


def test_parent_overview_no_token_403(client: TestClient):
    assert client.get("/api/tutor/parent/overview").status_code == 403
    r = client.get("/api/tutor/parent/overview", params={"token": "bidon"})
    assert r.status_code == 403


def test_parent_share_unknown_learner_404(client: TestClient):
    assert client.post("/api/tutor/learners/nope/share").status_code == 404
    assert client.delete("/api/tutor/learners/nope/share").status_code == 404
