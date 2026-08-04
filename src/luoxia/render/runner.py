from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.luoxia.render.duration import require_request_duration
from src.luoxia.timeline.freeze import assert_writable_for_render
from src.luoxia.timeline.hashing import assert_timeline_hash
from src.luoxia.timeline.io import save_timeline
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
    timeline["phase"] = "rendering"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)

    for shot in timeline["shots"]:
        _render_one(timeline, shot, root)
        assert_timeline_hash(timeline)
        if timeline_path:
            save_timeline(timeline_path, timeline)

    return timeline


def _render_one(timeline: Dict[str, Any], shot: Dict[str, Any], root: Path) -> None:
    video = shot.setdefault("video", {})
    shot_id = shot["shot_id"]
    local_path = video.get("local_path")
    if local_path and Path(local_path).is_file() and video.get("audio_stripped"):
        video["status"] = "done"
        return

    request_id = video.get("request_id")
    duration = require_request_duration(timeline, shot_id)
    model_name = video.get("model")
    if not model_name:
        raise ValueError(f"{shot_id}: video.model missing")

    still = shot.get("still") or {}
    image_path = still.get("local_path")
    prompt = (video.get("request") or {}).get("prompt") or still.get("prompt") or ""
    resolution = (timeline.get("global") or {}).get("resolution") or "720p"

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

    video["status"] = "submitted" if not request_id else "polling"
    video["request"] = {
        **(video.get("request") or {}),
        "duration": duration,
        "resolution": resolution,
        "prompt": prompt,
    }

    try:
        image_url = None
        if image_path:
            # Prefer public URL if already http; else local path for adapter.
            image_url = image_path if str(image_path).startswith(("http://", "https://", "data:")) else str(Path(image_path).resolve())

        path, _elapsed = adapter.generate(
            prompt,
            str(out),
            duration=duration,
            resolution=resolution,
            image_url=image_url,
            request_id=request_id,
        )
        video.update(
            {
                "status": "done",
                "provider": video.get("provider") or "xai",
                "request_id": getattr(adapter, "last_request_id", request_id),
                "source_url": getattr(adapter, "last_source_url", video.get("source_url")),
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
        if video.get("has_audio_track") and not video.get("audio_stripped"):
            raise RuntimeError(f"{shot_id}: audio track present but not stripped; refuse compose")
        cost = timeline.setdefault("cost", {})
        if video.get("cost_usd") is not None:
            cost["actual_usd"] = round(float(cost.get("actual_usd") or 0) + float(video["cost_usd"]), 6)
    except Exception as exc:
        attempts += 1
        video["attempts"] = attempts
        video["status"] = "failed"
        code = getattr(exc, "code", None) or "internal_error"
        video["error_code"] = code
        video["error"] = str(exc)
        if code == "invalid_argument" or not getattr(exc, "retryable", True):
            raise
        if attempts >= MAX_ATTEMPTS:
            raise
