"""Comparable Doubao Seed-TTS 2.0 takes for Xiao Yan's declaration."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.luoxia.env import load_env_once

load_env_once()

from src.audio.doubao_tts import DoubaoTTS  # noqa: E402
from src.audio.performance import normalize_performance  # noqa: E402
from src.output_contract import OUTPUT  # noqa: E402

TEXT = (
    "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——"
    "三十年河东，三十年河西，莫欺少年穷！"
)

ROOT = OUTPUT.sample_dir("doupo_moyan")
OUT_DIR = ROOT / "audio"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PERFORMANCE = {
    "intent": "萧炎当面回击退婚，前半段克制有礼，压住屈辱和怒意，后半段逐步抬高，最后短促坚定地爆发",
    "segments": [
        {
            "text": "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——",
            "style": "lower-pitch",
            "event_before": None,
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
}

INSTRUCTIONS = (
    "这是萧炎当面回击退婚，不是旁白。前半段克制而有礼，压着被羞辱的怒意；"
    "从“三十年河东”开始逐步抬高，但不要逐字用力；“莫欺少年穷”才短促、坚定地爆发。"
    "保持二十岁少年的血性，不要播音腔、老成腔或预告片腔。"
)

# Same text, speed and direction; only the casting voice changes.
VARIANTS = {
    "qingcang": "zh_male_qingcang_uranus_bigtts",
    "ruyaqingnian": "zh_male_ruyaqingnian_uranus_bigtts",
    "bujiqingnian": "saturn_zh_male_bujiqingnian_tob",
}

PROMPT_VARIANTS = {
    "kouyu": (
        "萧炎只有二十岁，刚被人当众退婚。他不是在念名句，也没有预先排练。"
        "看着对方，用正常说话的音量开口，语气里压着火；“三十年河东，三十年河西”"
        "连贯说完，不要四字一顿；只在最后一句突然变硬、稍快一点。允许自然呼吸和口语感，"
        "不要播音腔、朗诵腔、预告片腔，也不要故意压低嗓音。"
    ),
    "renru": (
        "萧炎刚被当众羞辱，气到想发作，却为了爷爷强忍。开头礼貌但不服，声音略微发紧，"
        "不能从第一个字就演得很满；破折号后情绪像压不住一样自然往上走，不要逐词加重；"
        "最后一句盯着对方咬住说完，短、狠、有少年气，但不咆哮、不朗诵。"
    ),
    "mangju": (
        "国产AI漫剧的近景人物对白：年轻男主当场反击退婚。前半句稍快，句中少停顿；"
        "破折号处留半拍，后两句一口气推高；最后一句清晰、有冲击力、收尾干脆。"
        "不要旁白感、播音感、古装舞台腔，不要把每个词都当成重点。"
    ),
}

EXPLOSIVE_PROMPT_VARIANTS = {
    "last_hit": (
        "用热血短剧男主被当众羞辱后爆发的语气演绎下面这句话：前半段强压怒火，"
        "从“三十年河东”迅速渐强，停半拍，最后把“莫欺少年穷”突然提高音量、"
        "短促怒吼着砸出去。"
    ),
    "turn_rage": (
        "用年轻男主忍无可忍、当场翻脸的愤怒语气演绎下面这句话：开头冷硬，"
        "破折号后情绪持续上冲，“三十年河东，三十年河西”越说越狠，"
        "“莫欺少年穷”拔高音调全力爆发。"
    ),
    "climax": (
        "用国产热血AI漫剧高潮镜头的高燃语气演绎下面这句话：前半段压低蓄力，"
        "破折号后立刻进入高潮，把“三十年河东，三十年河西”一口气冲上去，"
        "停顿后用最大情绪怒声喊出“莫欺少年穷”。"
    ),
}

GRITTED_PROMPT_VARIANTS = {
    "physical": (
        "用咬牙切齿、强忍暴怒的语气连续演绎。牙关始终收紧，下颌紧绷，字像从齿缝里"
        "挤出来；前半句压着说，破折号后怒意迅速变重，最后“莫欺少年穷”咬得最紧、"
        "最狠，保持同一人的声线短促砸下。"
    ),
    "motive": (
        "你是二十岁的萧炎，刚被人当众退婚羞辱。你盯着对方，死死咬住后槽牙，不能让"
        "自己失控；这不是喊口号，而是把屈辱和杀气压在牙关里说给她听。说到最后一句时，"
        "忍住的怒火终于顶到极限，每个字都像威胁一样咬出去。"
    ),
}


def main() -> None:
    tts = DoubaoTTS()
    results: list[tuple[str, str, str, float]] = []
    plan = normalize_performance(TEXT, PERFORMANCE)
    if not plan or len(plan["segments"]) != len(PERFORMANCE["segments"]):
        raise SystemExit("invalid performance plan")

    for label, voice in VARIANTS.items():
        out = OUT_DIR / f"line_doubao_{label}.wav"
        print(f"voice={voice} plan={plan['segments']}")
        try:
            path, measured, _digest = tts.synthesize_measured(
                text=TEXT,
                output_path=str(out),
                voice=voice,
                speech_rate=1.0,
                instructions=INSTRUCTIONS,
                performance=PERFORMANCE,
            )
            results.append((label, voice, path, measured))
            print(f"  OK dur={measured:.2f}s -> {path}")
        except Exception as exc:
            print(f"  FAIL {exc}")

    if not results:
        raise SystemExit("no TTS takes succeeded")

    meta = (
        "provider=doubao\n"
        "model=seed-tts-2.0\n"
        f"text={TEXT}\n"
        f"instructions={INSTRUCTIONS}\n"
        f"takes={','.join(r[1] for r in results)}\n"
        "canonical_line_unchanged=true\n"
        f"listen_all={OUT_DIR / 'line_doubao_*.wav'}\n"
    )
    (OUT_DIR / "line_doubao_comparison.txt").write_text(meta, encoding="utf-8")
    (ROOT / "dialogue.txt").write_text(TEXT + "\n", encoding="utf-8")
    print("CANONICAL line.wav unchanged; choose after listening")
    print("ALL", [(r[0], r[1], round(r[3], 2)) for r in results])


if __name__ == "__main__":
    main()
