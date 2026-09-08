"""US5 P2-Adaptatif, tranche A (T029) — FSRS réel + wiring review.

Adapté de ``autreprojet/OpenTutor-main``
(``services/spaced_repetition/fsrs.py`` : DEFAULT_W 21 params, FSRSCard,
stabilité/difficulté initiales, ``review_card``, ``retrievability``) vers
``tutor/fsrs.py`` stdlib (math/dataclasses/datetime) + ``tutor/review.py``
(chemin FSRS ajouté, chemins SM-2/legacy intacts).

TDD tranche A : stabilité initiale, difficulté, prochaine révision sur
vecteurs connus. 100 % offline, aucun réseau/LLM.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ollama_tutor.tutor.assessment import (
    ERROR_CATEGORIES,
    derive_clean_question,
    diagnose_error,
    parse_error_diagnosis,
)
from src.ollama_tutor.tutor.blocks import decide_blocks
from src.ollama_tutor.tutor.classifier import (
    classify_by_content_heuristics,
    classify_by_filename,
    classify_document,
    detect_mime_type,
)
from src.ollama_tutor.tutor.fsrs import (
    DEFAULT_W,
    FSRSCard,
    estimate_forgetting_cost,
    retrievability,
    review_card,
)
from src.ollama_tutor.tutor.memory import (
    classify_memory_type,
    consolidate_memories,
    cosine_similarity,
    encode_memory,
    format_resumption_prompt,
    teaching_state,
)
from src.ollama_tutor.tutor.review import ReviewScheduler
from src.ollama_tutor.tutor.store import LibraryStore


# ---------------------------------------------------------------------------
# T030 — FSRS (vecteurs connus)
# ---------------------------------------------------------------------------


def test_fsrs_default_weights_have_21_params() -> None:
    assert len(DEFAULT_W) == 21
    assert DEFAULT_W[2] == pytest.approx(2.4)  # stabilité initiale Good
    assert DEFAULT_W[4] == pytest.approx(4.93)
    assert DEFAULT_W[20] == pytest.approx(1.0)  # decay FSRS-4.5 compatible


def test_fsrs_initial_stability_good() -> None:
    card, log = review_card(FSRSCard(), rating=3)
    assert card.stability == pytest.approx(2.4)
    assert card.reps == 1
    assert log.rating == 3


def test_fsrs_initial_difficulty_follows_exponential() -> None:
    card, _ = review_card(FSRSCard(), rating=3)
    expected = min(max(DEFAULT_W[4] - math.exp(DEFAULT_W[5] * 2) + 1, 1.0), 10.0)
    assert card.difficulty == pytest.approx(expected)
    assert card.difficulty == pytest.approx(1.0)  # Good ⇒ facile (clamp bas)


def test_fsrs_initial_difficulty_again() -> None:
    card, _ = review_card(FSRSCard(), rating=1)
    expected = min(max(DEFAULT_W[4] - math.exp(DEFAULT_W[5] * 0) + 1, 1.0), 10.0)
    assert card.difficulty == pytest.approx(expected)
    assert card.difficulty == pytest.approx(4.93)
    assert card.state == "learning"


def test_fsrs_first_good_review_due_matches_stability() -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    card, log = review_card(FSRSCard(), rating=3, now=now)
    assert card.state == "review"
    assert log.scheduled_days == 2  # round(2.4)
    assert card.due == now + timedelta(days=2)


def test_fsrs_again_is_lapse_with_next_day_due() -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    card = FSRSCard(difficulty=5.0, stability=2.4, reps=2, lapses=0,
                    last_review=now - timedelta(days=3), state="review")
    updated, log = review_card(card, rating=1, now=now)
    assert updated.lapses == 1
    assert updated.state == "relearning"
    assert log.scheduled_days == 1
    assert updated.due == now + timedelta(days=1)


def test_fsrs_retrievability_at_stability_is_90_percent() -> None:
    assert retrievability(0.0, 2.4) == pytest.approx(1.0)
    assert retrievability(2.4, 2.4) == pytest.approx(0.9)  # définition S
    assert retrievability(10.0, 0.0) == 0.0


def test_fsrs_forgetting_cost_sums_overdue() -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    overdue = FSRSCard(
        difficulty=5.0, stability=2.4, reps=2,
        last_review=now - timedelta(days=32),
        due=now - timedelta(days=30), state="review",
    )
    fresh = FSRSCard(
        difficulty=5.0, stability=2.4, reps=2,
        last_review=now, due=now + timedelta(days=2), state="review",
    )
    cost = estimate_forgetting_cost([overdue, fresh], now=now)
    assert cost > 0.0
    assert estimate_forgetting_cost([fresh], now=now) == 0.0
    assert estimate_forgetting_cost([], now=now) == 0.0


# ---------------------------------------------------------------------------
# T031 — wiring FSRS dans review.py (legacy conservé)
# ---------------------------------------------------------------------------


def _seed_card(
    store: LibraryStore, fid: str = "f1", subject_id: str | None = None
) -> tuple[str, str]:
    if subject_id is None:
        subject = store.create_subject("SVT")
        subject_id = subject.id
    concept = store.create_concept(subject_id, f"Concept-{fid}")
    store._conn.execute(
        "INSERT INTO flashcards (id, subject_id, concept_id, level, question,"
        " answer, source_hash, created_at) VALUES (?, ?, ?, 'beginner', 'q',"
        " 'a', ?, 'now')",
        (fid, subject_id, concept.id, f"h-{fid}"),
    )
    store._conn.commit()
    return subject_id, fid


def test_review_fsrs_grade_persists_state(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject_id, fid = _seed_card(store)
    _seed_card(store, fid="f2", subject_id=subject_id)
    sched = ReviewScheduler(store)
    sched.seed_schedule("f2")  # due aujourd'hui, chemin legacy intact
    out = sched.grade_review_fsrs(fid, rating=3)
    assert out["stability"] == pytest.approx(2.4)
    assert out["next_due"] == (date.today() + timedelta(days=2)).isoformat()
    row = sched.get_review(fid)
    assert row is not None and float(row["stability"]) == pytest.approx(2.4)
    # Le listing legacy tourne sur les lignes FSRS : la carte tout juste
    # planifiée (+2 j) n'est pas due, la carte seedée (due aujourd'hui) oui.
    due_ids = {c.id for c in sched.due_reviews(subject_id)}
    assert fid not in due_ids and "f2" in due_ids


def test_review_fsrs_rejects_bad_rating(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    _subject_id, fid = _seed_card(store)
    sched = ReviewScheduler(store)
    with pytest.raises(ValueError):
        sched.grade_review_fsrs(fid, rating=0)
    with pytest.raises(ValueError):
        sched.grade_review_fsrs(fid, rating=5)


def test_review_legacy_ladder_unchanged(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    _subject_id, fid = _seed_card(store)
    sched = ReviewScheduler(store)
    out = sched.grade_review(fid, success=True)
    assert out["streak_index"] == 1
    assert out["next_due"] == (date.today() + timedelta(days=2)).isoformat()


def test_review_forgetting_cost_counts_overdue(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject_id, fid = _seed_card(store)
    sched = ReviewScheduler(store)
    sched.grade_review_fsrs(fid, rating=3)
    # Carte tout juste planifiée : rien d'oubliable.
    assert sched.forgetting_cost(subject_id) == 0.0
    # Carte échue depuis 30 j avec S=2.4 : coût > 0.
    store._conn.execute(
        "UPDATE review_schedule SET fsrs_due = ?, stability = 2.4 WHERE flashcard_id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), fid),
    )
    store._conn.commit()
    assert sched.forgetting_cost(subject_id) > 0.0


# ---------------------------------------------------------------------------
# T032 — classification 0-LLM (nom + contenu + MIME)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("CM1_lecture3_slides.pptx", "lecture_slides"),
        ("chapitre2_maths.pdf", "textbook"),
        ("devoir_maison_3.pdf", "assignment"),
        ("examen_final_maths.pdf", "exam_schedule"),
        ("syllabus_S1.pdf", "syllabus"),
        ("fiches_revision_bio.txt", "notes"),
        ("cours_photosynthese.md", "textbook"),
        ("doc123.bin", None),
    ],
)
def test_classify_by_filename(filename: str, expected: str | None) -> None:
    assert classify_by_filename(filename) == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Submit by Friday, deadline strict. Due date: 12/09.", "assignment"),
        ("Date limite de rendu : vendredi, à rendre avant midi.", "assignment"),
        ("Grading policy and office hours for this course.", "syllabus"),
        ("Slide 12 — next slide covers mitosis.", "lecture_slides"),
        ("Theorem 3 and definition 4 of chapter 2.", "textbook"),
        ("Le théorème 3 et la définition 4 du chapitre 2.", "textbook"),
        ("Rien de spécial ici, juste du texte courant.", None),
    ],
)
def test_classify_by_content_heuristics(content: str, expected: str | None) -> None:
    assert classify_by_content_heuristics(content) == expected


def test_detect_mime_type_extension_and_magic() -> None:
    assert detect_mime_type("cours.pdf") == "application/pdf"
    assert detect_mime_type("notes.txt") == "text/plain"
    assert detect_mime_type("doc123.bin") == "application/octet-stream"
    assert detect_mime_type("scan", b"%PDF-1.4 fake") == "application/pdf"
    assert detect_mime_type("img", b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert detect_mime_type("arc", b"PK\x03\x04....") == "application/zip"


def test_classify_document_returns_method() -> None:
    category, method = classify_document("devoir_maison_3.pdf", "Bonjour.")
    assert (category, method) == ("assignment", "filename_regex")
    category, method = classify_document("doc.txt", "Submit by Friday, deadline!")
    assert (category, method) == ("assignment", "content_heuristics")
    category, method = classify_document("doc.txt", "Texte courant sans indice.")
    assert (category, method) == ("other", "default")


# ---------------------------------------------------------------------------
# T033 — diagnostic 5 catégories + derive-clean
# ---------------------------------------------------------------------------


def test_error_categories_are_five() -> None:
    assert set(ERROR_CATEGORIES) == {
        "conceptual", "procedural", "computational", "reading", "careless",
    }


def test_parse_error_diagnosis_validates() -> None:
    out = parse_error_diagnosis(
        '{"category": "computational", "confidence": 0.8,'
        ' "evidence": "9x7=63 pas 62", "related_concept": "multiplication"}'
    )
    assert out == {
        "category": "computational",
        "confidence": 0.8,
        "evidence": "9x7=63 pas 62",
        "related_concept": "multiplication",
    }


def test_parse_error_diagnosis_fallbacks() -> None:
    out = parse_error_diagnosis("pas du json")
    assert out["category"] == "conceptual" and out["confidence"] == 0.3
    out = parse_error_diagnosis('{"category": "nope", "confidence": 9}')
    assert out["category"] == "conceptual" and out["confidence"] == 1.0
    out = parse_error_diagnosis('{"category": "careless", "confidence": -2}')
    assert out["confidence"] == 0.0


def test_diagnose_error_offline_heuristics() -> None:
    assert diagnose_error("2+2=?", "4", "4 ")["category"] == "careless"
    assert diagnose_error("Q?", "12", "")["category"] == "reading"
    assert diagnose_error("7×8=?", "56", "65")["category"] == "careless"
    assert diagnose_error("7×8=?", "56", "54")["category"] == "computational"
    out = diagnose_error("Qu'est-ce que la photosynthèse ?", "plantes", "voitures")
    assert out["category"] == "conceptual"
    assert 0.0 <= out["confidence"] <= 1.0


def test_derive_clean_question_offline() -> None:
    derived = derive_clean_question(
        "Calcule 12×13−7 en détaillant chaque étape du raisonnement.",
        "149", "42", "multiplication",
    )
    assert derived["core_concept_preserved"] == "multiplication"
    assert derived["question"] and derived["explanation"]
    assert "12" in derived["question"] or "13" in derived["question"]


def test_quiz_submit_diagnose_records_wrong_answer(tmp_path: Path) -> None:
    import asyncio

    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.assessment import QuizEngine
    from src.ollama_tutor.tutor.models import Quiz, QuizQuestion

    store = LibraryStore(tmp_path)
    subject = store.create_subject("Maths")
    concept = store.create_concept(subject.id, "Tables")
    engine = QuizEngine(store, None, Config(config_dir=tmp_path))
    quiz = Quiz(id="qz1", subject_id=subject.id, kind="quiz")
    engine._insert_quiz(quiz)
    engine._insert_question(QuizQuestion(
        id="qq1", quiz_id="qz1", type="mcq", concept_id=concept.id,
        payload={"question": "7×8=?", "choices": ["54", "56"]},
        answer={"index": 1}, points=1.0,
    ))
    report = asyncio.run(engine.submit_answers("qz1", {"qq1": 0}, diagnose=True))
    assert report.score == 0.0
    rows = store._conn.execute("SELECT * FROM error_history").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["error_category"] in ERROR_CATEGORIES
    assert row["diagnosis"] in (
        "fundamental_gap", "trap_vulnerability", "carelessness", "mastered",
    )
    import json as _json

    assert _json.loads(row["knowledge_points"]) == ["Tables"]


# ---------------------------------------------------------------------------
# T034 — store : colonnes WrongAnswer (migration idempotente)
# ---------------------------------------------------------------------------


def test_error_history_p2_columns_idempotent(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    store._migrate_p2_adaptive()
    store._migrate_p2_adaptive()  # 2e application : sans erreur
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(error_history)")}
    assert {"error_category", "knowledge_points", "diagnosis"} <= cols


def test_record_error_rejects_bad_diagnosis(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("Maths")
    with pytest.raises(ValueError):
        store.record_error(
            subject_id=subject.id, concept_name="Tables", question_text="q",
            given_answer="u", correct_answer="c", diagnosis="nope",
        )


# ---------------------------------------------------------------------------
# T035 — mémoire compacte (sync SQLite, 100 % offline)
# ---------------------------------------------------------------------------


def test_cosine_similarity_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], []) == 0.0


def test_classify_memory_type_rules() -> None:
    assert classify_memory_type("I prefer visual explanations, I am a slow learner") == "profile"
    assert classify_memory_type("Je préfère les exemples visuels, je suis débutant") == "profile"
    assert classify_memory_type("My exam is next week, deadline Friday") == "plan"
    assert classify_memory_type("Mon examen est demain, date limite vendredi") == "plan"
    assert classify_memory_type("Explain how mitosis works in cells") == "knowledge"


def test_encode_memory_skips_trivial_and_stores(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("SVT")
    assert encode_memory(store, subject.id, "ok") is None
    assert encode_memory(store, subject.id, "   ") is None
    mem = encode_memory(
        store, subject.id,
        "My final exam is next week, I must revise mitosis chapters",
        "Plan a revision schedule over 5 days",
    )
    assert mem is not None and mem["memory_type"] == "plan"
    assert mem["importance"] == pytest.approx(0.5)
    assert mem["summary"]


def test_consolidate_dedups_overlap_and_respects_type(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("SVT")
    encode_memory(store, subject.id, "Student revises the stages of mitosis for the final exam tonight")
    encode_memory(store, subject.id, "Student revises the stages of mitosis for the final exam tomorrow")
    encode_memory(store, subject.id, "I prefer short visual lessons in the evening")
    out = consolidate_memories(store, subject.id)
    assert out["deduped"] == 1
    remaining = store._conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_memories"
    ).fetchone()["c"]
    assert remaining == 2  # les 2 types survivent (knowledge + profile)


def test_consolidate_applies_90d_decay(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("SVT")
    mem = encode_memory(store, subject.id, "Student asked about the water cycle processes today")
    assert mem is not None
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    store._conn.execute(
        "UPDATE conversation_memories SET created_at = ? WHERE id = ?", (old, mem["id"])
    )
    store._conn.commit()
    out = consolidate_memories(store, subject.id)
    assert out["decayed"] == 1
    row = store._conn.execute(
        "SELECT importance FROM conversation_memories WHERE id = ?", (mem["id"],)
    ).fetchone()
    assert float(row["importance"]) == pytest.approx(0.25)  # 0.5 × 0.5 (90 j)


def test_teaching_state_and_resumption(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)
    subject = store.create_subject("SVT")
    concept = store.create_concept(subject.id, "Mitose")
    store.record_progress(concept.id, 80)
    weak = store.create_concept(subject.id, "Méiose")
    store.record_progress(weak.id, 10)
    state = teaching_state(store, subject.id)
    assert state["total_concepts"] == 2
    assert "Mitose" in state["strengths"] and "Méiose" in state["weaknesses"]
    assert state["next_topic"] == "Méiose"
    prompt = format_resumption_prompt(state)
    assert "Méiose" in prompt and "Mitose" in prompt


# ---------------------------------------------------------------------------
# T036 — blocs adaptatifs (4 règles, MAX 2 ops)
# ---------------------------------------------------------------------------


def test_blocks_deadline_forgetting_weak_prereq() -> None:
    ops = decide_blocks(
        [
            {"signal_type": "deadline", "urgency": 85,
             "detail": {"title": "DM maths"}},
            {"signal_type": "forgetting_risk", "urgency": 70},
            {"signal_type": "forgetting_risk", "urgency": 75},
            {"signal_type": "forgetting_risk", "urgency": 80},
            {"signal_type": "weak_area", "urgency": 60},
            {"signal_type": "weak_area", "urgency": 65},
            {"signal_type": "weak_area", "urgency": 70},
            {"signal_type": "prerequisite_gap", "urgency": 75,
             "concept": "fractions"},
        ],
        ["notes", "quiz"],
        max_ops=10,
    )
    by_block = {op.block_type: op for op in ops}
    assert by_block["plan"].action == "add"
    assert by_block["review"].action == "add"
    assert by_block["wrong_answers"].action == "add"
    assert by_block["knowledge_graph"].action == "add"
    assert all(0 <= op.urgency <= 100 for op in ops)


def test_blocks_caps_at_two_ops_by_default() -> None:
    ops = decide_blocks(
        [
            {"signal_type": "deadline", "urgency": 85},
            {"signal_type": "forgetting_risk", "urgency": 70},
            {"signal_type": "forgetting_risk", "urgency": 75},
            {"signal_type": "forgetting_risk", "urgency": 80},
            {"signal_type": "weak_area", "urgency": 60},
            {"signal_type": "weak_area", "urgency": 65},
            {"signal_type": "weak_area", "urgency": 70},
            {"signal_type": "prerequisite_gap", "urgency": 75},
        ],
        ["notes"],
    )
    assert len(ops) == 2  # MAX_OPS_PER_TURN=2 (engine source)
    urgencies = [op.urgency for op in ops]
    assert urgencies == sorted(urgencies, reverse=True)


def test_blocks_noop_when_present_or_empty() -> None:
    assert decide_blocks([], ["notes"]) == []
    ops = decide_blocks(
        [{"signal_type": "deadline", "urgency": 90}],
        ["notes", "plan"],
    )
    assert ops == []
    ops = decide_blocks(
        [{"signal_type": "forgetting_risk", "urgency": 70}],
        ["notes"],
    )
    assert ops == []  # seuil : 3 risques urgents requis


# ---------------------------------------------------------------------------
# T037 — mini-diagnostic 5Q (offline via banque injectée)
# ---------------------------------------------------------------------------


def _mini_bank() -> list[dict]:
    return [
        {"concept": "Fractions", "question": "1/2 + 1/4 = ?",
         "options": {"A": "3/4", "B": "2/6", "C": "1/8", "D": "2/4"}, "correct": "A"},
        {"concept": "Tables", "question": "7×8 = ?",
         "options": {"A": "54", "B": "56", "C": "63", "D": "48"}, "correct": "B"},
        {"concept": "Grammaire", "question": "Le ___ mange.",
         "options": {"A": "chat", "B": "chats", "C": "chatte", "D": "chatts"}, "correct": "A"},
        {"concept": "Atomes", "question": "H2O contient ?",
         "options": {"A": "H2", "B": "O2", "C": "H2O", "D": "HO"}, "correct": "C"},
        {"concept": "Cartes", "question": "La capitale de la France ?",
         "options": {"A": "Lyon", "B": "Paris", "C": "Nice", "D": "Lille"}, "correct": "B"},
    ]


def test_mini_diagnostic_full_flow(tmp_path: Path) -> None:
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService

    store = LibraryStore(tmp_path)
    subject = store.create_subject("Général")
    for q in _mini_bank():
        store.create_concept(subject.id, q["concept"])
    svc = TutorService(store, None, Config(config_dir=tmp_path))

    started = svc.start_mini_diagnostic(subject.id, questions=_mini_bank())
    assert started["total_questions"] == 5
    assert started["question_num"] == 1
    assert started["question"]
    session_id = started["session_id"]

    answers = ["A", "A", "A", "C", "B"]  # Q2 fausse (54 au lieu de 56)
    report = None
    for i, answer in enumerate(answers):
        out = svc.answer_mini_diagnostic(session_id, answer)
        if i < 4:
            assert out["done"] is False
            assert out["question_num"] == i + 2
        else:
            report = out
    assert report is not None and report["done"] is True
    assert report["score"] == 4 and report["total"] == 5
    assert "Tables" in report["weak_concepts"]
    assert report["level"] in ("beginner", "intermediate", "advanced", "expert")
    assert report["per_category"], "comptage par catégorie d'erreur requis"

    rows = store._conn.execute("SELECT * FROM error_history").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    import json as _json

    assert _json.loads(row["knowledge_points"]) == ["Tables"]
    assert row["diagnosis"] in (
        "fundamental_gap", "trap_vulnerability", "carelessness", "mastered",
    )


def test_mini_diagnostic_unknown_session(tmp_path: Path) -> None:
    from src.ollama_tutor.config import Config
    from src.ollama_tutor.tutor.service import TutorService

    store = LibraryStore(tmp_path)
    svc = TutorService(store, None, Config(config_dir=tmp_path))
    with pytest.raises(KeyError):
        svc.answer_mini_diagnostic("inexistante", "A")
