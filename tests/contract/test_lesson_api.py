"""Contract tests for lesson discussion endpoints (Feature 009, US1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.ollama_tutor.tutor.store import LibraryStore
from src.ollama_tutor.web.server import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(config_dir=tmp_path / "config"))


def _seed_path_step(tmp_path):
    store = LibraryStore(tmp_path / "config")
    subj = store.create_subject("Maths")
    path = store.create_learning_path(subj.id, "Parcours")
    step = store.add_path_step(path.id, "concept", "notion-vars", "Variables", ordinal=0)
    step2 = store.add_path_step(path.id, "concept", "notion-loops", "Boucles", ordinal=1)
    return store, subj, path, step, step2


def test_post_discussion_creates_and_returns(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        r = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200, r.text
        disc = r.json()["discussion"]
        assert disc["path_step_id"] == step.id
        assert disc["learner_id"] == "alice"
        assert "id" in disc


def test_post_discussion_idempotent(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        r1 = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"})
        r2 = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"})
        assert r1.json()["discussion"]["id"] == r2.json()["discussion"]["id"]


def test_post_discussion_transitions_status(tmp_path):
    store, _, _, step, _ = _seed_path_step(tmp_path)
    assert store.get_path_step(step.id).status == "not_started"
    with _client(tmp_path) as c:
        c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"})
    # reload store
    store2 = LibraryStore(tmp_path / "config")
    assert store2.get_path_step(step.id).status == "in_progress"


def test_post_discussion_requires_learner(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        r = c.post(f"/api/tutor/path-steps/{step.id}/discussion")
        assert r.status_code == 400


def test_post_discussion_unknown_step_404(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/tutor/path-steps/unknown-id/discussion", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 404


def test_post_discussion_learner_via_query_param(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        r = c.post(f"/api/tutor/path-steps/{step.id}/discussion?learner_id=bob")
        assert r.status_code == 200
        assert r.json()["discussion"]["learner_id"] == "bob"


def test_get_discussion_returns_messages_and_contents(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        r = c.get(f"/api/tutor/lesson-discussions/{disc_id}")
        assert r.status_code == 200
        data = r.json()
        assert "discussion" in data
        assert "messages" in data
        assert "generated_contents" in data
        assert "exercise_attempts" in data
        assert data["discussion"]["id"] == disc_id


def test_get_discussion_unknown_404(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/tutor/lesson-discussions/unknown")
        assert r.status_code == 404


# ------------------------------------------------------------------
# US2 — generate-course / generate-summary (T013)
# ------------------------------------------------------------------

def test_generate_course_returns_content_with_kind_and_sources(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/generate-course", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200, r.text
        content = r.json()["content"]
        assert content["kind"] == "lesson_course"
        assert content["content"]
        # 800-1200 words
        wc = len(content["content"].split())
        assert 800 <= wc <= 1200, f"wc {wc}"
        assert "sources" in content
        assert "confidence" in content


def test_generate_course_idempotent_second_call_creates_new(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        r1 = c.post(f"/api/tutor/lesson-discussions/{disc_id}/generate-course", headers={"X-Learner-Id": "alice"})
        r2 = c.post(f"/api/tutor/lesson-discussions/{disc_id}/generate-course", headers={"X-Learner-Id": "alice"})
        assert r1.json()["content"]["id"] != r2.json()["content"]["id"]
        # both persisted
        data = c.get(f"/api/tutor/lesson-discussions/{disc_id}").json()
        assert len([x for x in data["generated_contents"] if x["kind"] == "lesson_course"]) == 2


def test_generate_summary_without_course_still_works(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/generate-summary", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200, r.text
        content = r.json()["content"]
        assert content["kind"] == "lesson_summary"
        wc = len(content["content"].split())
        assert 150 <= wc <= 250, f"wc {wc}"
        assert "sources" in content


def test_generate_summary_after_course_uses_course_path(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        c.post(f"/api/tutor/lesson-discussions/{disc_id}/generate-course", headers={"X-Learner-Id": "alice"})
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/generate-summary", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200
        summary = r.json()["content"]
        assert summary["kind"] == "lesson_summary"
        wc = len(summary["content"].split())
        assert 150 <= wc <= 250
        data = c.get(f"/api/tutor/lesson-discussions/{disc_id}").json()
        assert any(x["kind"] == "lesson_course" for x in data["generated_contents"])
        assert any(x["kind"] == "lesson_summary" for x in data["generated_contents"])


# ------------------------------------------------------------------
# US3 — exercices, scoring et clôture (T018) — FR-006/007/008
# ------------------------------------------------------------------

def test_generate_exercises_returns_3_to_5_questions(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200, r.text
        attempt = r.json()["attempt"]
        assert 3 <= len(attempt["questions"]) <= 5
        for q in attempt["questions"]:
            assert "id" in q and "statement" in q and "type" in q
        assert attempt["score"] == 0.0
        assert attempt["passed"] is False


def test_submit_exercises_passed_auto_completes(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        attempt = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises", headers={"X-Learner-Id": "alice"}).json()["attempt"]
        answers = {q["id"]: q["answer"] for q in attempt["questions"]}
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises/{attempt['id']}/submit", json={"answers": answers}, headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200, r.text
        updated = r.json()["attempt"]
        assert updated["passed"] is True
        assert updated["score"] >= 0.6
        assert "per_question" in updated
        # status should be completed
        from src.ollama_tutor.tutor.store import LibraryStore
        s = LibraryStore(tmp_path / "config")
        assert s.get_path_step(step.id).status == "completed"


def test_submit_exercises_fail_stays_in_progress_and_manual_complete(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        attempt = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises", headers={"X-Learner-Id": "alice"}).json()["attempt"]
        answers = {q["id"]: "mauvaise" for q in attempt["questions"]}
        # keep one correct so that fail is <60% not 0/ maybe edge 3 q => 1/3=33% <60 ok
        if attempt["questions"]:
            answers[attempt["questions"][0]["id"]] = attempt["questions"][0]["answer"]
            if len(attempt["questions"]) == 3:
                # 1/3 still <60 so fine; keep as is
                pass
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises/{attempt['id']}/submit", json=answers, headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200, r.text
        assert r.json()["attempt"]["passed"] is False
        assert r.json()["attempt"]["score"] < 0.6
        # manual completion via POST .../complete (and alias .../complete-manual)
        rc = c.post(f"/api/tutor/lesson-discussions/{disc_id}/complete", headers={"X-Learner-Id": "alice"})
        assert rc.status_code == 200
        assert rc.json()["status"] == "completed"
        # alias
        rc2 = c.post(f"/api/tutor/lesson-discussions/{disc_id}/complete-manual", headers={"X-Learner-Id": "alice"})
        assert rc2.status_code == 200


def test_complete_manual_without_passing(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        r = c.post(f"/api/tutor/lesson-discussions/{disc_id}/complete-manual", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


def test_regeneration_creates_new_questions_and_unknown_returns_404(tmp_path):
    _, _, _, step, _ = _seed_path_step(tmp_path)
    with _client(tmp_path) as c:
        disc_id = c.post(f"/api/tutor/path-steps/{step.id}/discussion", headers={"X-Learner-Id": "alice"}).json()["discussion"]["id"]
        a1 = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises", headers={"X-Learner-Id": "alice"}).json()["attempt"]
        a2 = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises", headers={"X-Learner-Id": "alice"}).json()["attempt"]
        assert a1["id"] != a2["id"]
        ids1 = {q["id"] for q in a1["questions"]}
        ids2 = {q["id"] for q in a2["questions"]}
        assert ids1.isdisjoint(ids2)
        # unknown discussion
        r = c.post("/api/tutor/lesson-discussions/unknown-id/exercises", headers={"X-Learner-Id": "alice"})
        assert r.status_code == 404
        # unknown attempt
        r2 = c.post(f"/api/tutor/lesson-discussions/{disc_id}/exercises/unknown-attempt/submit", json={}, headers={"X-Learner-Id": "alice"})
        assert r2.status_code == 404
