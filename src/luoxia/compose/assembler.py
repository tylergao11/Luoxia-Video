from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.luoxia.timeline.freeze import assert_writable_for_render
from src.utils.system_check import get_ffmpeg_path


def assemble_episode(
    timeline: Dict[str, Any],
    *,
    output_path: Path | str,
    work_dir: Optional[Path | str] = None,
) -> Path:
    """Trim slack, mux TTS, burn subtitles, concat by target timeline."""
    assert_writable_for_render(timeline)
    for shot in timeline["shots"]:
        video = shot.get("video") or {}
        if video.get("has_audio_track") and not video.get("audio_stripped"):
            raise RuntimeError(
                f"{shot.get('shot_id')}: refuse compose with unstripped provider audio"
            )

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir or out.parent / "_compose")
    work.mkdir(parents=True, exist_ok=True)

    segment_paths: List[Path] = []
    for shot in timeline["shots"]:
        segment_paths.append(_build_segment(ffmpeg, shot, work))

    list_file = work / "concat.txt"
    with list_file.open("w", encoding="utf-8", newline="\n") as f:
        for p in segment_paths:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    result = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"concat failed: {result.stderr[-500:]}")

    timeline["phase"] = "rendered"
    return out


def _build_segment(ffmpeg: str, shot: Dict[str, Any], work: Path) -> Path:
    shot_id = shot["shot_id"]
    timing = shot["timing"]
    video = shot["video"]
    audio = shot.get("audio") or {}
    subtitle = shot.get("subtitle") or {}
    trim = timing.get("trim") or {}

    src = Path(video["local_path"])
    if not src.is_file():
        raise FileNotFoundError(f"{shot_id}: missing video {src}")

    target = float(timing["target_duration_s"])
    head = float(trim.get("head_s") or 0)
    # Consume from request video: skip head slack, keep target duration.
    seg = work / f"{shot_id}.seg.mp4"
    vf_parts = []
    # Optional subtitle burn-in for dialogue shots.
    srt = None
    if subtitle.get("text") and subtitle.get("start_s") is not None:
        srt = work / f"{shot_id}.srt"
        # Segment-local times: dialogue starts at lead_in within the trimmed segment.
        lead = float(timing.get("lead_in_s") or 0)
        measured = float((audio.get("measured_duration_s") or 0))
        _write_srt(srt, subtitle.get("text") or "", start=lead, end=lead + measured)
        # Escape path for ffmpeg subtitles filter on Windows.
        escaped = srt.resolve().as_posix().replace(":", "\\:")
        vf_parts.append(f"subtitles='{escaped}'")

    cmd = [ffmpeg, "-y", "-ss", str(head), "-i", str(src), "-t", str(target)]
    audio_path = audio.get("local_path")
    if audio_path and Path(audio_path).is_file():
        delay_ms = int(round(float(timing.get("lead_in_s") or 0) * 1000))
        cmd += ["-i", str(audio_path)]
        filter_complex = f"[1:a]adelay={delay_ms}|{delay_ms},apad,atrim=0:{target}[a]"
        if vf_parts:
            cmd += ["-filter_complex", f"[0:v]{','.join(vf_parts)}[v];{filter_complex}", "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-filter_complex", filter_complex, "-map", "0:v", "-map", "[a]"]
        cmd += ["-c:v", "libx264", "-c:a", "aac", "-shortest", str(seg)]
    else:
        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]
        cmd += ["-an", "-c:v", "libx264", str(seg)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"segment {shot_id} failed: {result.stderr[-500:]}")
    return seg


def _write_srt(path: Path, text: str, *, start: float, end: float) -> None:
    def ts(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h, rem = divmod(ms, 3600_000)
        m, rem = divmod(rem, 60_000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    content = f"1\n{ts(max(0.0, start))} --> {ts(max(start + 0.05, end))}\n{text}\n"
    path.write_text(content, encoding="utf-8")
