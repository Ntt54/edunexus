"""Contract tests for the /ws/tutor endpoint (004-local-ai-tutor T020).

Mirrors tests/contract/test_ws_agent_mode.py: the Ollama client is stubbed at
module level (create_app builds its own client, so ``ollama_tutor.web.server.
OllamaClient`` is monkeypatched) and fed scripted NDJSON chat iterations plus
scripted embed vectors through an httpx.MockTransport. Verifies the frame
ordering and invariants from contracts/tutor-ws-protocol.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

fastapi = pytest.importorskip("fastapi")

import ollama_tutor.web.server as web_server
from fastapi.testclient import TestClient
from ollama_tutor.config import Config

# Dimension used for synthetic embeddings in these tests.
DIM = 4
EMBED_VEC = [1.0, 0.0, 0.0, 0.0]


def make_tutor_transport(
    chat_iterations: list[list[dict[str, Any]]],
    embed_vector: list[float] = EMBED_VEC,
    gate: threading.Event | None = None,
    gate_after: int = 0,
) -> httpx.MockTransport:
    """Transport handling both /api/embed and /api/chat (one NDJSON iteration
    per chat request; optionally blocks chat request N on a gate)."""
    state = {"call": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(
                status_code=200,
                json={"embeddings": [embed_vector]},
                request=request,
            )
        idx = min(state["call"], len(chat_iterations) - 1)
        n = state["call"]
        state["call"] += 1
        if gate is not None and n >= gate_after:
            while not gate.is_set():
                await asyncio.sleep(0.01)
        body = "\n".join(json.dumps(f) for f in chat_iterations[idx]) + "\n"
        return httpx.Response(
            status_code=200,
            content=body.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


def make_blocking_transport(
    embed_vector: list[float],
    frames: list[dict[str, Any]],
    gate: threading.Event,
) -> httpx.MockTransport:
    """Transport that yields ``frames`` then blocks on ``gate`` (cancel test)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(
                status_code=200,
                json={"embeddings": [embed_vector]},
                request=request,
            )

        async def _gen():
            for f in frames:
                yield json.dumps(f)
            while not gate.is_set():
                await asyncio.sleep(0.01)

        resp = httpx.Response(
            status_code=200,
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )
        resp.aiter_lines = lambda: _gen()
        return resp

    return httpx.MockTransport(handler)


def write_config(cfg_dir: Path, data: dict[str, Any]) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps(data))
    return cfg_dir


def build_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transport: httpx.MockTransport,
    tutor_extra: dict[str, Any] | None = None,
) -> TestClient:
    cfg_dir = write_config(
        tmp_path / "config",
        {"tutor": {"enabled": True, **(tutor_extra or {})}, "agent": {"enabled": False}},
    )
    RealClient = web_server.OllamaClient
    monkeypatch.setattr(
        web_server, "OllamaClient", lambda: RealClient(transport=transport)
    )
    monkeypatch.setattr(web_server, "get_config_dir", lambda: tmp_path / "platform-config")
    tc = TestClient(web_server.create_app(config_dir=cfg_dir))
    # Stash the config dir so tests can build a store against the same DB.
    tc.app._tutor_cfg_dir = cfg_dir  # type: ignore[attr-defined]
    return tc


def recv_until_end(ws: Any, cap: int = 300) -> list[dict[str, Any]]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == "end":
            return frames
        assert len(frames) <= cap, "too many frames without end"


def seed_subject_with_chunk(store, name: str, chunk_text: str, vec: list[float], tmp_path: Path):
    subject = store.create_subject(name)
    p = tmp_path / f"{name}.txt"
    p.write_text(chunk_text)
    book = store.import_document(subject.id, p)
    store.add_chunks(subject.id, book.id, [chunk_text], [vec], "embeddinggemma")
    store.mark_indexed(book.id, 1)
    return subject, book


# ---------------------------------------------------------------------------
# T020 — origin/host guard
# ---------------------------------------------------------------------------


class TestTutorOriginGuard:
    def test_ws_rejects_foreign_origin(self, monkeypatch, tmp_path):
        transport = make_tutor_transport([[]])
        with build_app(monkeypatch, tmp_path, transport) as tc:
            with pytest.raises(Exception):  # WebSocketDisconnect (close 1008)
                with tc.websocket_connect(
                    "/ws/tutor", headers={"origin": "http://evil.example"}
                ):
                    pass

    def test_ws_rejects_when_tutor_disabled(self, monkeypatch, tmp_path):
        cfg_dir = write_config(
            tmp_path / "config",
            {"tutor": {"enabled": False}, "agent": {"enabled": False}},
        )
        transport = make_tutor_transport([[]])
        RealClient = web_server.OllamaClient
        monkeypatch.setattr(
            web_server, "OllamaClient", lambda: RealClient(transport=transport)
        )
        monkeypatch.setattr(web_server, "get_config_dir", lambda: tmp_path / "platform-config")
        with TestClient(web_server.create_app(config_dir=cfg_dir)) as tc:
            with pytest.raises(Exception):
                with tc.websocket_connect("/ws/tutor"):
                    pass


# ---------------------------------------------------------------------------
# T020 — ask flow ordering
# ---------------------------------------------------------------------------


class TestTutorAskFlow:
    def test_ask_produces_start_sources_content_end(self, monkeypatch, tmp_path):
        chat = [[
            {"message": {"content": "Réponse fondée."}, "done": False},
            {
                "done": True,
                "prompt_eval_count": 10,
                "eval_count": 5,
                "eval_rate": 20.0,
            },
        ]]
        transport = make_tutor_transport(chat)
        with build_app(monkeypatch, tmp_path, transport) as tc:
            svc = _tutor_service(tc)
            subject, book = seed_subject_with_chunk(
                svc.store, "Math", "Un passage sur les intégrales.", EMBED_VEC, tmp_path
            )
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({
                    "type": "ask",
                    "question": "Qu'est-ce qu'une intégrale ?",
                    "subject_id": subject.id,
                })
                frames = recv_until_end(ws)

        types = [f["type"] for f in frames]
        assert types[0] == "start"
        assert types[1] == "sources"
        assert "content_delta" in types
        assert types[-1] == "end"
        # sources MUST precede any content_delta (SC-004).
        assert types.index("sources") < types.index("content_delta")
        # sources cite the imported book, not nothing.
        sources = frames[1]["sources"]
        assert sources and sources[0]["book"] == book.title
        assert frames[-1]["status"] == "done"
        assert isinstance(frames[-1]["session_id"], str)


# ---------------------------------------------------------------------------
# T020 — busy guard
# ---------------------------------------------------------------------------


class TestTutorBusyGuard:
    def test_second_concurrent_ask_gets_busy(self, monkeypatch, tmp_path):
        gate = threading.Event()
        chat = [[
            {"message": {"content": "Réponse longue."}, "done": False},
            {"done": True, "prompt_eval_count": 10, "eval_count": 5, "eval_rate": 20.0},
        ]]
        transport = make_tutor_transport(chat, gate=gate, gate_after=0)
        with build_app(monkeypatch, tmp_path, transport) as tc:
            svc = _tutor_service(tc)
            subject, _ = seed_subject_with_chunk(
                svc.store, "Math", "Un passage sur les intégrales.", EMBED_VEC, tmp_path
            )
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({
                    "type": "ask",
                    "question": "question A",
                    "subject_id": subject.id,
                })
                first = ws.receive_json()
                assert first["type"] == "start"
                second = ws.receive_json()
                assert second["type"] == "sources"

                # Second ask while the first is still running → busy.
                ws.send_json({
                    "type": "ask",
                    "question": "question B",
                    "subject_id": subject.id,
                })
                busy = ws.receive_json()
                assert busy["type"] == "error"
                assert busy["code"] == "busy"

                # Release the first run.
                gate.set()
                rest = recv_until_end(ws)
            assert rest[-1]["type"] == "end"
            assert rest[-1]["status"] == "done"


# ---------------------------------------------------------------------------
# T020 — cancel
# ---------------------------------------------------------------------------


class TestTutorCancel:
    def test_cancel_produces_cancelled_and_end_stopped(self, monkeypatch, tmp_path):
        gate = threading.Event()
        transport = make_blocking_transport(
            EMBED_VEC,
            [{"message": {"content": "Réponse partielle."}, "done": False}],
            gate,
        )
        with build_app(monkeypatch, tmp_path, transport) as tc:
            svc = _tutor_service(tc)
            subject, _ = seed_subject_with_chunk(
                svc.store, "Math", "Un passage sur les intégrales.", EMBED_VEC, tmp_path
            )
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({
                    "type": "ask",
                    "question": "question",
                    "subject_id": subject.id,
                })
                assert ws.receive_json()["type"] == "start"
                assert ws.receive_json()["type"] == "sources"
                # At least one content delta before cancel.
                delta = ws.receive_json()
                assert delta["type"] == "content_delta"

                ws.send_json({"type": "cancel"})
                cancelled = ws.receive_json()
                assert cancelled["type"] == "cancelled"
                end = recv_until_end(ws)
                assert end[-1]["type"] == "end"
                assert end[-1]["status"] == "stopped"


# ---------------------------------------------------------------------------
# T020 — empty retrieval ⇒ ungrounded model-knowledge answer (Phase 6 UX)
# ---------------------------------------------------------------------------


class TestTutorNoPassages:
    def test_no_retrieval_streams_ungrounded_answer(self, monkeypatch, tmp_path):
        chat = [[
            {"message": {"content": "Voici une réponse de mes connaissances."}, "done": False},
            {
                "done": True,
                "prompt_eval_count": 10,
                "eval_count": 5,
                "eval_rate": 20.0,
            },
        ]]
        transport = make_tutor_transport(chat)
        with build_app(monkeypatch, tmp_path, transport) as tc:
            svc = _tutor_service(tc)
            # Subject with no indexed chunks.
            subject = svc.store.create_subject("Vide")
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({
                    "type": "ask",
                    "question": "question orpheline",
                    "subject_id": subject.id,
                })
                frames = recv_until_end(ws)
        types = [f["type"] for f in frames]
        assert types[0] == "start"
        assert types[-1] == "end"
        assert frames[-1]["status"] == "done"
        # NO sources frame, NO error frame: the answer streams cleanly.
        assert "sources" not in types
        assert not any(t == "error" for t in types)
        deltas = [f for f in frames if f["type"] == "content_delta"]
        assert deltas, "ungrounded answer must stream content"
        # The system note prefixes the answer; citations are absent.
        assert deltas[0]["text"].startswith(
            "(aucune source sélectionnée — réponse sans contexte documentaire)"
        )
        joined = "".join(d["text"] for d in deltas)
        assert "connaissances" in joined


# ---------------------------------------------------------------------------
# T050 — voice (transcribe) contract
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, wav_path: str, transcript: str):
        self._wav = wav_path
        self._transcript = transcript
        self.returncode = 0

    async def communicate(self):
        with open(self._wav + ".txt", "w", encoding="utf-8") as fh:
            fh.write(self._transcript)
        return (b"", b"")


class TestTutorVoice:
    def test_transcribe_returns_transcript(self, monkeypatch, tmp_path):
        transcript = "le calcul intégral remonte à Leibniz"

        async def fake_exec(*args, **kwargs):
            argv = list(args)
            wav = argv[argv.index("-f") + 1]
            return _FakeProc(wav, transcript)

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        transport = make_tutor_transport([[]])
        with build_app(
            monkeypatch, tmp_path, transport,
            tutor_extra={"whisper_binary": sys.executable, "whisper_model": "/models/ggml-base.bin"},
        ) as tc:
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({
                    "type": "transcribe",
                    "audio": base64.b64encode(b"RIFFdummyWAVE").decode("ascii"),
                })
                frame = ws.receive_json()
        assert frame["type"] == "transcript"
        assert frame["text"] == transcript

    def test_transcribe_without_config_is_voice_disabled(self, monkeypatch, tmp_path):
        transport = make_tutor_transport([[]])
        with build_app(monkeypatch, tmp_path, transport) as tc:
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({"type": "transcribe", "audio": base64.b64encode(b"x").decode("ascii")})
                frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "voice_disabled"

    def test_transcribe_invalid_audio_is_rejected(self, monkeypatch, tmp_path):
        transport = make_tutor_transport([[]])
        with build_app(
            monkeypatch, tmp_path, transport,
            tutor_extra={"whisper_binary": sys.executable, "whisper_model": "/m"},
        ) as tc:
            with tc.websocket_connect("/ws/tutor") as ws:
                ws.send_json({"type": "transcribe", "audio": "not!!valid!!base64"})
                frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "invalid_audio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tutor_service(tc: TestClient):
    """Build a TutorService + LibraryStore bound to the SAME config dir the app
    used (so they share ``<config>/tutor/library.db``)."""
    from ollama_tutor.tutor.service import TutorService
    from ollama_tutor.tutor.store import LibraryStore

    cfg_dir = Path(str(tc.app._tutor_cfg_dir))  # type: ignore[attr-defined]
    store = LibraryStore(cfg_dir)
    client = web_server.OllamaClient()
    config = Config(config_dir=cfg_dir)
    return TutorService(store, client, config)
