from __future__ import annotations

from typing import Any, Dict, Optional

from src.luoxia.beats.prompts import STILL_PROMPT_SYSTEM, STILL_PROMPT_USER, VIDEO_MOTION_SYSTEM
from src.luoxia.llm.client import LuoxiaLLM


def polish_timeline_prompts(
    timeline: Dict[str, Any],
    *,
    llm: Optional[LuoxiaLLM] = None,
) -> Dict[str, Any]:
    """Fill still.prompt and video.request.prompt for every shot that lacks them."""
    client = llm or LuoxiaLLM()
    cast_brief = "; ".join(
        f"{c.get('display_name')}({c.get('character_id')})" for c in (timeline.get("cast") or [])
    )
    for shot in timeline.get("shots") or []:
        still = shot.setdefault("still", {})
        video = shot.setdefault("video", {})
        request = video.setdefault("request", {})
        if not still.get("prompt"):
            still["prompt"] = _still_prompt(client, timeline, shot, cast_brief)
        if not request.get("prompt"):
            request["prompt"] = _motion_prompt(client, shot, still.get("prompt") or "")
        still.setdefault("aspect_ratio", (timeline.get("global") or {}).get("aspect_ratio") or "16:9")
    return timeline


def _still_prompt(client: LuoxiaLLM, timeline: Dict[str, Any], shot: Dict[str, Any], cast_brief: str) -> str:
    seed = (shot.get("still") or {}).get("prompt") or ""
    dialogue = (shot.get("dialogue") or {}).get("text") or ""
    context = dialogue or (shot.get("subtitle") or {}).get("description") or shot.get("shot_id")
    if not client.is_configured:
        return seed or f"{shot.get('shot_size') or 'medium'} shot, {context}, cinematic, widescreen 16:9"
    user = STILL_PROMPT_USER.format(
        cast_brief=cast_brief or "无",
        shot_id=shot.get("shot_id"),
        shot_type=shot.get("type"),
        shot_size=shot.get("shot_size"),
        scene_id=shot.get("scene_id"),
        context=context,
        seed_prompt=seed or "(空)",
    )
    try:
        data = client.chat_json(
            [
                {"role": "system", "content": STILL_PROMPT_SYSTEM},
                {"role": "user", "content": user},
            ]
        )
        prompt = (data.get("prompt") or "").strip()
        if data.get("negative_prompt"):
            shot.setdefault("still", {})["negative_prompt"] = data["negative_prompt"]
        return prompt or seed or str(context)
    except Exception:
        return seed or f"{context}, cinematic lighting, widescreen composition"


def _motion_prompt(client: LuoxiaLLM, shot: Dict[str, Any], still_prompt: str) -> str:
    if not client.is_configured:
        return "轻微呼吸感与缓慢推镜，人物微动，环境细节缓慢变化"
    try:
        data = client.chat_json(
            [
                {"role": "system", "content": VIDEO_MOTION_SYSTEM},
                {
                    "role": "user",
                    "content": f"shot_size={shot.get('shot_size')}\nstill={still_prompt}",
                },
            ]
        )
        return (data.get("prompt") or "").strip() or "缓慢推镜，人物微动"
    except Exception:
        return "缓慢推镜，人物微动"
