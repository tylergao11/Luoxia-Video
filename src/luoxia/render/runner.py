from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.luoxia.render.acceptance import VideoAcceptanceError, require_timeline_shot
from src.luoxia.render.duration import require_request_duration
from src.luoxia.timeline.freeze import assert_writable_for_render
from src.luoxia.timeline.hashing import assert_timeline_hash
from src.luoxia.timeline.io import save_timeline
from src.luoxia.timeline.transitions import plan_segments
from src.luoxia.timeline.validator import validate_timeline
from src.models.factory import ModelFactory


MAX_ATTEMPTS = 3


def render_timeline_videos(
    timeline: Dict[str, Any],
    *,
    output_root: Path | str,
    timeline_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Idempotent video render driven solely by timeline.json."""
    assert_writable_for_render(timeline)
    validate_timeline(timeline)
    timeline["phase"] = "rendering"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)

    for shot in timeline["shots"]:
        try:
            _render_one(timeline, shot, root)
            assert_timeline_hash(timeline)
        finally:
            if timeline_path:
                save_timeline(timeline_path, timeline)

    return timeline


def _render_one(timeline: Dict[str, Any], shot: Dict[str, Any], root: Path) -> None:
    video = shot.setdefault("video", {})
    shot_id = shot["shot_id"]
    request = video.get("request") or {}
    audio_mode = str(request.get("audio_mode") or "strip")
    _require_direction_contract(timeline, shot)
    prompt = str((video.get("request") or {}).get("prompt") or "").strip()
    if not prompt:
        message = f"{shot_id}: video.request.prompt missing; refuse render"
        video.update(
            {
                "status": "failed",
                "error_code": "missing_motion_prompt",
                "error": message,
            }
        )
        raise ValueError(message)
    request_id = video.get("request_id")
    duration = require_request_duration(timeline, shot_id)
    required_clip_duration = _required_clip_duration(timeline, shot_id)
    model_name = video.get("model")
    if not model_name:
        raise ValueError(f"{shot_id}: video.model missing")

    still = shot.get("still") or {}
    image_path = still.get("local_path")
    if not image_path:
        message = (
            f"{shot_id}: still.local_path missing; "
            "timeline video render requires an explicit first frame"
        )
        video.update(
            {
                "status": "failed",
                "error_code": "missing_first_frame",
                "error": message,
            }
        )
        raise ValueError(message)
    resolution = (timeline.get("global") or {}).get("resolution")
    if not resolution:
        raise ValueError(f"{shot_id}: global.resolution missing")
    provider = video.get("provider")
    if not provider:
        raise ValueError(f"{shot_id}: video.provider missing")

    out = root / "video" / f"{shot_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    adapter = ModelFactory.create_model(
        {
            "model.name": model_name,
            "model": {"name": model_name, "params": {"model_name": model_name}},
        }
    )

    attempts = int(video.get("attempts") or 0)
    error_code = video.get("error_code")
    if video.get("status") == "failed" and error_code == "invalid_argument":
        raise RuntimeError(f"{shot_id}: invalid_argument — fix params before retry")
    if attempts >= MAX_ATTEMPTS and video.get("status") == "failed":
        raise RuntimeError(f"{shot_id}: exceeded max attempts ({MAX_ATTEMPTS})")

    local_path = video.get("local_path")
    reusable_audio = video.get("audio_stripped") or audio_mode == "native_required"
    if local_path and Path(local_path).is_file() and reusable_audio:
        try:
            require_timeline_shot(
                timeline,
                shot,
                required_duration_s=required_clip_duration,
            )
            video.update({"status": "done", "error": None, "error_code": None})
            return
        except VideoAcceptanceError:
            attempts += 1
            video["attempts"] = attempts
            # Polling the old provider request would only fetch the same rejected take.
            video["request_id"] = None
            video["source_url"] = None
            request_id = None
            if attempts >= MAX_ATTEMPTS:
                raise

    video["status"] = "submitted" if not request_id else "polling"
    video["request"] = {
        **request,
        "duration": duration,
        "resolution": resolution,
        "prompt": prompt,
    }

    try:
        # Prefer public URL if already http; else local path for adapter.
        image_url = (
            image_path
            if str(image_path).startswith(("http://", "https://", "data:"))
            else str(Path(image_path).resolve())
        )

        path, _elapsed = adapter.generate(
            prompt,
            str(out),
            duration=duration,
            resolution=resolution,
            image_url=image_url,
            request_id=request_id,
            audio_mode=audio_mode,
        )
        video.update(
            {
                "status": "polling",
                "provider": provider,
                "request_id": getattr(adapter, "last_request_id", request_id),
                # Provider download URLs are short-lived receipts and may carry signed
                # query data. Once the file is local, they must not enter episode truth.
                "source_url": None,
                "local_path": path,
                "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "has_audio_track": bool(getattr(adapter, "last_has_audio_track", False)),
                "audio_stripped": bool(getattr(adapter, "last_audio_stripped", False)),
                "moderation_passed": getattr(adapter, "last_moderation_passed", True),
                "mode": getattr(adapter, "last_mode", None),
                "cost_usd": getattr(adapter, "last_cost_usd", None),
                "error": None,
                "error_code": None,
            }
        )
        if (
            video.get("has_audio_track")
            and not video.get("audio_stripped")
            and audio_mode != "native_required"
        ):
            raise RuntimeError(f"{shot_id}: audio track present but not stripped; refuse compose")
        cost = timeline.setdefault("cost", {})
        if video.get("cost_usd") is not None:
            cost["actual_usd"] = round(float(cost.get("actual_usd") or 0) + float(video["cost_usd"]), 6)
        require_timeline_shot(
            timeline,
            shot,
            required_duration_s=required_clip_duration,
        )
        video.update({"status": "done", "error": None, "error_code": None})
    except Exception as exc:
        attempts += 1
        video["attempts"] = attempts
        video["status"] = "failed"
        code = getattr(exc, "code", None) or "internal_error"
        video["error_code"] = code
        video["error"] = str(exc)
        if code in {"short_clip", "quality_rejected"}:
            video["request_id"] = None
            video["source_url"] = None
        # Retryable means a future run may submit a new take. This run still failed.
        raise


def _require_direction_contract(timeline: Dict[str, Any], shot: Dict[str, Any]) -> None:
    if timeline.get("schema_version") != "1.3.0":
        return
    video = shot.setdefault("video", {})
    direction = video.get("direction")
    if not isinstance(direction, dict):
        message = f"{shot.get('shot_id')}: video.direction missing; refuse render"
        video.update(
            {"status": "failed", "error_code": "missing_video_direction", "error": message}
        )
        raise ValueError(message)
    request = video.get("request") or {}
    if direction.get("playback_speed") == "slow_motion" and not request.get(
        "allow_slow_motion"
    ):
        message = (
            f"{shot.get('shot_id')}: slow_motion requires "
            "video.request.allow_slow_motion=true"
        )
        video.update(
            {"status": "failed", "error_code": "forbidden_slow_motion", "error": message}
        )
        raise ValueError(message)


def _required_clip_duration(timeline: Dict[str, Any], shot_id: str) -> float:
    for plan in plan_segments(timeline):
        if plan.shot.get("shot_id") == shot_id:
            return float(plan.segment_duration_s)
    raise ValueError(f"{shot_id}: no segment plan found")
