from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Optional external engine: (video_path, audio_path, out_path) -> out_path
LipsyncEngine = Callable[[str, str, str], str]


def apply_lipsync(
    timeline: Dict[str, Any],
    *,
    output_root: Path | str,
    engine: Optional[LipsyncEngine] = None,
) -> Dict[str, Any]:
    """Optional post-process. Failures never block episode assembly."""
    root = Path(output_root)
    for shot in timeline.get("shots") or []:
        lipsync = shot.get("lipsync") or {}
        if not lipsync.get("required"):
            lipsync["status"] = "skipped"
            shot["lipsync"] = lipsync
            continue
        if lipsync.get("status") == "done" and lipsync.get("local_path"):
            continue

        video = shot.get("video") or {}
        audio = shot.get("audio") or {}
        vpath = video.get("local_path")
        apath = audio.get("local_path")
        if not vpath or not apath:
            lipsync["status"] = "failed"
            lipsync["reason"] = "missing video/audio for lipsync"
            shot["lipsync"] = lipsync
            logger.warning("%s: lipsync skipped due to missing media", shot.get("shot_id"))
            continue

        out = root / "lipsync" / f"{shot['shot_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            if engine is None:
                raise RuntimeError("no lipsync engine configured")
            result = engine(str(vpath), str(apath), str(out))
            lipsync["status"] = "done"
            lipsync["local_path"] = result
            video["local_path"] = result
        except Exception as exc:
            lipsync["status"] = "failed"
            lipsync["reason"] = str(exc)
            logger.warning("%s: lipsync failed (non-blocking): %s", shot.get("shot_id"), exc)
        shot["lipsync"] = lipsync
    return timeline
