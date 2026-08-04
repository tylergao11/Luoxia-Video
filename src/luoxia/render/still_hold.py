from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict

from src.luoxia.render.duration import require_request_duration
from src.utils.system_check import get_ffmpeg_path


def render_still_hold_videos(timeline: Dict[str, Any], *, output_root: Path | str) -> Dict[str, Any]:
    """Offline video path: hold each still for request_duration_s (no cloud video API)."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg required for still-hold video mode")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)

    for shot in timeline["shots"]:
        video = shot.setdefault("video", {})
        out = root / "video" / f"{shot['shot_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_file() and video.get("status") == "done" and video.get("audio_stripped"):
            continue

        still_path = (shot.get("still") or {}).get("local_path")
        if not still_path or not Path(still_path).is_file():
            raise FileNotFoundError(f"{shot['shot_id']}: still required for still-hold mode")

        duration = require_request_duration(timeline, shot["shot_id"])
        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(still_path),
            "-t",
            str(duration),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"still-hold {shot['shot_id']} failed: {result.stderr[-400:]}")

        video.update(
            {
                "status": "done",
                "provider": "still_hold",
                "model": "ffmpeg-still-hold",
                "local_path": str(out),
                "has_audio_track": False,
                "audio_stripped": True,
                "request": {
                    **(video.get("request") or {}),
                    "duration": duration,
                    "mode": "still_hold",
                },
                "error": None,
                "error_code": None,
            }
        )
    timeline["phase"] = "rendering"
    return timeline
