from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Optional external engine: (video_path, audio_path, out_path) -> out_path
LipsyncEngine = Callable[[str, str, str], str]


class LipsyncError(RuntimeError):
    """A required dialogue shot could not be lip-synced."""


def apply_lipsync(
    timeline: Dict[str, Any],
    *,
    output_root: Path | str,
    engine: Optional[LipsyncEngine] = None,
) -> Dict[str, Any]:
    """Apply audio-driven mouth motion to every shot that requires it.

    ``lipsync.required`` is a delivery contract, not a hint.  A missing input or an
    engine failure aborts the pass so assembly cannot silently publish the original
    non-speaking video.
    """
    root = Path(output_root)
    resolved_engine = engine
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
            raise LipsyncError(
                f"{shot.get('shot_id')}: required lipsync is missing video/audio"
            )

        out = root / "lipsync" / f"{shot['shot_id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            if resolved_engine is None:
                from .musetalk import resolve_musetalk_engine

                resolved_engine = resolve_musetalk_engine()
            result = resolved_engine(str(vpath), str(apath), str(out))
            lipsync["status"] = "done"
            lipsync["local_path"] = result
            video["local_path"] = result
        except Exception as exc:
            lipsync["status"] = "failed"
            lipsync["reason"] = str(exc)
            shot["lipsync"] = lipsync
            if isinstance(exc, LipsyncError):
                raise
            raise LipsyncError(f"{shot.get('shot_id')}: required lipsync failed: {exc}") from exc
        shot["lipsync"] = lipsync
    return timeline
