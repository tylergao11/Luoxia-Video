"""Assemble the 30-second audio-first proof with subtitles and sound design."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.system_check import get_ffmpeg_path
from src.output_contract import OUTPUT


ROOT = OUTPUT.sample_dir("doupo_moyan_30s")
FINAL = ROOT / "final_30s.mp4"
ASS_PATH = ROOT / "subtitles.ass"
SCENE_MANIFEST = ROOT / "scene_manifest.json"

SHOT_IDS = [
    "s01_nalan_break",
    "s02_mock_and_fist",
    "s03_xiao_declare",
    "s04_nalan_react",
    "s05_xiao_vow",
]
SHOT_DURATIONS = [8.0, 4.0, 10.0, 2.0, 5.0]
END_STILL_DURATION = 1.0

AUDIO_STARTS = {
    "nalan_break": 1.35,
    "clan_mock": 8.25,
    "xiao_declare": 12.20,
    "xiao_vow": 24.30,
}


def ass_time(seconds: float) -> str:
    value = max(0.0, seconds)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    remainder = value % 60
    return f"{hours}:{minutes:02d}:{remainder:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def timing_for(path: Path) -> dict[str, Any] | None:
    timing_path = Path(str(path) + ".timings.json")
    if not timing_path.is_file():
        return None
    return json.loads(timing_path.read_text(encoding="utf-8"))


def subtitle_events(take: dict[str, Any], start_s: float) -> list[tuple[float, float, str]]:
    text = take["text"]
    duration_s = float(take["duration_s"])
    split_index = 0
    if take["id"] == "nalan_break":
        split_index = text.find("\u3002") + 1
    elif take["id"] == "xiao_declare":
        marker = text.find("\u2014\u2014")
        split_index = marker + 2 if marker >= 0 else 0

    if split_index <= 0 or split_index >= len(text):
        return [(start_s, start_s + duration_s, text)]

    timing = timing_for(Path(take["path"]))
    graph_times = (timing or {}).get("graph_times") or []
    if len(graph_times) < len(text):
        split_s = duration_s * split_index / len(text)
    else:
        split_s = float(graph_times[split_index - 1][1])
    split_s = max(0.35, min(duration_s - 0.35, split_s))
    return [
        (start_s, start_s + split_s, text[:split_index]),
        (start_s + split_s, start_s + duration_s, text[split_index:]),
    ]


def write_subtitles(audio_takes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Microsoft YaHei,42,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,1,0,1,3,1,2,80,80,42,1
Style: Title,Microsoft YaHei,68,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,4,0,1,4,2,5,80,80,40,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [header]
    timeline: list[dict[str, Any]] = []
    for take in audio_takes:
        start_s = AUDIO_STARTS[take["id"]]
        timeline.append({**take, "start_s": start_s, "end_s": start_s + take["duration_s"]})
        for event_start, event_end, text in subtitle_events(take, start_s):
            lines.append(
                "Dialogue: 0,"
                f"{ass_time(event_start)},{ass_time(event_end)},Default,,0,0,0,,"
                f"{ass_escape(text)}\n"
            )
    title = "\u4e09\u5e74\u4e4b\u7ea6"
    lines.append(
        f"Dialogue: 1,{ass_time(29.0)},{ass_time(30.0)},Title,,0,0,0,,"
        f"{{\\fad(120,160)}}{title}\n"
    )
    ASS_PATH.write_text("".join(lines), encoding="utf-8", newline="\n")
    return timeline


def main() -> None:
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")

    video_manifest = json.loads((ROOT / "video_manifest.json").read_text(encoding="utf-8"))
    video_by_id = {item["id"]: item for item in video_manifest["shots"]}
    audio_manifest = json.loads((ROOT / "audio" / "manifest.json").read_text(encoding="utf-8"))
    audio_takes = audio_manifest["takes"]
    audio_timeline = write_subtitles(audio_takes)

    command = [ffmpeg, "-y", "-v", "warning"]
    for shot_id in SHOT_IDS:
        command += ["-i", str(Path(video_by_id[shot_id]["video_path"]).resolve())]
    command += [
        "-loop", "1", "-framerate", "24", "-t", str(END_STILL_DURATION),
        "-i", str((ROOT / "stills" / "s06_aftermath.jpg").resolve()),
    ]
    for take in audio_takes:
        command += ["-i", str(Path(take["path"]).resolve())]
    command += [
        "-f", "lavfi", "-t", "30", "-i", "sine=frequency=55:sample_rate=48000",
        "-f", "lavfi", "-t", "30", "-i", "anoisesrc=color=pink:amplitude=0.02:sample_rate=48000",
        "-f", "lavfi", "-t", "0.45", "-i", "sine=frequency=64:sample_rate=48000",
        "-f", "lavfi", "-t", "0.40", "-i", "sine=frequency=48:sample_rate=48000",
        "-f", "lavfi", "-t", "0.55", "-i", "sine=frequency=58:sample_rate=48000",
        "-f", "lavfi", "-t", "0.65", "-i", "sine=frequency=46:sample_rate=48000",
    ]

    video_filters = []
    for index, duration_s in enumerate(SHOT_DURATIONS):
        video_filters.append(
            f"[{index}:v]trim=duration={duration_s:.3f},setpts=PTS-STARTPTS,"
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"setsar=1,fps=24[v{index}]"
        )
    video_filters.append(
        "[5:v]trim=duration=1.000,setpts=PTS-STARTPTS,"
        "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "setsar=1,fps=24[v5]"
    )
    video_filters.append("[v0][v1][v2][v3][v4][v5]concat=n=6:v=1:a=0[joined]")
    video_filters.append("[joined]subtitles=subtitles.ass:charenc=UTF-8[vout]")

    delay_ms = {key: round(value * 1000) for key, value in AUDIO_STARTS.items()}
    audio_filters = [
        f"[6:a]aresample=48000,loudnorm=I=-18:TP=-2:LRA=8,adelay={delay_ms['nalan_break']}:all=1[d0]",
        f"[7:a]aresample=48000,loudnorm=I=-22:TP=-3:LRA=6,volume=0.62,adelay={delay_ms['clan_mock']}:all=1[d1]",
        f"[8:a]aresample=48000,loudnorm=I=-17:TP=-1.5:LRA=9,volume=1.04,adelay={delay_ms['xiao_declare']}:all=1[d2]",
        f"[9:a]aresample=48000,loudnorm=I=-17:TP=-1.5:LRA=8,volume=1.04,adelay={delay_ms['xiao_vow']}:all=1[d3]",
        "[10:a]lowpass=f=180,volume=0.055,afade=t=in:st=0:d=2,afade=t=out:st=28:d=2[drone]",
        "[11:a]highpass=f=250,lowpass=f=2200,volume=0.32,afade=t=in:st=0:d=1,afade=t=out:st=29:d=1[air]",
        "[12:a]afade=t=out:st=0:d=0.45,volume=0.24,adelay=7100:all=1[h1]",
        "[13:a]afade=t=out:st=0:d=0.40,volume=0.30,adelay=11350:all=1[h2]",
        "[14:a]afade=t=out:st=0:d=0.55,volume=0.30,adelay=21750:all=1[h3]",
        "[15:a]afade=t=out:st=0:d=0.65,volume=0.36,adelay=28380:all=1[h4]",
        "[d0][d1][d2][d3][drone][air][h1][h2][h3][h4]"
        "amix=inputs=10:normalize=0:duration=longest,alimiter=limit=0.95,"
        "pan=stereo|c0=c0|c1=c0,atrim=duration=30[aout]",
    ]
    command += [
        "-filter_complex", ";".join(video_filters + audio_filters),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", "30", "-movflags", "+faststart", str(FINAL),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:])

    manifest = {
        "duration_s": 30.0,
        "resolution": "1280x720",
        "fps": 24,
        "shots": [
            {"id": shot_id, "duration_s": duration_s, "path": video_by_id[shot_id]["video_path"]}
            for shot_id, duration_s in zip(SHOT_IDS, SHOT_DURATIONS)
        ]
        + [{"id": "s06_aftermath", "duration_s": 1.0, "path": str(ROOT / "stills" / "s06_aftermath.jpg")}],
        "audio": audio_timeline,
        "subtitles": ASS_PATH.as_posix(),
        "final": FINAL.as_posix(),
    }
    SCENE_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(FINAL)


if __name__ == "__main__":
    main()
