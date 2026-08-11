"""Generate the audio-first dialogue track for the 30-second 退婚 proof."""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.luoxia.env import load_env_once

load_env_once()

from src.audio.doubao_tts import DoubaoTTS  # noqa: E402
from src.output_contract import OUTPUT  # noqa: E402


OUT_DIR = OUTPUT.sample_dir("doupo_moyan_30s") / "audio"
TAKE_ID = "doupo-moyan-30s-v1"

TAKES = [
    {
        "id": "nalan_break",
        "role": "纳兰嫣然",
        "text": "萧炎，你我早已不是一个世界的人。这婚约，今日作废。",
        "voice": "zh_female_gaolengyujie_uranus_bigtts",
        "instructions": (
            "十六七岁的宗门天才少女，当众退婚。声音清冷克制、居高临下，不喊叫，"
            "不播音。前一句平静划清界限；‘今日作废’稍慢、冷硬、斩钉截铁。"
        ),
        "performance": {
            "intent": "清冷高傲地当众宣布退婚",
            "segments": [
                {
                    "text": "萧炎，你我早已不是一个世界的人。这婚约，今日作废。",
                    "style": "lower-pitch",
                    "event_before": None,
                }
            ],
        },
    },
    {
        "id": "clan_mock",
        "role": "萧家族人",
        "text": "一个废物，也配得上云岚宗？",
        "voice": "saturn_zh_male_aomanshaoye_tob",
        "instructions": (
            "议事厅里年轻族人的低声讥笑，压低音量，带轻蔑和幸灾乐祸，"
            "像对身边人耳语，不要正面对观众播报。"
        ),
        "performance": {
            "intent": "族人低声轻蔑嘲笑",
            "segments": [
                {
                    "text": "一个废物，也配得上云岚宗？",
                    "style": "whisper",
                    "event_before": "chuckle",
                }
            ],
        },
    },
    {
        "id": "xiao_declare",
        "role": "萧炎",
        "text": (
            "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——"
            "三十年河东，三十年河西，莫欺少年穷！"
        ),
        "voice": "zh_male_qingcang_uranus_bigtts",
        "instructions": (
            "十七岁的少年刚被当众退婚。不是旁白和朗诵：开头压着屈辱，礼貌但冷硬；"
            "破折号后连贯渐强；只有‘莫欺少年穷’短促爆发，带少年血性，不要老成播音腔。"
        ),
        "performance": {
            "intent": "少年从强忍屈辱到当场反击",
            "segments": [
                {
                    "text": "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——",
                    "style": "lower-pitch",
                    "event_before": "breath",
                },
                {
                    "text": "三十年河东，三十年河西，",
                    "style": "build-intensity",
                    "event_before": None,
                },
                {
                    "text": "莫欺少年穷！",
                    "style": "emphasis",
                    "event_before": "pause",
                },
            ],
        },
    },
    {
        "id": "xiao_vow",
        "role": "萧炎",
        "text": "今日之辱，三年之后，我萧炎亲自奉还！",
        "voice": "zh_male_qingcang_uranus_bigtts",
        "instructions": (
            "同一个十七岁少年。怒火已经收成决心，盯住对方，一字一句立誓；"
            "‘三年之后’压低停稳，‘亲自奉还’突然变硬并干脆收尾。不要旁白腔。"
        ),
        "performance": {
            "intent": "少年冷硬立下三年之约",
            "segments": [
                {
                    "text": "今日之辱，",
                    "style": "lower-pitch",
                    "event_before": "breath",
                },
                {
                    "text": "三年之后，",
                    "style": "emphasis",
                    "event_before": "pause",
                },
                {
                    "text": "我萧炎亲自奉还！",
                    "style": "build-intensity",
                    "event_before": None,
                },
            ],
        },
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tts = DoubaoTTS()
    rendered = []
    for take in TAKES:
        output = OUT_DIR / f"{take['id']}.wav"
        path, duration_s, digest = tts.synthesize_measured(
            text=take["text"],
            output_path=str(output),
            voice=take["voice"],
            speech_rate=1.0,
            instructions=take["instructions"],
            performance=take["performance"],
            take_id=TAKE_ID,
        )
        item = {
            "id": take["id"],
            "role": take["role"],
            "text": take["text"],
            "voice": take["voice"],
            "path": Path(path).as_posix(),
            "duration_s": round(duration_s, 3),
            "content_sha256": digest,
        }
        rendered.append(item)
        print(f"{item['id']}: {item['duration_s']:.3f}s -> {item['path']}")

    manifest = {
        "provider": "doubao",
        "model": "seed-tts-2.0",
        "take_id": TAKE_ID,
        "takes": rendered,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
