from __future__ import annotations

from typing import Any, Dict, Optional


def require_request_duration(timeline: Dict[str, Any], shot_id: str) -> int:
    """Single source of truth for video request duration: timeline.json only."""
    shot = _find_shot(timeline, shot_id)
    timing = shot.get("timing") or {}
    if "request_duration_s" not in timing:
        raise KeyError(f"shot {shot_id} missing timing.request_duration_s in timeline")
    value = timing["request_duration_s"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(
            f"shot {shot_id} timing.request_duration_s must be int from timeline, got {type(value).__name__}"
        )
    return value


def require_target_duration(timeline: Dict[str, Any], shot_id: str) -> float:
    shot = _find_shot(timeline, shot_id)
    timing = shot.get("timing") or {}
    if "target_duration_s" not in timing:
        raise KeyError(f"shot {shot_id} missing timing.target_duration_s in timeline")
    return float(timing["target_duration_s"])


def _find_shot(timeline: Dict[str, Any], shot_id: str) -> Dict[str, Any]:
    for shot in timeline.get("shots") or []:
        if shot.get("shot_id") == shot_id:
            return shot
    raise KeyError(f"shot_id not found in timeline: {shot_id}")


def optional_shot_id_from_frame(frame_id: Optional[str]) -> Optional[str]:
    return frame_id
