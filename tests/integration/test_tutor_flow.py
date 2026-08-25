"""Offline integration test for grounded ask + subject isolation (T025).

Seeds a two-subject store with scripted embeddings, drives ``TutorService.ask``
with a fake Ollama client, and asserts that an ask over subject A cites ONLY
A's books (and delta ordering holds), and that switching the active subject to
B yields no A passages. Fully offline (no daemon, no network).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ollama_tutor.client import InferenceStats, StreamEvent
from ollama_tutor.config import Config
from ollama_tutor.tutor.service import TutorService
from ollama_tutor.tutor.store import LibraryStore

DIM = 4
VEC_A = [1.0, 0.0, 0.0, 0.0]
VEC_B = [0.0, 1.0, 0.0, 0.0]

CHAT_FRAMES = [
    {"message": {"content": "Réponse ancrée dans le livre."}, "done": False},
    {
        "done": True,
        "prompt_eval_count": 12,
        "eval_count": 6,
        "eval_rate": 18.0,
    },
]


class FakeTutorClient:
    """Scripted Ollama client: embed maps a keyword to a vector, chat streams
    fixed frames. No network, no daemon."""

    def __init__(self, embed_map: dict[str, list[float]], chat_frames: list[dict[str, Any]]):
        self.embed_map = embed_map
        self.chat_frames = chat_frames

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        text = inputs[0] if inputs else ""
        for key, vec in self.embed_map.items():
            if key and key in text:
                return [vec]
        return [self.embed_map.get("", [0.0] * DIM)]

    async def chat_stream(self, messages, model, *, think=False, options=None, format=None, tools=None):
        for f in self.chat_frames:
            msg = f.get("message", {})
            if msg.get("thinking"):
                yield StreamEvent(kind="thinking", text=msg["thinking"])
            if msg.get("content"):
                yield StreamEvent(kind="content", text=msg["content"])
            if f.get("done"):
                yield StreamEvent(
                    kind="done",
                    stats=InferenceStats(
                        model=model,
                        prompt_tokens=f.get("prompt_eval_count", 0),
                        generated_tokens=f.get("eval_count", 0),
                        eval_duration=1.0,
                    ),
                )


def seed_subject(store: LibraryStore, name: str, book_title: str, chunk_text: str,
                 vec: list[float], tmp_path: Path):
    subject = store.create_subject(name)
    p = tmp_path / f"{name}.txt"
    p.write_text(chunk_text)
    book = store.import_document(subject.id, p)
    store.add_chunks(subject.id, book.id, [chunk_text], [vec], "embeddinggemma")
    store.mark_indexed(book.id, 1)
    return subject, book


@pytest.fixture
def two_subject_service(tmp_path: Path):
    store = LibraryStore(tmp_path)
    config = Config(config_dir=tmp_path)
    config.tutor_enabled = True
    client = FakeTutorClient(
        embed_map={"sujet A": VEC_A, "sujet B": VEC_B, "": [0.0] * DIM},
        chat_frames=CHAT_FRAMES,
    )
    return TutorService(store, client, config), store


async def _collect(service: TutorService, subject_name: str, question: str):
    frames = []
    async for f in service.ask(subject_name, question):
        frames.append(f)
    return frames


@pytest.mark.asyncio
async def test_grounded_ask_isolation(two_subject_service, tmp_path: Path):
    service, store = two_subject_service
    subj_a, book_a = seed_subject(
        store, "SujetA", "LivreA", "Contenu propre au sujet A.", VEC_A, tmp_path
    )
    subj_b, book_b = seed_subject(
        store, "SujetB", "LivreB", "Contenu propre au sujet B.", VEC_B, tmp_path
    )

    # --- Ask over subject A ---
    frames_a = await _collect(service, "SujetA", "question sur le sujet A")
    types_a = [f["type"] for f in frames_a]
    assert types_a[0] == "sources"
    assert "content_delta" not in [f["type"] for f in frames_a[:1]]  # sources first
    assert "delta" in types_a
    assert types_a[-1] == "end"
    assert types_a.index("sources") < types_a.index("delta")

    sources_a = frames_a[0]["sources"]
    assert sources_a, "expected at least one retrieved passage for A"
    cited_books_a = {s["book"] for s in sources_a}
    assert cited_books_a == {book_a.title}
    assert book_b.title not in cited_books_a

    # --- Switch active subject to B, ask again ---
    frames_b = await _collect(service, "SujetB", "question sur le sujet B")
    sources_b = frames_b[0]["sources"]
    cited_books_b = {s["book"] for s in sources_b}
    assert cited_books_b == {book_b.title}
    # No passage from A must leak into B's answer.
    assert book_a.title not in cited_books_b

    # --- Sanity: a question matching neither falls back to model knowledge
    # (Phase 6 UX): no sources frame, no error frame, note-prefixed answer.
    frames_none = await _collect(service, "SujetA", "sujet inconnu xyz")
    types_none = [f["type"] for f in frames_none]
    assert "sources" not in types_none
    assert not any(t == "error" for t in types_none)
    deltas = [f for f in frames_none if f["type"] == "delta"]
    assert deltas and deltas[0]["text"].startswith(
        "(aucune source sélectionnée — réponse sans contexte documentaire)"
    )
    assert types_none[-1] == "end"
    assert frames_none[-1]["status"] == "done"
