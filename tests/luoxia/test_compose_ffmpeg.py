from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.luoxia.compose.assembler import assemble_episode
from src.luoxia.media.ffprobe import measure_media_duration_s, resolve_ffprobe_path
from src.luoxia.timeline.hashing import compute_timeline_hash
from src.luoxia.timeline.validator import validate_timeline
from src.utils.system_check import get_ffmpeg_path

FFMPEG = get_ffmpeg_path()

pytestmark = pytest.mark.skipif(
    not FFMPEG or not resolve_ffprobe_path(),
    reason="ffmpeg/ffprobe not installed; compose cannot be exercised",
)


def _make_video(path: Path, *, seconds: float, color: str) -> Path:
    subprocess.run(
        [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=1280x720:r=25:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return path


def _make_audio(path: Path, *, seconds: float) -> Path:
    subprocess.run(
        [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:a", "pcm_s16le", str(path),
        ],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return path


def _timeline(tmp_path: Path, *, transitions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Three shots: silent establishing, dialogue, dialogue. Sources are synthetic."""
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)

    # request_duration_s > target_duration_s, so real slack frames exist for a dissolve.
    specs = [
        {"shot_id": "s1", "target": 3.0, "request": 4, "measured": None, "text": None},
        {"shot_id": "s2", "target": 2.2, "request": 3, "measured": 1.4, "text": "你怎么在这？"},
        {
            "shot_id": "s3",
            "target": 4.8,
            "request": 5,
            "measured": 4.0,
            "text": "我等了三年。每一天都在想，如果那天我没有走，现在会不会不一样。",
        },
    ]

    shots: List[Dict[str, Any]] = []
    cursor = 0.0
    for i, spec in enumerate(specs):
        target = float(spec["target"])
        slack = spec["request"] - target
        video = _make_video(media / f"{spec['shot_id']}.mp4", seconds=spec["request"], color="navy" if i % 2 else "maroon")

        shot: Dict[str, Any] = {
            "shot_id": spec["shot_id"],
            "index": i,
            "type": "dialogue" if spec["measured"] else "transition",
            "timing_driver": "audio" if spec["measured"] else "rhythm",
            "characters": ["lin_wan"] if spec["measured"] else [],
            "timing": {
                "lead_in_s": 0.3,
                "tail_out_s": 0.5,
                "target_duration_s": target,
                "request_duration_s": spec["request"],
                "slack_s": slack,
                "trim": {"strategy": "tail", "head_s": 0.0, "tail_s": slack},
                "start_s": cursor,
                "end_s": cursor + target,
            },
            "video": {
                "status": "done",
                "provider": "xai",
                "model": "grok-imagine-video-1.5",
                "local_path": str(video),
                "has_audio_track": False,
                "audio_stripped": False,
            },
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {"text": spec["text"]},
            "transition": transitions[i],
        }
        if spec["measured"]:
            audio = _make_audio(media / f"{spec['shot_id']}.wav", seconds=spec["measured"])
            shot["dialogue"] = {"character_id": "lin_wan", "text": spec["text"], "rewrite_count": 0}
            shot["audio"] = {
                "status": "rendered",
                "provider": "dashscope.cosyvoice",
                "voice_id": "longxiaochun",
                "speed": 1.0,
                "measured_duration_s": spec["measured"],
                "local_path": str(audio),
            }
            shot["subtitle"]["start_s"] = cursor + 0.3
            shot["subtitle"]["end_s"] = cursor + 0.3 + float(spec["measured"])
        cursor += target
        shots.append(shot)

    timeline: Dict[str, Any] = {
        "schema_version": "1.1.0",
        "episode_id": "ffmpeg_probe",
        "phase": "frozen",
        "global": {
            "fps": 25,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "lead_in_s": 0.3,
            "tail_out_s": 0.5,
            "min_speed_ratio": 0.92,
            "max_speed_ratio": 1.1,
            "default_action_duration_s": 4,
        },
        "cast": [{"character_id": "lin_wan", "display_name": "林晚", "voice_id": "longxiaochun"}],
        "shots": shots,
    }
    timeline["timeline_hash"] = compute_timeline_hash(timeline)
    timeline["frozen_at"] = "2026-08-04T12:00:00+08:00"
    return timeline


def _expected_total(timeline: Dict[str, Any]) -> float:
    return sum(float(s["timing"]["target_duration_s"]) for s in timeline["shots"])


CUT = {"kind": "cut", "duration_s": 0.0}


@pytest.mark.parametrize(
    "name,transitions",
    [
        ("all_cuts", [CUT, CUT, CUT]),
        (
            "fades",
            [
                {"kind": "fade_black", "duration_s": 0.3},
                {"kind": "fade_white", "duration_s": 0.3},
                {"kind": "fade_black", "duration_s": 0.5},
            ],
        ),
        (
            "dissolves",
            [
                {"kind": "dissolve", "duration_s": 0.3},
                {"kind": "dissolve", "duration_s": 0.3},
                CUT,
            ],
        ),
        (
            "mixed",
            [
                {"kind": "dissolve", "duration_s": 0.3},
                {"kind": "fade_black", "duration_s": 0.3},
                CUT,
            ],
        ),
    ],
)
def test_compose_preserves_master_clock(tmp_path, name, transitions):
    """The rendered episode must be exactly as long as the timeline says."""
    timeline = _timeline(tmp_path, transitions=transitions)
    assert validate_timeline(timeline, raise_on_error=False) == []

    out = tmp_path / f"{name}.mp4"
    assemble_episode(timeline, output_path=out, work_dir=tmp_path / "_compose")

    assert out.is_file()
    # One frame of tolerance at 25fps; container rounding is unavoidable.
    assert measure_media_duration_s(out) == pytest.approx(_expected_total(timeline), abs=0.08)
    assert timeline["phase"] == "rendered"


def test_dissolve_survives_a_clip_with_no_slack(tmp_path):
    """A provider clip exactly as long as target still dissolves, by holding its last frame."""
    timeline = _timeline(
        tmp_path,
        transitions=[{"kind": "dissolve", "duration_s": 0.3}, CUT, CUT],
    )
    shot = timeline["shots"][0]
    tight = _make_video(tmp_path / "media" / "tight.mp4", seconds=shot["timing"]["target_duration_s"], color="black")
    shot["video"]["local_path"] = str(tight)

    out = tmp_path / "tight.mp4"
    assemble_episode(timeline, output_path=out, work_dir=tmp_path / "_compose_tight")
    assert measure_media_duration_s(out) == pytest.approx(_expected_total(timeline), abs=0.08)


def test_subtitles_are_burned_in(tmp_path):
    """The subtitle filter must actually run: compare a frame against a no-subtitle render."""
    timeline = _timeline(tmp_path, transitions=[CUT, CUT, CUT])
    with_subs = tmp_path / "subs.mp4"
    assemble_episode(timeline, output_path=with_subs, work_dir=tmp_path / "_c1")

    stripped = _timeline(tmp_path, transitions=[CUT, CUT, CUT])
    for shot in stripped["shots"]:
        shot["subtitle"] = {"text": None}
    stripped["timeline_hash"] = compute_timeline_hash(stripped)
    without_subs = tmp_path / "nosubs.mp4"
    assemble_episode(stripped, output_path=without_subs, work_dir=tmp_path / "_c2")

    # Sample a frame from the middle of the second shot, where a line is on screen.
    a = _frame_bytes(with_subs, at=4.0, path=tmp_path / "a.png")
    b = _frame_bytes(without_subs, at=4.0, path=tmp_path / "b.png")
    assert a != b, "subtitle burn-in produced an identical frame"


def _frame_bytes(video: Path, *, at: float, path: Path) -> bytes:
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return path.read_bytes()
