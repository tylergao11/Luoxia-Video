"""Emotional TTS takes for 三十年河东 declaration (Xiao Yan)."""
from __future__ import annotations

import shutil
from pathlib import Path

from src.luoxia.env import load_env_once

load_env_once()

from src.audio.xai_tts import XaiTTS, apply_emotion  # noqa: E402
from src.luoxia.media.ffprobe import measure_media_duration_s  # noqa: E402

TEXT = (
    "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——"
    "三十年河东，三十年河西，莫欺少年穷！"
)

OUT_DIR = Path("output/doupo_moyan/audio")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Chinese emotion prose → xAI speech tags via apply_emotion keywords
VARIANTS = [
    # Prefer loud + emphasis + build-intensity; avoid "缓慢" keywords.
    ("atlas", "怒吼、咬牙切齿、一字一句、决绝、恨意、渐强"),
    ("leo", "大声、咬牙切齿、决绝、恨意、一字一句"),
    ("rex", "沉声、咬牙、决绝、渐强、恨意"),
    ("perseus", "怒吼、咬牙切齿、决绝、恨意"),
    ("sirius", "大声、咬牙切齿、一字一句、决绝、恨意"),
]


def main() -> None:
    tts = XaiTTS()
    results: list[tuple[str, str, float, list[str]]] = []

    for voice, emo in VARIANTS:
        tagged, applied = apply_emotion(TEXT, emo)
        print(f"voice={voice} tags={applied}")
        print(f"  tagged={tagged[:90]}")
        out = OUT_DIR / f"line_xai_{voice}.wav"
        for side in (out, Path(str(out) + ".sha256"), Path(str(out) + ".timings.json")):
            if side.is_file():
                side.unlink()
        try:
            path, measured, _digest = tts.synthesize_measured(
                text=TEXT,
                output_path=str(out),
                voice=voice,
                speech_rate=0.96,
                instructions=emo,
            )
            results.append((voice, path, measured, applied))
            print(f"  OK dur={measured:.2f}s -> {path}")
        except Exception as exc:
            print(f"  FAIL {exc}")

    # Hand-crafted tags (put tags in text; no instructions)
    hand = (
        "<loud><emphasis><build-intensity>"
        "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——"
        "三十年河东，三十年河西，[pause]莫欺少年穷！"
        "</build-intensity></emphasis></loud>"
    )
    out_h = OUT_DIR / "line_xai_hand.wav"
    for side in (out_h, Path(str(out_h) + ".sha256"), Path(str(out_h) + ".timings.json")):
        if side.is_file():
            side.unlink()
    try:
        path, measured, _ = tts.synthesize_measured(
            text=hand,
            output_path=str(out_h),
            voice="atlas",
            speech_rate=0.95,
            instructions=None,
        )
        results.append(("atlas_hand", path, measured, ["hand-tags"]))
        print(f"HAND OK dur={measured:.2f}s")
    except Exception as exc:
        print(f"HAND FAIL {exc}")

    if not results:
        raise SystemExit("no TTS takes succeeded")

    # Prefer atlas stacked-emotion take, else hand, else first
    pick = next((r for r in results if r[0] == "atlas"), None)
    if pick is None:
        pick = next((r for r in results if r[0] == "atlas_hand"), results[0])

    dest = OUT_DIR / "line.wav"
    shutil.copy2(pick[1], dest)
    meta = (
        f"provider=xai\n"
        f"primary_voice={pick[0]}\n"
        f"duration_s={pick[2]:.3f}\n"
        f"tags={pick[3]}\n"
        f"text={TEXT}\n"
        f"emotion=愤怒不甘决绝，先抑后扬，末四字加重\n"
        f"takes={','.join(r[0] for r in results)}\n"
        f"listen_all=output/doupo_moyan/audio/line_xai_*.wav\n"
    )
    (OUT_DIR / "line_meta.txt").write_text(meta, encoding="utf-8")
    Path("output/doupo_moyan/dialogue.txt").write_text(TEXT + "\n", encoding="utf-8")
    print("PRIMARY", dest, f"dur={pick[2]:.2f}s voice={pick[0]}")
    print("ALL", [(r[0], round(r[2], 2)) for r in results])


if __name__ == "__main__":
    main()
