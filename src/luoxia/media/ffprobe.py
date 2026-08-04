from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def resolve_ffprobe_path() -> Optional[str]:
    """Locate ffprobe, preferring a sibling of the project's ffmpeg resolver."""
    from src.utils.system_check import get_ffmpeg_path

    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        sibling = _sibling_ffprobe(ffmpeg)
        if sibling:
            return sibling

    which = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if which:
        return which

    if platform.system() == "Windows":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffprobe.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin" / "ffprobe.exe",
            Path(r"C:\ffmpeg\bin\ffprobe.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def _sibling_ffprobe(ffmpeg_path: str) -> Optional[str]:
    parent = Path(ffmpeg_path).parent
    name = "ffprobe.exe" if platform.system() == "Windows" else "ffprobe"
    candidate = parent / name
    if candidate.is_file():
        return str(candidate)
    return None


def measure_media_duration_s(path: str | Path, *, ffprobe_path: Optional[str] = None) -> float:
    """Return media duration in seconds via ffprobe. No estimation allowed."""
    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(f"media file not found: {media}")

    probe = ffprobe_path or resolve_ffprobe_path()
    if not probe:
        raise RuntimeError(
            "ffprobe not found. Install FFmpeg with ffprobe on PATH; "
            "audio duration must be measured, never estimated."
        )

    result = subprocess.run(
        [
            probe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(media),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media}: {result.stderr.strip()}")

    payload = json.loads(result.stdout or "{}")
    duration = payload.get("format", {}).get("duration")
    if duration is None:
        raise RuntimeError(f"ffprobe returned no duration for {media}")
    value = float(duration)
    if value < 0:
        raise RuntimeError(f"invalid negative duration for {media}: {value}")
    return value


def measure_video_size(path: str | Path, *, ffprobe_path: Optional[str] = None) -> tuple[int, int]:
    """Return (width, height) of the first video stream.

    Subtitle geometry must follow the real frame, not what the timeline claims the
    resolution is, or margins land in the wrong place when a provider returns an
    off-spec size.
    """
    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(f"media file not found: {media}")

    probe = ffprobe_path or resolve_ffprobe_path()
    if not probe:
        raise RuntimeError("ffprobe not found; cannot measure video size")

    result = subprocess.run(
        [
            probe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(media),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media}: {result.stderr.strip()}")

    streams = json.loads(result.stdout or "{}").get("streams") or []
    if not streams or not streams[0].get("width") or not streams[0].get("height"):
        raise RuntimeError(f"ffprobe returned no video size for {media}")
    return int(streams[0]["width"]), int(streams[0]["height"])
