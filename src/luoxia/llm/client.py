from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extract a JSON object from an LLM reply that may wrap it in fences or prose."""
    if not text or not text.strip():
        raise ValueError("empty LLM response")
    content = text.strip()
    if "```" in content:
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if fence:
            content = fence.group(1).strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"LLM response is not JSON: {content[:240]}")
        data = json.loads(content[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object, got {type(data).__name__}")
    return data


class LuoxiaLLM:
    """Chat + JSON convenience over comic_gen.LLMAdapter (DashScope / OpenAI-compat)."""

    def __init__(self, adapter=None):
        if adapter is None:
            from src.apps.comic_gen.llm_adapter import LLMAdapter

            adapter = LLMAdapter()
        self.adapter = adapter

    @property
    def is_configured(self) -> bool:
        return bool(self.adapter.is_configured)

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        if not self.is_configured:
            raise RuntimeError(
                "LLM not configured. Set DASHSCOPE_API_KEY (default) or "
                "LLM_PROVIDER=openai with OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL."
            )
        response_format = {"type": "json_object"} if json_mode else None
        return self.adapter.chat(messages, model=model, response_format=response_format)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw = self.chat(messages, model=model, json_mode=True)
        return parse_json_object(raw)
