from __future__ import annotations

from typing import Any, Dict, Optional

from src.luoxia.beats.prompts import REWRITE_SYSTEM, REWRITE_USER_TEMPLATE
from src.luoxia.llm.client import LuoxiaLLM

CHARS_PER_SECOND = 4.5


def make_rewrite_fn(llm: Optional[LuoxiaLLM] = None):
    """Return a solver-compatible rewrite(text, budget_s, shot) -> str."""
    client = llm or LuoxiaLLM()

    def rewrite(text: str, budget_s: float, shot: Dict[str, Any]) -> str:
        dialogue = shot.get("dialogue") or {}
        budget_chars = max(4, int(budget_s * CHARS_PER_SECOND))
        # Local fallback when LLM unavailable: hard truncate by punctuation.
        if not client.is_configured:
            return _local_compress(text, budget_chars)
        user = REWRITE_USER_TEMPLATE.format(
            budget_s=budget_s,
            budget_chars=budget_chars,
            character_id=dialogue.get("character_id") or "",
            emotion=dialogue.get("emotion") or "",
            text=text,
        )
        try:
            out = client.chat(
                [
                    {"role": "system", "content": REWRITE_SYSTEM},
                    {"role": "user", "content": user},
                ]
            ).strip()
        except Exception:
            return _local_compress(text, budget_chars)
        out = out.strip().strip("「」\"'“”")
        if not out:
            return _local_compress(text, budget_chars)
        if len(out) > budget_chars + 8:
            out = _local_compress(out, budget_chars)
        return out

    return rewrite


def _local_compress(text: str, budget_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= budget_chars:
        return text
    cut = text[:budget_chars]
    for sep in ("。", "！", "？", "；", "，", ",", " "):
        idx = cut.rfind(sep)
        if idx >= max(2, budget_chars // 3):
            return cut[: idx + 1]
    return cut.rstrip("，,、") + "。"
