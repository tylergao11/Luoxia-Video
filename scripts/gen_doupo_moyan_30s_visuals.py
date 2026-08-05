"""Generate locked characters and six keyframes for the 30-second 退婚 proof."""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.luoxia.env import load_env_once

load_env_once()

from src.models.xai_image import XaiImageModel  # noqa: E402


ROOT = Path("output/doupo_moyan_30s")
CHAR_DIR = ROOT / "characters"
STILL_DIR = ROOT / "stills"

STYLE = (
    "成年向中国玄幻AI漫剧，精致半写实3D人物，虚幻引擎电影CG质感，"
    "锋利自然的东亚五官，细腻皮肤，真实发丝与布料，电影体积光，"
    "浅景深，暗金与冷蓝色调，强戏剧张力，横屏短剧成片，不是真人照片。"
)
NEGATIVE = (
    "2D anime, cel shading, flat illustration, cartoon, chibi, childish face, "
    "live action photo, plastic toy, low poly, deformed hands, extra fingers, "
    "text, watermark, logo, subtitles, captions, modern objects"
)


def render(
    model: XaiImageModel,
    *,
    output: Path,
    prompt: str,
    aspect_ratio: str,
    references: list[dict[str, str]] | None = None,
) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    written, elapsed = model.generate(
        f"{STYLE}\n{prompt}",
        str(output),
        aspect_ratio=aspect_ratio,
        negative_prompt=NEGATIVE,
        ref_images=references or [],
    )
    print(f"{output.stem}: {elapsed:.1f}s -> {written}")
    return Path(written).as_posix()


def main() -> None:
    model = XaiImageModel({"params": {"resolution": "1k"}})

    xiao_path = render(
        model,
        output=CHAR_DIR / "xiao_yan.png",
        aspect_ratio="9:16",
        prompt=(
            "原创角色定妆照：萧炎，十七岁中国玄幻少年，黑色微乱短发，剑眉深目，"
            "清瘦锋利下颌，深蓝旧武服带细密暗纹和轻微磨损，倔强隐忍。"
            "单人半身正面，中性表情，直视镜头，灰黑纯净背景，完整头发和肩膀。"
        ),
    )
    nalan_path = render(
        model,
        output=CHAR_DIR / "nalan_yanran.png",
        aspect_ratio="9:16",
        prompt=(
            "原创角色定妆照：纳兰嫣然，十七岁中国玄幻宗门天才少女，乌黑长发高挽，"
            "冷淡凤眼，白底金纹宗门长裙，纱质披帛与精致金饰，清冷高傲。"
            "单人半身正面，中性表情，直视镜头，灰黑纯净背景，完整头发和肩膀。"
        ),
    )

    xiao = {
        "display_name": "萧炎固定角色",
        "path": xiao_path,
        "role": "identity",
    }
    nalan = {
        "display_name": "纳兰嫣然固定角色",
        "path": nalan_path,
        "role": "identity",
    }

    shots = [
        {
            "id": "s01_nalan_break",
            "duration_s": 8,
            "references": [nalan, xiao],
            "prompt": (
                "萧家古典议事厅广角对峙，纳兰嫣然在画面左前方白衣金纹、冷眼宣布退婚，"
                "萧炎在右后方深蓝旧武服沉默站立，族人分列两侧，暖烛与冷天光对撞。"
                "镜头视觉中心是纳兰嫣然，人物比例正常，无文字。"
            ),
        },
        {
            "id": "s02_mock_and_fist",
            "duration_s": 4,
            "references": [xiao, nalan],
            "prompt": (
                "萧家议事厅侧面中景，背景两名年轻族人掩嘴讥笑，前景萧炎垂在身侧的手"
                "死死攥拳，指节发白、青筋微显，纳兰嫣然白衣身影在远处虚化。"
                "压迫感构图，浅景深，无文字。"
            ),
        },
        {
            "id": "s03_xiao_declare",
            "duration_s": 10,
            "references": [xiao],
            "prompt": (
                "萧炎胸像中近景英雄位，正面微侧抬头直视对手，黑眸压着屈辱和怒火，"
                "嘴唇微张正要说话，深蓝旧武服，一只拳头仍握紧，厅门逆光勾边，"
                "额前碎发被风掀起，背景族人虚化，无文字。"
            ),
        },
        {
            "id": "s04_nalan_react",
            "duration_s": 2,
            "references": [nalan],
            "prompt": (
                "纳兰嫣然面部近景反应镜头，保持清冷但瞳孔轻微收缩，傲慢表情第一次出现裂缝，"
                "白底金纹衣领和乌黑高挽长发清楚，摇曳烛火映在眼中，无文字。"
            ),
        },
        {
            "id": "s05_xiao_vow",
            "duration_s": 5,
            "references": [xiao],
            "prompt": (
                "萧炎低机位中近景立誓，少年挺直脊背，目光冷硬锁住对手，嘴唇微张正要说话，"
                "深蓝衣摆被穿堂风掀动，尘埃和烛火向后卷，逆光增强，气势从隐忍转为锋利，"
                "不挥舞手臂，无文字。"
            ),
        },
        {
            "id": "s06_aftermath",
            "duration_s": 1,
            "references": [xiao, nalan],
            "prompt": (
                "萧家议事厅超广角收尾，萧炎与纳兰嫣然隔着大厅对峙，所有族人安静下来，"
                "厅门外冷风卷入，烛火偏斜，少年背影挺直，史诗般静默余韵，无文字。"
            ),
        },
    ]

    rendered = []
    for shot in shots:
        path = render(
            model,
            output=STILL_DIR / f"{shot['id']}.png",
            aspect_ratio="16:9",
            prompt=shot["prompt"],
            references=shot["references"],
        )
        rendered.append(
            {
                "id": shot["id"],
                "duration_s": shot["duration_s"],
                "still_path": path,
                "prompt": shot["prompt"],
            }
        )

    manifest = {
        "image_provider": "xai",
        "image_model": model.model_name,
        "characters": {
            "xiao_yan": xiao_path,
            "nalan_yanran": nalan_path,
        },
        "shots": rendered,
    }
    (ROOT / "visual_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
