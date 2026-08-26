"""Data models for Ollama TUI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    """A single message in a conversation."""

    role: MessageRole
    content: str
    images: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    thinking: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.images:
            d["images"] = self.images
        return d


@dataclass
class InferenceStats:
    """Statistics from an Ollama inference response."""

    model: str = ""
    prompt_tokens: int = 0
    generated_tokens: int = 0
    prompt_eval_duration: float = 0.0
    eval_duration: float = 0.0
    total_duration: float = 0.0

    @property
    def prompt_eval_speed(self) -> float:
        if self.prompt_eval_duration <= 0:
            return 0.0
        return self.prompt_tokens / self.prompt_eval_duration

    @property
    def generation_speed(self) -> float:
        if self.eval_duration <= 0:
            return 0.0
        return self.generated_tokens / self.eval_duration

    def format_verbose(self) -> str:
        lines = []
        if self.model:
            lines.append(f"Model: {self.model}")
        if self.prompt_tokens > 0:
            lines.append(f"Prompt tokens: {self.prompt_tokens}")
        if self.generated_tokens > 0:
            lines.append(f"Generated tokens: {self.generated_tokens}")
        if self.prompt_eval_duration > 0:
            lines.append(f"Prompt eval: {self.prompt_eval_duration:.2f} s")
        if self.eval_duration > 0:
            lines.append(f"Generation: {self.eval_duration:.2f} s")
        speed = self.generation_speed
        if speed > 0:
            lines.append(f"Speed: {speed:.2f} tok/s")
        if self.total_duration > 0:
            lines.append(f"Total: {self.total_duration:.2f} s")
        return "\n".join(lines)


@dataclass
class OllamaModel:
    """Info about an available Ollama model."""

    name: str
    size: int = 0
    digest: str = ""
    modified_at: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def size_human(self) -> str:
        if self.size <= 0:
            return "unknown"
        size = float(self.size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


@dataclass
class OllamaOptions:
    """Ollama inference options sent via the API."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    num_ctx: int | None = None
    num_predict: int | None = None
    repeat_penalty: float | None = None
    seed: int | None = None
    num_batch: int | None = None
    # Défaut 3 threads pour tous les LLM Ollama (demande utilisateur) ;
    # surchargeable via config « options » ou preset.
    num_thread: int = 3
    keep_alive: str | int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.top_p is not None:
            d["top_p"] = self.top_p
        if self.top_k is not None:
            d["top_k"] = self.top_k
        if self.num_ctx is not None:
            d["num_ctx"] = self.num_ctx
        if self.num_predict is not None:
            d["num_predict"] = self.num_predict
        if self.repeat_penalty is not None:
            d["repeat_penalty"] = self.repeat_penalty
        if self.seed is not None:
            d["seed"] = self.seed
        if self.num_batch is not None:
            d["num_batch"] = self.num_batch
        if self.num_thread is not None:
            d["num_thread"] = self.num_thread
        if self.keep_alive is not None:
            d["keep_alive"] = self.keep_alive
        return d


@dataclass
class Preset:
    """A saved configuration preset."""

    name: str
    model: str = ""
    think: bool = False
    options: OllamaOptions = field(default_factory=OllamaOptions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "think": self.think,
            "options": self.options.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Preset:
        opts = OllamaOptions(**d.get("options", {}))
        return cls(
            name=d["name"],
            model=d.get("model", ""),
            think=d.get("think", False),
            options=opts,
        )


@dataclass
class Conversation:
    """A conversation with metadata."""

    id: str
    title: str = "New conversation"
    messages: list[Message] = field(default_factory=list)
    model: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    agent_session_id: str | None = None


class StepType(str, Enum):
    """Type of an agent step."""
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    OBSERVATION = "observation"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"


class StepStatus(str, Enum):
    """Status of a tool execution step."""
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"


class SessionStatus(str, Enum):
    """Terminal status of an agent session."""
    RUNNING = "running"
    DONE = "done"
    STOPPED = "stopped"
    ERROR = "error"
    MAX_STEPS = "max_steps"


@dataclass
class ToolCall:
    """A tool call made by the agent."""
    name: str
    args: dict[str, Any]
    raw_result: str = ""
    truncated_result: str = ""
    exit_status: int | None = None
    confirmed_by_user: bool = False
    duration_ms: int = 0
    status: StepStatus = StepStatus.OK


@dataclass
class AgentStep:
    """A single iteration in the agent loop."""
    index: int
    type: StepType
    content: str = ""
    tool_call: ToolCall | None = None
    duration_ms: int | None = None
    status: StepStatus | None = None


@dataclass
class AgentSession:
    """A complete agent session record."""
    id: str
    goal: str
    model: str
    steps: list[AgentStep]
    status: SessionStatus
    iterations_used: int
    started_at: str
    finished_at: str | None = None
    config_snapshot: dict[str, Any] | None = None

    @classmethod
    def create(cls, goal: str, model: str, config_snapshot: dict[str, Any]) -> "AgentSession":
        import uuid
        import datetime
        return cls(
            id=uuid.uuid4().hex[:12],
            goal=goal,
            model=model,
            steps=[],
            status=SessionStatus.RUNNING,
            iterations_used=0,
            started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            finished_at=None,
            config_snapshot=config_snapshot,
        )
