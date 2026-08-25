"""Shared test fixtures and configuration."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest


def create_mock_transport(frames: list[dict[str, Any]]) -> httpx.MockTransport:
    """Create an httpx.MockTransport that yields the given NDJSON frames.

    Args:
        frames: List of dicts representing NDJSON frames to return.
                Each dict will be serialized to JSON and yielded as a line.

    Returns:
        An httpx.MockTransport that streams the frames as NDJSON lines.
    """
    async def handler(request: httpx.Request) -> httpx.Response:
        # Convert frames to NDJSON lines
        lines = [json.dumps(frame) for frame in frames]
        body = "\n".join(lines) + "\n"

        return httpx.Response(
            status_code=200,
            content=body.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    return httpx.MockTransport(handler)


def create_error_transport(status_code: int, body: str = "") -> httpx.MockTransport:
    """Create a mock transport that returns an error response."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            content=body.encode("utf-8") if body else b"",
            request=request,
        )
    return httpx.MockTransport(handler)


def create_embed_transport(vectors: list[list[float]]) -> httpx.MockTransport:
    """Create a mock transport for ``POST /api/embed`` (research D14).

    Returns a plain JSON ``{"embeddings": vectors}`` response (content-type
    ``application/json``, not NDJSON) for any POST request, so offline tests
    can script embedding output without a live Ollama daemon.
    """
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"embeddings": vectors},
            request=request,
        )
    return httpx.MockTransport(handler)


def create_streaming_transport(frames: list[dict[str, Any]]) -> httpx.MockTransport:
    """Create a mock transport that streams frames one at a time with proper async iteration.

    This simulates a real streaming response where frames arrive incrementally.
    """
    frame_iter = iter(frames)

    async def handler(request: httpx.Request) -> httpx.Response:
        async def aiter_lines():
            for frame in frames:
                yield json.dumps(frame)
            # Signal end
            yield ""

        # Create a mock response with proper async iteration
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )
        # Patch the aiter_lines method
        response.aiter_lines = aiter_lines
        return response

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_transport_factory():
    """Factory fixture to create mock transports with custom frames."""
    return create_mock_transport


@pytest.fixture
def streaming_transport_factory():
    """Factory fixture to create streaming mock transports."""
    return create_streaming_transport


# Sample frames for common test scenarios
@pytest.fixture
def thinking_then_content_frames():
    """Frames simulating thinking followed by content, then done."""
    return [
        {"message": {"thinking": "Let me think about this..."}, "done": False},
        {"message": {"thinking": "I need to use a tool."}, "done": False},
        {"message": {"content": '{"action": "list_dir", "path": "."}'}, "done": False},
        {
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 50,
            "eval_rate": 25.5,
        },
    ]


@pytest.fixture
def multi_step_frames():
    """Frames for a multi-step agent session."""
    return [
        # Step 1: thinking -> tool call
        {"message": {"thinking": "I'll start by listing the directory."}, "done": False},
        {"message": {"content": '{"action": "list_dir", "path": "."}'}, "done": False},
        {
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 50,
            "eval_rate": 25.5,
        },
        # Step 2: thinking -> done
        {"message": {"thinking": "Now I can provide the answer."}, "done": False},
        {"message": {"content": '{"action": "done", "summary": "Found 3 files"}'}, "done": False},
        {
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 80,
            "eval_count": 30,
            "eval_rate": 22.0,
        },
    ]


@pytest.fixture
def error_frames():
    """Frames that would cause an error."""
    return [
        {"message": {"content": '{"action": "invalid_tool", "path": "."}'}, "done": False},
        {
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 50,
            "eval_count": 20,
            "eval_rate": 15.0,
        },
    ]