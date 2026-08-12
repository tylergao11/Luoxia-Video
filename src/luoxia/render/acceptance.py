from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.luoxia.media.ffprobe import measure_media_duration_s
from src.luoxia.timeline.video_policy import (
    validate_video_acceptance_policy,
    video_acceptance_policy,
)
from src.utils.system_check import get_ffmpeg_path


_FREEZE_EVENT = re.compile(
    r"lavfi\.freezedetect\.(freeze_start|freeze_duration|freeze_end):\s*"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))"
)


class VideoAcceptanceError(RuntimeError):
    """A generated clip failed the timeline's objective acceptance contract."""

    retryable = True

    def __init__(self, shot_id: str, acceptance: Dict[str, Any]):
        self.shot_id = shot_id
        self.acceptance = acceptance
        reasons = list(acceptance.get("reasons") or ["video acceptance failed"])
        self.code = "short_clip" if reasons and all(
            reason.startswith("short_clip:") for reason in reasons
        ) else "quality_rejected"
        super().__init__(f"{shot_id}: " + "; ".join(reasons))


def evaluate_video_file(
    path: str | Path,
    *,
    required_duration_s: float,
    policy: Dict[str, Any],
    start_s: float = 0.0,
) -> Dict[str, Any]:
    """Verify that the clip exists, decodes, and covers the required window."""
    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(f"video file not found: {media}")

    required = float(required_duration_s)
    if required <= 0:
        raise ValueError("required_duration_s must be > 0")
    policy = validate_video_acceptance_policy(policy)
    start = max(0.0, float(start_s))
    delivered = measure_media_duration_s(media)
    available = max(0.0, delivered - start)
    tolerance = float(policy["duration_tolerance_s"])
    reasons: List[str] = []
    if required - available > tolerance:
        reasons.append(
            f"short_clip: delivered {available:.3f}s, required {required:.3f}s"
        )
    return {
        "status": "failed" if reasons else "passed",
        "checker": str(policy["checker"]),
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "policy": dict(policy),
        "observed": {
            "delivered_duration_s": round(available, 3),
            "required_duration_s": round(required, 3),
            "max_freeze_segment_s": 0.0,
            "freeze_segments": [],
        },
        "reasons": reasons,
    }


def review_timeline_shot(
    timeline: Dict[str, Any],
    shot: Dict[str, Any],
    *,
    required_duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Review one shot and persist both the verdict and its measured evidence."""
    shot_id = str(shot.get("shot_id") or "unknown_shot")
    video = shot.setdefault("video", {})
    path = video.get("local_path")
    if not path:
        raise FileNotFoundError(f"{shot_id}: video.local_path missing")
    timing = shot.get("timing") or {}
    trim = timing.get("trim") or {}
    required = (
        float(required_duration_s)
        if required_duration_s is not None
        else float(timing["target_duration_s"])
    )
    acceptance = evaluate_video_file(
        path,
        required_duration_s=required,
        policy=video_acceptance_policy(timeline),
        start_s=float(trim.get("head_s") or 0.0),
    )
    video["acceptance"] = acceptance
    observed = acceptance["observed"]
    video["delivered_duration_s"] = observed["delivered_duration_s"]
    video["required_duration_s"] = observed["required_duration_s"]
    if acceptance["status"] == "failed":
        error = VideoAcceptanceError(shot_id, acceptance)
        video["status"] = "failed"
        video["error_code"] = error.code
        video["error"] = str(error)
    else:
        video["status"] = "done"
        if video.get("error_code") in {"short_clip", "quality_rejected"}:
            video["error_code"] = None
            video["error"] = None
    return acceptance


def require_timeline_shot(
    timeline: Dict[str, Any],
    shot: Dict[str, Any],
    *,
    required_duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    acceptance = review_timeline_shot(
        timeline,
        shot,
        required_duration_s=required_duration_s,
    )
    if acceptance["status"] != "passed":
        raise VideoAcceptanceError(str(shot.get("shot_id") or "unknown_shot"), acceptance)
    return acceptance


def _detect_freezes(
    path: Path,
    *,
    start_s: float,
    window_s: float,
    noise_db: float,
    minimum_s: float,
) -> List[Dict[str, float]]:
    if window_s <= 0:
        return []
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; video acceptance cannot run")

    filters = (
        f"trim=start={_fmt(start_s)}:duration={_fmt(window_s)},"
        "setpts=PTS-STARTPTS,"
        f"freezedetect=n={_fmt(noise_db)}dB:d={_fmt(minimum_s)}"
    )
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            filters,
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg freezedetect failed: {result.stderr[-500:]}")
    return _parse_freeze_events(result.stderr, window_s=window_s, minimum_s=minimum_s)


def _parse_freeze_events(
    output: str,
    *,
    window_s: float,
    minimum_s: float,
) -> List[Dict[str, float]]:
    segments: List[Dict[str, float]] = []
    current_start: Optional[float] = None
    current_duration: Optional[float] = None
    for match in _FREEZE_EVENT.finditer(output):
        event, raw_value = match.groups()
        value = float(raw_value)
        if event == "freeze_start":
            current_start = value
            current_duration = None
        elif event == "freeze_duration" and current_start is not None:
            current_duration = value
        elif event == "freeze_end" and current_start is not None:
            duration = current_duration if current_duration is not None else value - current_start
            _append_segment(segments, current_start, value, duration, minimum_s)
            current_start = None
            current_duration = None

    # FFmpeg has no later frame on which to emit freeze_end for a freeze that reaches EOF.
    if current_start is not None:
        duration = max(0.0, float(window_s) - current_start)
        _append_segment(segments, current_start, float(window_s), duration, minimum_s)
    return segments


def _append_segment(
    segments: List[Dict[str, float]],
    start: float,
    end: float,
    duration: float,
    minimum_s: float,
) -> None:
    if duration + 1e-6 < minimum_s:
        return
    segments.append(
        {
            "start_s": round(max(0.0, start), 3),
            "end_s": round(max(start, end), 3),
            "duration_s": round(max(0.0, duration), 3),
        }
    )


def _fmt(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
