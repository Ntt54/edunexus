"""Voice input for the local AI tutor (US8 "Talk to the tutor").

Pure stdlib only: this module must stay free of any textual/fastapi imports so
that ``src/ollama_tutor/tutor/`` remains UI-framework-free (contracts/
tutor-core-api.md invariant 1, enforced by tests/contract/test_tutor_imports.py).

It wraps a local ``whisper.cpp`` binary (``whisper-cli`` / ``main``) invoked as a
subprocess. The transcript is written by whisper.cpp to a sidecar
``<wav_path>.txt`` file which we read back.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path


class VoiceError(Exception):
    """Raised when voice transcription fails (config, decode, or whisper)."""


class WhisperTranscriber:
    """Thin async wrapper around a local whisper.cpp binary."""

    def __init__(self, binary: str, model: str) -> None:
        self.binary = binary
        self.model = model

    @property
    def available(self) -> bool:
        """True when a binary + model are configured and the binary resolves."""
        return bool(
            self.binary
            and self.model
            and (os.path.exists(self.binary) or shutil.which(self.binary))
        )

    async def transcribe_wav(self, wav_path: str, runner=None) -> str:
        """Transcribe a WAV file via whisper.cpp.

        ``runner`` is injectable for testing; it defaults to
        ``asyncio.create_subprocess_exec`` and must accept the argv plus
        ``stdout``/``stderr`` pipes and return a process whose ``communicate()``
        yields ``(stdout, stderr)``.

        whisper.cpp writes the transcript to a sidecar ``<wav_path>.txt``; we
        read and return its stripped contents.
        """
        if runner is None:
            runner = asyncio.create_subprocess_exec

        argv = [
            self.binary,
            "-m", self.model,
            "-f", wav_path,
            "-nt",          # no timestamps
            "-l", "fr",     # language: French
            "-otxt",        # output transcript to a .txt sidecar
        ]

        proc = await runner(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise VoiceError(stderr.decode("utf-8", "replace")[:500])

        sidecar = f"{wav_path}.txt"
        if not os.path.exists(sidecar):
            raise VoiceError("no transcript output")
        return Path(sidecar).read_text(encoding="utf-8", errors="replace").strip()
