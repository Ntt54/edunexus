"""Pleias RAG provider via Ollama /api/generate raw mode."""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

_DEFAULT_MODEL = "hf.co/brendanddev/Pleias-RAG-1B-Q4_K_M-GGUF:Q4_K_M"
_OPTS: dict[str, Any] = {"temperature": 0.0, "top_p": 0.95, "num_ctx": 10000, "num_predict": 1200, "repeat_penalty": 1.0, "stop": ["#"]}

def _extract(raw: str, name: str) -> str:
    m = re.search(rf"<\|{name}_start\|>(.*?)<\|{name}_end\|>", raw, re.DOTALL)
    return m.group(1).strip() if m else ""

_CIT = re.compile(r'<ref\s+name="<\|source_id\|>(\d+)">([^<]*)</ref>', re.DOTALL)
_CIT2 = re.compile(r'<ref\s+name="<\|source_id_start\|>(\d+)<\|source_id_end\|>">([^<]*)</ref>', re.DOTALL)

def parse_pleias_response(raw: str) -> dict[str, Any]:
    language = _extract(raw, "language")
    query_report = _extract(raw, "query_report")
    source_analysis = _extract(raw, "source_analysis")
    draft = _extract(raw, "draft")
    answer = _extract(raw, "answer") or raw
    citations: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for pat in (_CIT, _CIT2):
        for m in pat.finditer(raw):
            sid, cited = int(m.group(1)), m.group(2).strip()
            if (sid, cited) not in seen:
                seen.add((sid, cited))
                citations.append({"source_id": sid, "cited_text": cited, "supported_text": cited})
    clean = _CIT.sub(lambda m: f"[{m.group(1)}]", answer)
    clean = _CIT2.sub(lambda m: f"[{m.group(1)}]", clean)
    clean = re.sub(r"<\|source_id_start\|>(\d+)<\|source_id_end\|>", lambda m: f"[{m.group(1)}]", clean)
    return {"language": language, "query_report": query_report, "source_analysis": source_analysis, "draft": draft, "answer": answer, "citations": citations, "clean_answer": clean.strip()}

class PleiasRAGProvider:
    def __init__(self, client: Any, model: str = _DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model
    async def generate(self, pleias_prompt: str, stream: bool = False) -> str:
        http = self._client._get_client()
        payload: dict[str, Any] = {"model": self._model, "prompt": pleias_prompt, "raw": True, "stream": False, "options": dict(_OPTS)}
        resp = await http.post("/api/generate", json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Pleias error {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            if isinstance(data, dict) and "response" in data:
                return data["response"]
        except Exception:
            pass
        text = resp.text or ""
        parts: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("response"):
                    parts.append(obj["response"])
            except Exception:
                continue
        return "".join(parts) if parts else text
    async def stream(self, pleias_prompt: str) -> AsyncIterator[dict[str, Any]]:
        http = self._client._get_client()
        payload: dict[str, Any] = {"model": self._model, "prompt": pleias_prompt, "raw": True, "stream": True, "options": dict(_OPTS)}
        async with http.stream("POST", "/api/generate", json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"Pleias stream {resp.status_code}: {body.decode()[:200]}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                txt = chunk.get("response") or ""
                if txt:
                    yield {"type": "delta", "text": txt}
                if chunk.get("done"):
                    break
        yield {"type": "end"}
