"""Configuration and presets management for Ollama TUI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .models import OllamaOptions, Preset

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "ollama-tui"

_VALID_TUTOR_LEVELS = {"beginner", "intermediate", "advanced", "expert"}


class Config:
    """Application configuration."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.config_file = self.config_dir / "config.json"
        self.presets_file = self.config_dir / "presets.json"
        self.history_dir = self.config_dir / "history"
        self._data: dict[str, Any] = {}
        self._presets: list[Preset] = []
        self._save_task: asyncio.Task | None = None
        self._ensure_dirs()
        self._load()

    def _ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        if self.config_file.exists():
            try:
                self._data = json.loads(self.config_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}
        if self.presets_file.exists():
            try:
                raw = json.loads(self.presets_file.read_text())
                self._presets = [Preset.from_dict(p) for p in raw]
            except (json.JSONDecodeError, OSError):
                self._presets = []

    def _schedule_save(self) -> None:
        """Schedule a debounced save. Falls back to immediate save if no event loop."""
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        try:
            loop = asyncio.get_running_loop()
            self._save_task = loop.create_task(self._debounced_save())
        except RuntimeError:
            # No running event loop (e.g., in tests) - fall back to immediate save
            self._write_files()

    async def _debounced_save(self) -> None:
        """Debounced save - waits 100ms before writing."""
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return
        self._write_files()

    def _write_files(self) -> None:
        """Actually write the files to disk."""
        self.config_file.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        raw = [p.to_dict() for p in self._presets]
        self.presets_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False))

    def save(self) -> None:
        """Immediate save (for shutdown/flush)."""
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        self._write_files()

    async def flush(self) -> None:
        """Async flush - waits for any pending save."""
        if self._save_task and not self._save_task.done():
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass

    # --- Config keys ---

    @property
    def last_model(self) -> str:
        return self._data.get("last_model", "")

    @last_model.setter
    def last_model(self, value: str) -> None:
        self._data["last_model"] = value
        self._schedule_save()

    @property
    def think(self) -> bool:
        return self._data.get("think", False)

    @think.setter
    def think(self, value: bool) -> None:
        self._data["think"] = value
        self._schedule_save()

    @property
    def verbose(self) -> bool:
        return self._data.get("verbose", True)

    @verbose.setter
    def verbose(self, value: bool) -> None:
        self._data["verbose"] = value
        self._schedule_save()

    @property
    def options(self) -> OllamaOptions:
        raw = self._data.get("options", {})
        return OllamaOptions(**raw)

    @options.setter
    def options(self, value: OllamaOptions) -> None:
        self._data["options"] = value.to_dict()
        self._schedule_save()

    @property
    def selected_preset(self) -> str:
        return self._data.get("selected_preset", "")

    @selected_preset.setter
    def selected_preset(self, value: str) -> None:
        self._data["selected_preset"] = value
        self._schedule_save()

    # --- Presets ---

    @property
    def presets(self) -> list[Preset]:
        return list(self._presets)

    def add_preset(self, preset: Preset) -> None:
        self._presets.append(preset)
        self.save()

    def remove_preset(self, name: str) -> bool:
        before = len(self._presets)
        self._presets = [p for p in self._presets if p.name != name]
        if len(self._presets) < before:
            self.save()
            return True
        return False

    def get_preset(self, name: str) -> Preset | None:
        for p in self._presets:
            if p.name == name:
                return p
        return None

    # --- Agent Config ---

    @property
    def agent_enabled(self) -> bool:
        return self._data.get("agent", {}).get("enabled", False)

    @agent_enabled.setter
    def agent_enabled(self, value: bool) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["enabled"] = value
        self._schedule_save()

    @property
    def agent_max_iterations(self) -> int:
        val = self._data.get("agent", {}).get("max_iterations", 8)
        return max(1, min(20, val))

    @agent_max_iterations.setter
    def agent_max_iterations(self, value: int) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["max_iterations"] = max(1, min(20, value))
        self._schedule_save()

    @property
    def agent_max_output_chars(self) -> int:
        val = self._data.get("agent", {}).get("max_output_chars", 800)
        return max(200, val)

    @agent_max_output_chars.setter
    def agent_max_output_chars(self, value: int) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["max_output_chars"] = max(200, value)
        self._schedule_save()

    @property
    def agent_command_timeout_s(self) -> int:
        val = self._data.get("agent", {}).get("command_timeout_s", 30)
        return max(5, min(300, val))

    @agent_command_timeout_s.setter
    def agent_command_timeout_s(self, value: int) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["command_timeout_s"] = max(5, min(300, value))
        self._schedule_save()

    @property
    def agent_allowed_root(self) -> str:
        return self._data.get("agent", {}).get("allowed_root", str(Path.cwd()))

    @agent_allowed_root.setter
    def agent_allowed_root(self, value: str) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["allowed_root"] = value
        self._schedule_save()

    @property
    def agent_auto_approve_commands(self) -> bool:
        return self._data.get("agent", {}).get("auto_approve_commands", False)

    @agent_auto_approve_commands.setter
    def agent_auto_approve_commands(self, value: bool) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["auto_approve_commands"] = value
        self._schedule_save()

    @property
    def agent_context_token_budget(self) -> int:
        return self._data.get("agent", {}).get("context_token_budget", 3072)

    @agent_context_token_budget.setter
    def agent_context_token_budget(self, value: int) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["context_token_budget"] = value
        self._schedule_save()

    @property
    def agent_native_tools(self) -> bool:
        return self._data.get("agent", {}).get("native_tools", False)

    @agent_native_tools.setter
    def agent_native_tools(self, value: bool) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["native_tools"] = value
        self._schedule_save()

    @property
    def fix_max_attempts(self) -> int:
        """Max failed run_command attempts in a build-fix loop (research D3)."""
        val = self._data.get("agent", {}).get("fix_max_attempts", 4)
        return max(1, min(20, val))

    @fix_max_attempts.setter
    def fix_max_attempts(self, value: int) -> None:
        if "agent" not in self._data:
            self._data["agent"] = {}
        self._data["agent"]["fix_max_attempts"] = max(1, min(20, value))
        self._schedule_save()

    def get_agent_config_snapshot(self) -> dict[str, Any]:
        """Get a snapshot of the current agent config for session recording."""
        return {
            "enabled": self.agent_enabled,
            "max_iterations": self.agent_max_iterations,
            "max_output_chars": self.agent_max_output_chars,
            "command_timeout_s": self.agent_command_timeout_s,
            "allowed_root": self.agent_allowed_root,
            "auto_approve_commands": self.agent_auto_approve_commands,
            "context_token_budget": self.agent_context_token_budget,
            "native_tools": self.agent_native_tools,
            "fix_max_attempts": self.fix_max_attempts,
        }

    # --- Tutor Config (004-local-ai-tutor) ---

    @property
    def tutor_enabled(self) -> bool:
        return self._data.get("tutor", {}).get("enabled", False)

    @tutor_enabled.setter
    def tutor_enabled(self, value: bool) -> None:
        self._data.setdefault("tutor", {})["enabled"] = value
        self._schedule_save()

    @property
    def tutor_embedding_model(self) -> str:
        return self._data.get("tutor", {}).get("embedding_model", "embeddinggemma")

    @tutor_embedding_model.setter
    def tutor_embedding_model(self, value: str) -> None:
        self._data.setdefault("tutor", {})["embedding_model"] = value
        self._schedule_save()

    @property
    def tutor_model(self) -> str:
        return self._data.get("tutor", {}).get("tutor_model", "gemma4:e2b")

    @tutor_model.setter
    def tutor_model(self, value: str) -> None:
        self._data.setdefault("tutor", {})["tutor_model"] = value
        self._schedule_save()

    @property
    def tutor_socratic(self) -> bool:
        return self._data.get("tutor", {}).get("socratic", True)

    @tutor_socratic.setter
    def tutor_socratic(self, value: bool) -> None:
        self._data.setdefault("tutor", {})["socratic"] = value
        self._schedule_save()

    @property
    def tutor_level(self) -> str:
        return self._data.get("tutor", {}).get("level", "intermediate")

    @tutor_level.setter
    def tutor_level(self, value: str) -> None:
        if value not in _VALID_TUTOR_LEVELS:
            raise ValueError(
                f"Invalid tutor level {value!r}; expected one of {sorted(_VALID_TUTOR_LEVELS)}"
            )
        self._data.setdefault("tutor", {})["level"] = value
        self._schedule_save()

    @property
    def tutor_think(self) -> bool:
        return self._data.get("tutor", {}).get("think", False)

    @tutor_think.setter
    def tutor_think(self, value: bool) -> None:
        self._data.setdefault("tutor", {})["think"] = value
        self._schedule_save()

    @property
    def tutor_top_k(self) -> int:
        return max(1, min(20, self._data.get("tutor", {}).get("top_k", 5)))

    @tutor_top_k.setter
    def tutor_top_k(self, value: int) -> None:
        self._data.setdefault("tutor", {})["top_k"] = max(1, min(20, value))
        self._schedule_save()

    @property
    def tutor_reranking_enabled(self) -> bool:
        """Enable post-retrieval reranking (US12, default False)."""
        return self._data.get("tutor", {}).get("reranking_enabled", False)

    @tutor_reranking_enabled.setter
    def tutor_reranking_enabled(self, value: bool) -> None:
        self._data.setdefault("tutor", {})["reranking_enabled"] = value
        self._schedule_save()

    # ------------------------------------------------------------------
    # Fournisseur LLM (B1 multi-fournisseur)
    # ------------------------------------------------------------------

    @property
    def llm_provider(self) -> str:
        """Fournisseur LLM : 'ollama' (défaut) ou 'openai' (compatible)."""
        return self._data.get("tutor", {}).get("llm_provider", "ollama")

    @llm_provider.setter
    def llm_provider(self, value: str) -> None:
        if value not in ("ollama", "openai"):
            raise ValueError(f"Fournisseur inconnu {value!r} ; attendu 'ollama' ou 'openai'")
        self._data.setdefault("tutor", {})["llm_provider"] = value
        self._schedule_save()

    @property
    def llm_base_url(self) -> str:
        """URL de base du fournisseur LLM (ex. https://api.openai.com/v1)."""
        return self._data.get("tutor", {}).get("llm_base_url", "")

    @llm_base_url.setter
    def llm_base_url(self, value: str) -> None:
        self._data.setdefault("tutor", {})["llm_base_url"] = value
        self._schedule_save()

    @property
    def llm_api_key(self) -> str:
        """Clé API du fournisseur LLM (optionnel, vide = pas d'auth)."""
        return self._data.get("tutor", {}).get("llm_api_key", "")

    @llm_api_key.setter
    def llm_api_key(self, value: str) -> None:
        self._data.setdefault("tutor", {})["llm_api_key"] = value
        self._schedule_save()

    @property
    def tutor_whisper_binary(self) -> str:
        return self._data.get("tutor", {}).get("whisper_binary", "")

    @tutor_whisper_binary.setter
    def tutor_whisper_binary(self, value: str) -> None:
        self._data.setdefault("tutor", {})["whisper_binary"] = value
        self._schedule_save()

    @property
    def tutor_whisper_model(self) -> str:
        return self._data.get("tutor", {}).get("whisper_model", "")

    @tutor_whisper_model.setter
    def tutor_whisper_model(self, value: str) -> None:
        self._data.setdefault("tutor", {})["whisper_model"] = value
        self._schedule_save()

    # --- Tutor GGUF/llama.cpp paths (Phase 0; empty = feature disabled) ---

    @property
    def tutor_llama_bin(self) -> str:
        return self._data.get("tutor", {}).get("llama_bin", "")

    @tutor_llama_bin.setter
    def tutor_llama_bin(self, value: str) -> None:
        self._data.setdefault("tutor", {})["llama_bin"] = value
        self._schedule_save()

    @property
    def tutor_llama_models_dir(self) -> str:
        return self._data.get("tutor", {}).get("llama_models_dir", "")

    @tutor_llama_models_dir.setter
    def tutor_llama_models_dir(self, value: str) -> None:
        self._data.setdefault("tutor", {})["llama_models_dir"] = value
        self._schedule_save()

    @property
    def tutor_embed_gguf(self) -> str:
        """Granite Embedding R2 GGUF path (empty = disabled)."""
        return self._data.get("tutor", {}).get("embed_gguf", "")

    @tutor_embed_gguf.setter
    def tutor_embed_gguf(self, value: str) -> None:
        self._data.setdefault("tutor", {})["embed_gguf"] = value
        self._schedule_save()

    @property
    def tutor_docling_gguf(self) -> str:
        """Granite-Docling Q5_K_M GGUF path (empty = disabled)."""
        return self._data.get("tutor", {}).get("docling_gguf", "")

    @tutor_docling_gguf.setter
    def tutor_docling_gguf(self, value: str) -> None:
        self._data.setdefault("tutor", {})["docling_gguf"] = value
        self._schedule_save()

    @property
    def tutor_docling_mmproj(self) -> str:
        """Granite-Docling mmproj f16 path (empty = disabled)."""
        return self._data.get("tutor", {}).get("docling_mmproj", "")

    @tutor_docling_mmproj.setter
    def tutor_docling_mmproj(self, value: str) -> None:
        self._data.setdefault("tutor", {})["docling_mmproj"] = value
        self._schedule_save()

    @property
    def tutor_llm_gguf(self) -> str:
        """Granite 4.1 3B Instruct GGUF path (empty = disabled)."""
        return self._data.get("tutor", {}).get("llm_gguf", "")

    @tutor_llm_gguf.setter
    def tutor_llm_gguf(self, value: str) -> None:
        self._data.setdefault("tutor", {})["llm_gguf"] = value
        self._schedule_save()

    @property
    def tutor_llama_health_timeout_s(self) -> int:
        return max(
            10, min(600, self._data.get("tutor", {}).get("llama_health_timeout_s", 120))
        )

    @tutor_llama_health_timeout_s.setter
    def tutor_llama_health_timeout_s(self, value: int) -> None:
        clamped = max(10, min(600, value))
        self._data.setdefault("tutor", {})["llama_health_timeout_s"] = clamped
        self._schedule_save()

    @property
    def tutor_ocr_text_threshold(self) -> int:
        """Min stripped chars per page to trust the text layer over OCR."""
        return max(
            0,
            min(10000, self._data.get("tutor", {}).get("ocr_text_threshold", 32)),
        )

    @tutor_ocr_text_threshold.setter
    def tutor_ocr_text_threshold(self, value: int) -> None:
        clamped = max(0, min(10000, value))
        self._data.setdefault("tutor", {})["ocr_text_threshold"] = clamped
        self._schedule_save()

    @property
    def tutor_ocr_dpi(self) -> int:
        """Rasterization DPI for pages routed to OCR."""
        return max(72, min(300, self._data.get("tutor", {}).get("ocr_dpi", 150)))

    @tutor_ocr_dpi.setter
    def tutor_ocr_dpi(self, value: int) -> None:
        clamped = max(72, min(300, value))
        self._data.setdefault("tutor", {})["ocr_dpi"] = clamped
        self._schedule_save()

    @property
    def tutor_pdftoppm_bin(self) -> str:
        """Path to the poppler pdftoppm binary used for OCR rasterization."""
        return self._data.get("tutor", {}).get("pdftoppm_bin", "pdftoppm")

    @tutor_pdftoppm_bin.setter
    def tutor_pdftoppm_bin(self, value: str) -> None:
        self._data.setdefault("tutor", {})["pdftoppm_bin"] = value
        self._schedule_save()

    # ------------------------------------------------------------------
    # Parallel embeddings (Feature 006 — llama.cpp --parallel N)
    # ------------------------------------------------------------------

    @property
    def tutor_max_parallel_embed(self) -> int:
        """Max parallel embedding servers (1 = sequential, 2+ = parallel)."""
        return max(1, min(8, self._data.get("tutor", {}).get("max_parallel_embed", 1)))

    @tutor_max_parallel_embed.setter
    def tutor_max_parallel_embed(self, value: int) -> None:
        clamped = max(1, min(8, value))
        self._data.setdefault("tutor", {})["max_parallel_embed"] = clamped
        self._schedule_save()

    def get_tutor_config_snapshot(self) -> dict[str, Any]:
        """Get a snapshot of the current tutor config for session recording."""
        return {
            "enabled": self.tutor_enabled,
            "embedding_model": self.tutor_embedding_model,
            "tutor_model": self.tutor_model,
            "socratic": self.tutor_socratic,
            "level": self.tutor_level,
            "think": self.tutor_think,
            "top_k": self.tutor_top_k,
            "llm_provider": self.llm_provider,
            "llm_base_url": self.llm_base_url,
            "llm_api_key": self.llm_api_key,
            "whisper_binary": self.tutor_whisper_binary,
            "whisper_model": self.tutor_whisper_model,
            "llama_bin": self.tutor_llama_bin,
            "llama_models_dir": self.tutor_llama_models_dir,
            "embed_gguf": self.tutor_embed_gguf,
            "docling_gguf": self.tutor_docling_gguf,
            "docling_mmproj": self.tutor_docling_mmproj,
            "llm_gguf": self.tutor_llm_gguf,
            "llama_health_timeout_s": self.tutor_llama_health_timeout_s,
            "ocr_text_threshold": self.tutor_ocr_text_threshold,
            "ocr_dpi": self.tutor_ocr_dpi,
            "pdftoppm_bin": self.tutor_pdftoppm_bin,
            "max_parallel_embed": self.tutor_max_parallel_embed,
            "reranking_enabled": self.tutor_reranking_enabled,
        }
