from __future__ import annotations

from typing import Any, Dict, Optional

from src.luoxia.beats.prompts import (
    STILL_PROMPT_SYSTEM,
    STILL_PROMPT_USER,
    VIDEO_MOTION_SYSTEM,
)
from src.luoxia.llm.client import LuoxiaLLM
from src.luoxia.timeline.video_policy import (
    ACTION_ARC_DURATION_TOLERANCE_S,
    video_acceptance_policy,
)


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
            if timeline.get("phase") in {"frozen", "rendering", "rendered"}:
                raise ValueError(
                    f"{shot.get('shot_id')}: still direction is frozen; unfreeze before editing"
                )
            old_still_prompt = still.get("prompt")
            new_still_prompt = _still_prompt(
                client,
                timeline,
                shot,
                cast_brief,
                strict=strict,
            )
            if force_still or old_still_prompt != new_still_prompt:
                _reset_still_render_state(still)
                _reset_video_render_state(video)
                request = video.setdefault("request", {})
            still["prompt"] = new_still_prompt
        needs_motion = force_motion or not request.get("prompt") or not video.get("direction")
        if needs_motion:
            if timeline.get("phase") in {"frozen", "rendering", "rendered"}:
                raise ValueError(
                    f"{shot.get('shot_id')}: motion direction is frozen; unfreeze before editing"
                )
            old_prompt = request.get("prompt")
            old_direction = video.get("direction")
            motion = _motion_contract(
                client,
                shot,
                still.get("prompt") or "",
            )
            if force_motion or old_prompt != motion["prompt"] or old_direction != motion["direction"]:
                _reset_video_render_state(video)
                request = video.setdefault("request", {})
            request["prompt"] = motion["prompt"]
            video["direction"] = motion["direction"]
            policy = video_acceptance_policy(timeline)
            video["acceptance"] = {
                "status": "pending",
                "checker": policy["checker"],
                "reasons": [],
            }
        still.setdefault(
            "aspect_ratio",
            (timeline.get("global") or {}).get("aspect_ratio") or "16:9",
        )
    return timeline


def _reset_video_render_state(video: Dict[str, Any]) -> None:
    """A changed direction invalidates the old provider result and its verdict."""
    checker = (video.get("acceptance") or {}).get("checker")
    video["status"] = "pending"
    video["attempts"] = 0
    video["has_audio_track"] = False
    video["audio_stripped"] = False
    for key in (
        "request_id",
        "source_url",
        "local_path",
        "fetched_at",
        "moderation_passed",
        "mode",
        "cost_usd",
        "delivered_duration_s",
        "required_duration_s",
        "error_code",
        "error",
    ):
        video.pop(key, None)
    video.pop("direction", None)
    video.setdefault("request", {}).pop("prompt", None)
    if checker:
        video["acceptance"] = {
            "status": "pending",
            "checker": checker,
            "reasons": [],
        }
    else:
        video.pop("acceptance", None)


def _reset_still_render_state(still: Dict[str, Any]) -> None:
    still["status"] = "pending"
    still["attempts"] = 0
    for key in ("asset_id", "local_path", "error"):
        still.pop(key, None)


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


def _motion_contract(
    client: LuoxiaLLM,
    shot: Dict[str, Any],
    still_prompt: str,
) -> Dict[str, Any]:
    if not client.is_configured:
        raise RuntimeError(
            f"{shot.get('shot_id')}: visual agent is not configured; "
            "refusing to substitute a generic motion prompt"
        )
    timing = shot.get("timing") or {}
    target_duration = timing.get("target_duration_s")
    if target_duration is None:
        raise ValueError(
            f"{shot.get('shot_id')}: target_duration_s missing before motion direction"
        )
    request = (shot.get("video") or {}).get("request") or {}
    allow_slow_motion = bool(request.get("allow_slow_motion"))
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
                        f"target_duration_s={target_duration}\n"
                        f"allow_slow_motion={str(allow_slow_motion).lower()}\n"
                        f"台词或动作语境={context}\n"
                        f"静帧={still_prompt}"
                    ),
                },
            ]
        )
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"{shot.get('shot_id')}: visual agent returned no motion prompt")
        direction = _validate_direction(
            data.get("direction"),
            shot_id=str(shot.get("shot_id") or "unknown_shot"),
            target_duration_s=float(target_duration),
            allow_slow_motion=allow_slow_motion,
        )
        return {"prompt": prompt, "direction": direction}
    except Exception as exc:
        raise RuntimeError(
            f"{shot.get('shot_id')}: motion prompt generation failed; "
            "refusing to substitute a generic prompt"
        ) from exc


def _validate_direction(
    value: Any,
    *,
    shot_id: str,
    target_duration_s: float,
    allow_slow_motion: bool,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{shot_id}: visual agent returned no direction contract")
    playback_speed = value.get("playback_speed")
    if playback_speed not in {"realtime", "slow_motion"}:
        raise ValueError(f"{shot_id}: invalid direction.playback_speed={playback_speed!r}")
    if playback_speed == "slow_motion" and not allow_slow_motion:
        raise ValueError(
            f"{shot_id}: slow_motion was not explicitly allowed by video.request"
        )

    camera = value.get("camera")
    if not isinstance(camera, dict):
        raise ValueError(f"{shot_id}: direction.camera missing")
    missing_camera = [
        key for key in ("kind", "speed", "path", "purpose")
        if not str(camera.get(key) or "").strip()
    ]
    if missing_camera:
        raise ValueError(
            f"{shot_id}: direction.camera missing " + ", ".join(missing_camera)
        )
    if camera.get("speed") not in {"slow", "normal", "fast"}:
        raise ValueError(f"{shot_id}: invalid camera.speed={camera.get('speed')!r}")

    arc = value.get("action_arc")
    if not isinstance(arc, list) or not 2 <= len(arc) <= 4:
        raise ValueError(f"{shot_id}: direction.action_arc must contain 2-4 phases")
    duration_sum = 0.0
    normalized_arc = []
    for index, item in enumerate(arc):
        if not isinstance(item, dict):
            raise ValueError(f"{shot_id}: action_arc[{index}] must be an object")
        phase = str(item.get("phase") or "").strip()
        action = str(item.get("action") or "").strip()
        try:
            duration = float(item.get("duration_s"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{shot_id}: action_arc[{index}].duration_s must be numeric"
            ) from exc
        if not phase or not action or duration <= 0:
            raise ValueError(
                f"{shot_id}: action_arc[{index}] requires phase, positive duration_s and action"
            )
        duration_sum += duration
        normalized_arc.append(
            {"phase": phase, "duration_s": duration, "action": action}
        )
    if abs(duration_sum - target_duration_s) > ACTION_ARC_DURATION_TOLERANCE_S:
        raise ValueError(
            f"{shot_id}: action_arc durations total {duration_sum:.3f}s, "
            f"target is {target_duration_s:.3f}s"
        )
    return {
        "playback_speed": playback_speed,
        "camera": {
            "kind": str(camera["kind"]).strip(),
            "speed": camera["speed"],
            "path": str(camera["path"]).strip(),
            "purpose": str(camera["purpose"]).strip(),
        },
        "action_arc": normalized_arc,
    }
