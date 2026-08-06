from __future__ import annotations

from typing import Any, Dict, Optional

from src.luoxia.beats.prompts import (
    STILL_PROMPT_SYSTEM,
    STILL_PROMPT_USER,
    VIDEO_MOTION_SYSTEM,
)
from src.luoxia.llm.client import LuoxiaLLM


def polish_timeline_prompts(
    timeline: Dict[str, Any],
    *,
    llm: Optional[LuoxiaLLM] = None,
    force_still: bool = False,
    force_motion: bool = False,
    strict: bool = False,
) -> Dict[str, Any]:
    """Fill still and motion prompts from the current, already-solved shot contract."""
    client = llm or LuoxiaLLM()
    cast_brief = "; ".join(
        f"{c.get('display_name')}({c.get('character_id')})" for c in (timeline.get("cast") or [])
    )
    for shot in timeline.get("shots") or []:
        still = shot.setdefault("still", {})
        video = shot.setdefault("video", {})
        request = video.setdefault("request", {})
        if force_still or not still.get("prompt"):
            still["prompt"] = _still_prompt(
                client,
                timeline,
                shot,
                cast_brief,
                strict=strict,
            )
        if force_motion or not request.get("prompt"):
            request["prompt"] = _motion_prompt(
                client,
                shot,
                still.get("prompt") or "",
                strict=strict,
            )
        still.setdefault(
            "aspect_ratio",
            (timeline.get("global") or {}).get("aspect_ratio") or "16:9",
        )
    return timeline


def _still_prompt(
    client: LuoxiaLLM,
    timeline: Dict[str, Any],
    shot: Dict[str, Any],
    cast_brief: str,
    *,
    strict: bool = False,
) -> str:
    seed = (shot.get("still") or {}).get("prompt") or ""
    dialogue = (shot.get("dialogue") or {}).get("text") or ""
    context = dialogue or (shot.get("subtitle") or {}).get("description") or shot.get("shot_id")
    if not client.is_configured:
        return seed or (
            f"{shot.get('shot_size') or 'medium'} shot, {context}, "
            "cinematic, widescreen 16:9"
        )
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
        if strict and not prompt:
            raise ValueError(f"{shot.get('shot_id')}: visual agent returned no still prompt")
        return prompt or seed or str(context)
    except Exception:
        if strict:
            raise
        return seed or f"{context}, cinematic lighting, widescreen composition"


def _motion_prompt(
    client: LuoxiaLLM,
    shot: Dict[str, Any],
    still_prompt: str,
    *,
    strict: bool = False,
) -> str:
    if not client.is_configured:
        return _motion_fallback(shot)
    timing = shot.get("timing") or {}
    context = (
        (shot.get("dialogue") or {}).get("text")
        or (shot.get("subtitle") or {}).get("description")
        or "无台词动作"
    )
    try:
        data = client.chat_json(
            [
                {"role": "system", "content": VIDEO_MOTION_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"shot_id={shot.get('shot_id')}\n"
                        f"shot_type={shot.get('type')}\n"
                        f"shot_size={shot.get('shot_size')}\n"
                        f"target_duration_s={timing.get('target_duration_s')}\n"
                        f"台词或动作语境={context}\n"
                        f"静帧={still_prompt}"
                    ),
                },
            ]
        )
        prompt = (data.get("prompt") or "").strip()
        if strict and not prompt:
            raise ValueError(f"{shot.get('shot_id')}: visual agent returned no motion prompt")
        return prompt or _motion_fallback(shot)
    except Exception:
        if strict:
            raise
        return _motion_fallback(shot)


def _motion_fallback(shot: Dict[str, Any]) -> str:
    duration = float((shot.get("timing") or {}).get("target_duration_s") or 0.0)
    shot_type = str(shot.get("type") or "action")
    end = f"在约 {duration:g} 秒内完成，单镜头无切换"
    if shot_type == "action":
        return (
            "人物从明确起势开始，脚步、重心、髋肩和手臂连续发力，"
            f"动作峰值后出现清楚的结果与回震；{end}"
        )
    if shot_type == "reaction":
        return f"人物从原表情清晰转为震惊或压抑，视线与肩颈同步变化，动作克制可读；{end}"
    if shot_type == "insert":
        return f"关键物体或能量细节快速发生一次明确变化，峰值后保留短暂余韵；{end}"
    if shot_type == "transition":
        return f"环境中的光影与空间层次自然变化，建立场景而不做无意义匀速漂移；{end}"
    return f"人物按台词情绪完成连续表情和肢体变化，背景角色保持克制但可见的反应；{end}"
