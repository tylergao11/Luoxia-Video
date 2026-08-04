"""Regenerate Doupo stills with user Hongguo screenshots as style references.

Style is locked by feeding actual 红果 AI 漫 frames (edits API), not pure text.
"""
from __future__ import annotations

from pathlib import Path

from src.luoxia.env import load_env_once

load_env_once()

from src.models.xai_image import XaiImageModel  # noqa: E402

ROOT = Path("output/doupo_moyan")
REFS = ROOT / "refs"
CHARS = ROOT / "characters"
STILLS = ROOT / "stills"
for p in (REFS, CHARS, STILLS):
    p.mkdir(parents=True, exist_ok=True)

STYLE_PALACE = REFS / "style_ref_palace.jpg"
STYLE_OUTDOOR = REFS / "style_ref_outdoor.jpg"
STYLE_MALE = REFS / "style_ref_male_closeup.jpg"

STYLE_INSTR = (
    "严格匹配参考图的画面质感与制作语言：中国AI漫剧/红果短剧同款半写实3D角色渲染，"
    "精致美型脸、细腻皮肤、真实布料与发丝体积、电影体积光、浅景深、高端CG成片帧。"
    "只借鉴风格、光影、材质与人物精致度，不要复制参考图里的具体人物身份、发型颜色、"
    "服装图案、字幕、备案号或手机UI。禁止2D动漫、赛璐璐、厚线稿、丑脸、畸形五官、"
    "劣质塑料建模、真人抓拍。"
)
NEG = (
    "2d anime, cel shading, thick outlines, cartoon, chibi, pixar, disney, "
    "ugly deformed face, asymmetric eyes, extra fingers, low poly plastic toy, "
    "phone UI, watermark, subtitles, captions, logo, live action photo"
)

# Spoken line for later video/lip prompts (keep in one place).
DIALOGUE = (
    "纳兰小姐，看在纳兰老爷子的面上，萧炎奉劝你几句话——"
    "三十年河东，三十年河西，莫欺少年穷！"
)

model = XaiImageModel({"params": {"resolution": "2k"}})


def gen(prompt: str, out: Path, ar: str, refs: list[dict]) -> Path:
    if out.is_file():
        out.unlink()
    print(f"-> {out.name}  refs={[r['display_name'] for r in refs]}", flush=True)
    path, elapsed = model.generate(
        f"{STYLE_INSTR}\n{prompt}",
        str(out),
        aspect_ratio=ar,
        negative_prompt=NEG,
        ref_images=refs,
    )
    # API may change extension
    written = Path(path)
    if written != out and written.is_file():
        # keep expected stem in our tree for downstream
        target = out.with_suffix(written.suffix)
        if target != written:
            if target.is_file():
                target.unlink()
            written.replace(target)
            written = target
    print(f"   {written} ({elapsed:.1f}s)", flush=True)
    return written


def main() -> None:
    ref_male_style = {
        "display_name": "红果AI漫剧男角特写风格参考（精致半写实3D）",
        "path": str(STYLE_MALE),
    }
    ref_palace_style = {
        "display_name": "红果AI漫剧宫殿戏风格参考",
        "path": str(STYLE_PALACE),
    }
    ref_outdoor_style = {
        "display_name": "红果AI漫剧情绪戏风格参考",
        "path": str(STYLE_OUTDOOR),
    }

    # Character sheets: style-conditioned
    xiao_path = gen(
        "生成角色定妆照：少年萧炎（斗破苍穹男主），十五六岁，黑色微乱短发（不要银发白发），"
        "剑眉深目，清瘦下颌，深蓝旧武服有磨损，气质倔强隐忍。"
        "单人半身正面，中性表情，直视镜头，均匀柔光，纯灰色背景。"
        "精致度与参考图同级的红果AI漫剧男主质感，美型半写实3D。",
        CHARS / "xiao_yan.png",
        "9:16",
        [ref_male_style],
    )
    nalan_path = gen(
        "生成角色定妆照：少女纳兰嫣然，十六七岁，乌黑长发高挽，白底金纹云岚宗弟子服，"
        "清冷高傲美型。单人半身正面，中性表情，直视镜头，均匀柔光，纯灰色背景。"
        "精致度对齐红果AI漫剧女主。",
        CHARS / "nalan_yanran.png",
        "9:16",
        [ref_palace_style],
    )

    xiao = {"display_name": "萧炎（本片男主，锁定长相）", "path": str(xiao_path)}
    nalan = {"display_name": "纳兰嫣然（本片女主，锁定长相）", "path": str(nalan_path)}

    gen(
        "横屏16:9。萧家议事厅对峙中景：纳兰嫣然白衣金纹立于左侧高傲侧视，"
        "萧炎深蓝旧武服立于右侧咬牙含怒；背景族人窃笑，烛火体积光，强戏剧张力，"
        "红果AI漫剧成片构图，无字幕无文字。",
        STILLS / "s01_humiliation.png",
        "16:9",
        [ref_palace_style, nalan, xiao],
    )
    gen(
        "横屏16:9。特写：萧炎双手攥拳指节发白，深蓝武服袖口褶皱，压抑愤怒，"
        "侧光尘埃，红果AI漫剧质感，无字幕。",
        STILLS / "s02_fists.png",
        "16:9",
        [ref_outdoor_style, xiao],
    )
    # Mouth slightly open / speaking-ready frame for declaration (lip-sync friendly first frame)
    gen(
        "横屏16:9。中近景英雄位说话帧：萧炎抬头宣战，怒火与不甘，眉峰紧锁，"
        "嘴唇微张呈说话口型（即将说出台词），碎发被风掀起，深蓝旧武服，拳头紧握，"
        "议事厅逆光体积光，红果AI漫剧成片，无字幕无文字。"
        f"本镜将说出台词：「{DIALOGUE}」——画面是说话进行中的关键帧。",
        STILLS / "s03_declaration.png",
        "16:9",
        [ref_male_style, xiao],
    )
    gen(
        "横屏16:9。反应镜头：纳兰嫣然清冷脸上一丝震动，目光落向对面少年，"
        "白衣金纹，厅内烛火，红果AI漫剧成片，无字幕。",
        STILLS / "s04_aftermath.png",
        "16:9",
        [ref_palace_style, nalan],
    )

    (ROOT / "dialogue.txt").write_text(DIALOGUE + "\n", encoding="utf-8")
    print("DIALOGUE:", DIALOGUE)
    print("DONE")


if __name__ == "__main__":
    main()
