"""Assemble ~15s AI-manhua cut of 三十年河东 with TTS."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from src.utils.system_check import get_ffmpeg_path

ROOT = Path("output/doupo_moyan")
SESSION_VID = Path(
    r"C:\Users\84720\.Doggy\sessions"
    r"\C%3A%5CAi%5CLuoxia-Video"
    r"\019fcdb3-3327-76d2-804b-5d490c70c092\videos"
)

# Map: local name -> session file
CLIPS = {
    "s01_src.mp4": SESSION_VID / "2.mp4",  # humiliation
    "s02_src.mp4": SESSION_VID / "3.mp4",  # fists
    "s03_src.mp4": SESSION_VID / "4.mp4",  # declaration 10s
}


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{r.stderr[-800:]}")


def main() -> None:
    ff = get_ffmpeg_path()
    if not ff:
        raise SystemExit("ffmpeg not found")

    vdir = ROOT / "video"
    vdir.mkdir(parents=True, exist_ok=True)
    work = ROOT / "_compose"
    work.mkdir(parents=True, exist_ok=True)

    for name, src in CLIPS.items():
        dst = vdir / name
        shutil.copy2(src, dst)
        print("copied", dst)

    audio = ROOT / "audio" / "line.wav"
    still04 = ROOT / "stills" / "s04_aftermath.png"

    # Normalize all video pieces to 1280x720 25fps h264
    def to_seg(src: Path, out: Path, duration: float, ss: float = 0.0) -> None:
        run(
            [
                ff,
                "-y",
                "-ss",
                str(ss),
                "-t",
                str(duration),
                "-i",
                str(src),
                "-vf",
                "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=25",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-crf",
                "18",
                str(out),
            ]
        )

    def still_seg(img: Path, out: Path, duration: float) -> None:
        run(
            [
                ff,
                "-y",
                "-loop",
                "1",
                "-t",
                str(duration),
                "-i",
                str(img),
                "-vf",
                "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=25",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-crf",
                "18",
                str(out),
            ]
        )

    # Timeline (target ~14.7s):
    # 0.0-2.0  s01 humiliation
    # 2.0-3.2  s02 fists
    # 3.2-13.7 s03 declaration + full line (~10.45s)
    # 13.7-14.9 s04 aftermath freeze
    s01 = work / "s01.mp4"
    s02 = work / "s02.mp4"
    s03 = work / "s03.mp4"
    s04 = work / "s04.mp4"

    to_seg(vdir / "s01_src.mp4", s01, 2.0)
    to_seg(vdir / "s02_src.mp4", s02, 1.2)
    # pad/trim s03 to audio length
    audio_dur = 10.45
    # if source is 10s, pad last frame for remaining
    s03_raw = work / "s03_raw.mp4"
    to_seg(vdir / "s03_src.mp4", s03_raw, 10.0)
    pad = max(0.0, audio_dur - 10.0 + 0.15)  # tiny hold after line
    if pad > 0.05:
        # freeze last frame of s03_raw
        run(
            [
                ff,
                "-y",
                "-i",
                str(s03_raw),
                "-vf",
                f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-crf",
                "18",
                str(s03),
            ]
        )
    else:
        shutil.copy2(s03_raw, s03)

    still_seg(still04, s04, 1.2)

    concat_list = work / "concat.txt"
    parts = [s01, s02, s03, s04]
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts) + "\n",
        encoding="utf-8",
    )
    silent = work / "video_silent.mp4"
    run(
        [
            ff,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(silent),
        ]
    )

    # Mix: silence for first 3.2s then TTS
    # adelay in ms
    delay_ms = 3200
    final = ROOT / "final.mp4"
    run(
        [
            ff,
            "-y",
            "-i",
            str(silent),
            "-i",
            str(audio),
            "-filter_complex",
            f"[1:a]adelay={delay_ms}|{delay_ms},apad[a];[0:v]format=yuv420p[v]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final),
        ]
    )

    # probe duration
    probe = subprocess.run(
        [
            ff.replace("ffmpeg", "ffprobe") if "ffmpeg" in ff else "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(final),
        ],
        capture_output=True,
        text=True,
    )
    print("final", final)
    print("duration_s", probe.stdout.strip() or "?")


if __name__ == "__main__":
    main()
