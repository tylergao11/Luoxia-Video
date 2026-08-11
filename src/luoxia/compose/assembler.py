from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.luoxia.compose.subtitles import (
    build_cues,
    resolve_position,
    resolve_style,
    shot_subtitle_window,
    style_for_frame,
    write_ass,
)
from src.luoxia.media.geometry import frame_size
from src.luoxia.render.acceptance import require_timeline_shot
from src.luoxia.timeline.freeze import assert_writable_for_render
from src.luoxia.timeline.transitions import (
    SegmentPlan,
    needs_filter_graph,
    plan_segments,
    total_duration_s,
)
from src.luoxia.timeline.validator import validate_timeline
from src.utils.system_check import get_ffmpeg_path


def assemble_episode(
    timeline: Dict[str, Any],
    *,
    output_path: Path | str,
    work_dir: Optional[Path | str] = None,
) -> Path:
    """Trim slack, mux TTS, burn subtitles, apply transitions, concat by target timeline."""
    assert_writable_for_render(timeline)
    validate_timeline(timeline)
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

    style = resolve_style(timeline)
    plans = plan_segments(timeline)
    for plan in plans:
        require_timeline_shot(
            timeline,
            plan.shot,
            required_duration_s=plan.segment_duration_s,
        )
    fps = int((timeline.get("global") or {}).get("fps") or 24)
    # Providers round sizes their own way — asking grok for 1080p returns 1920x1088 — and
    # the concat demuxer copies streams, so segments that disagree on size or fps produce a
    # broken master. Normalise every segment to the size the contract declares.
    frame = frame_size(timeline)

    segments: List[Path] = [
        _build_segment(ffmpeg, plan, work, style=style, frame=frame, fps=fps) for plan in plans
    ]

    if needs_filter_graph(plans):
        _join_with_transitions(ffmpeg, segments, plans, out=out, fps=fps)
    else:
        _concat_copy(ffmpeg, segments, work=work, out=out)

    timeline["phase"] = "rendered"
    validate_timeline(timeline)
    return out


def _concat_copy(ffmpeg: str, segments: List[Path], *, work: Path, out: Path) -> None:
    list_file = work / "concat.txt"
    with list_file.open("w", encoding="utf-8", newline="\n") as f:
        for p in segments:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    result = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"concat failed: {result.stderr[-500:]}")


def _join_with_transitions(
    ffmpeg: str,
    segments: List[Path],
    plans: List[SegmentPlan],
    *,
    out: Path,
    fps: int,
) -> None:
    """Chain segments with xfade at dissolve boundaries and concat at cuts.

    The dissolve overlap is covered by the outgoing segment's extension, so the joined
    length still equals the sum of target_duration_s and the audio concat stays aligned.
    """
    cmd = [ffmpeg, "-y"]
    for seg in segments:
        cmd += ["-i", str(seg)]

    steps: List[str] = []
    for i, plan in enumerate(plans):
        steps.append(f"[{i}:v]fps={fps},format=yuv420p,setsar=1,settb=AVTB[v{i}]")

    current = "[v0]"
    current_len = plans[0].segment_duration_s
    for i in range(1, len(plans)):
        overlap = plans[i].dissolve_in_s
        label = f"[j{i}]"
        if overlap > 0:
            offset = max(0.0, current_len - overlap)
            steps.append(
                f"{current}[v{i}]xfade=transition=fade"
                f":duration={_fmt(overlap)}:offset={_fmt(offset)}{label}"
            )
            current_len = current_len + plans[i].segment_duration_s - overlap
        else:
            steps.append(f"{current}[v{i}]concat=n=2:v=1:a=0{label}")
            current_len += plans[i].segment_duration_s
        current = label

    audio_inputs = "".join(f"[{i}:a]" for i in range(len(plans)))
    steps.append(f"{audio_inputs}concat=n={len(plans)}:v=0:a=1[aout]")

    cmd += [
        "-filter_complex", ";".join(steps),
        "-map", current,
        "-map", "[aout]",
        "-t", _fmt(total_duration_s(plans)),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"transition join failed: {result.stderr[-500:]}")


def _build_segment(
    ffmpeg: str,
    plan: SegmentPlan,
    work: Path,
    *,
    style: Dict[str, Any],
    frame: tuple[int, int],
    fps: int,
) -> Path:
    shot = plan.shot
    shot_id = shot["shot_id"]
    timing = shot["timing"]
    video = shot["video"]
    audio = shot.get("audio") or {}
    trim = timing.get("trim") or {}

    src = Path(video["local_path"])
    if not src.is_file():
        raise FileNotFoundError(f"{shot_id}: missing video {src}")

    target = plan.duration_s
    seg_len = plan.segment_duration_s
    head = float(trim.get("head_s") or 0)
    seg = work / f"{shot_id}.seg.mp4"

    width, height = frame
    vf_parts: List[str] = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={width}:{height}",
        f"fps={fps}",
        "setsar=1",
    ]

    ass = _write_shot_ass(shot, work, style=style, frame=frame)
    if ass is not None:
        escaped = ass.resolve().as_posix().replace(":", "\\:")
        vf_parts.append(f"ass='{escaped}'")

    if plan.fade_in_s > 0:
        vf_parts.append(
            f"fade=t=in:st=0:d={_fmt(plan.fade_in_s)}:color={plan.fade_in_color}"
        )
    if plan.fade_out_s > 0:
        st = max(0.0, target - plan.fade_out_s)
        vf_parts.append(
            f"fade=t=out:st={_fmt(st)}:d={_fmt(plan.fade_out_s)}:color={plan.fade_out_color}"
        )

    # -ss/-t must precede -i, otherwise they bind to the next input instead of this one
    # and the segment comes out request_duration_s long instead of target_duration_s.
    cmd = [ffmpeg, "-y", "-ss", str(head), "-t", _fmt(seg_len), "-i", str(src)]

    # Every segment carries an audio stream (silence when the shot has no line), so the
    # concat demuxer sees a uniform stream layout.
    audio_path = audio.get("local_path")
    has_audio_file = bool(audio_path) and Path(audio_path).is_file()
    if not has_audio_file and _expects_speech(shot):
        raise FileNotFoundError(
            f"{shot_id}: shot has a line but no audio file at {audio_path!r}; "
            "refusing to compose it as silence"
        )
    if has_audio_file:
        cmd += ["-i", str(audio_path)]
        delay_ms = int(round(float(timing.get("lead_in_s") or 0) * 1000))
        audio_chain = f"[1:a]adelay={delay_ms}|{delay_ms},apad,atrim=0:{_fmt(target)}[a]"
    else:
        cmd += ["-f", "lavfi", "-t", _fmt(target), "-i", "anullsrc=r=44100:cl=stereo"]
        audio_chain = f"[1:a]atrim=0:{_fmt(target)}[a]"

    video_chain = f"[0:v]{','.join(vf_parts)}[v]" if vf_parts else "[0:v]copy[v]"
    cmd += [
        "-filter_complex", f"{video_chain};{audio_chain}",
        "-map", "[v]",
        "-map", "[a]",
        "-t", _fmt(seg_len),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(seg),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"segment {shot_id} failed: {result.stderr[-500:]}")
    return seg


def _expects_speech(shot: Dict[str, Any]) -> bool:
    """Whether this shot is supposed to carry a spoken line.

    Silent shots legitimately get a generated silent track so concat sees a uniform
    layout; a dialogue shot must not, or the line just vanishes from the episode.
    """
    dialogue = shot.get("dialogue") or {}
    return bool(dialogue.get("text")) or shot.get("timing_driver") == "audio"


def short_clips(timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Shots whose provider clip was shorter than the timeline required."""
    rejected: List[Dict[str, Any]] = []
    for shot in timeline.get("shots") or []:
        video = shot.get("video") or {}
        reasons = list((video.get("acceptance") or {}).get("reasons") or [])
        delivered = video.get("delivered_duration_s")
        required = video.get("required_duration_s")
        # Read old artifacts without continuing their freeze-padding behaviour.
        if delivered is None:
            delivered = video.get("padded_from_s")
        if required is None:
            required = video.get("padded_to_s")
        if delivered is None:
            continue
        is_short = any(reason.startswith("short_clip:") for reason in reasons)
        if not is_short and required is not None:
            is_short = float(delivered) < float(required)
        if not is_short:
            continue
        rejected.append(
            {
                "shot_id": shot.get("shot_id"),
                "delivered_s": delivered,
                "required_s": required,
            }
        )
    return rejected


def _write_shot_ass(
    shot: Dict[str, Any],
    work: Path,
    *,
    style: Dict[str, Any],
    frame: tuple[int, int],
) -> Optional[Path]:
    """Subtitle geometry follows the frame the segment is normalised to, so PlayRes matches
    the pixels libass draws into and margins land where the style asked."""
    window = shot_subtitle_window(shot)
    if window is None:
        return None

    width, height = frame
    frame_style = style_for_frame(style, width, height)
    start, end = window
    cues = build_cues(
        (shot.get("subtitle") or {}).get("text") or "",
        start=start,
        end=end,
        style=frame_style,
    )
    if not cues:
        return None
    return write_ass(
        work / f"{shot['shot_id']}.ass",
        cues,
        style=frame_style,
        position=resolve_position(shot, style),
        width=width,
        height=height,
    )


def _fmt(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
